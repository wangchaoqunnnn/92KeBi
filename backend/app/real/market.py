"""实时实盘行情管理器（多数据源：新浪 + 腾讯 备份，低延迟择优与自动切换）：
- 快速轮询: 默认腾讯 qt / 新浪 hq 批量实时(块级并发, 数百毫秒级), 失败自动切换;
- 全量同步: 每 REAL_FULL_EVERY 次或交易日变更时用新浪行情中心重抓成分(约3~5s), 并存成员快照;
- 涨停/跌停按涨跌幅限制规则判定, 连板用日K倒推(新浪主/腾讯备);
- 行业(新浪49)成员映射按日缓存; 昨日涨停自动存档供“昨涨停今表现”;
- 状态: 数据源名/延迟/健康可在 /api/meta、/api/market/live、/api/admin/status 观察。
"""
import json
import logging
import os
import re
import threading
import time
from datetime import date

from .. import db
from ..config import DATA_DIR, REAL_FULL_EVERY
from ..providers import router, sina, tencent
log = logging.getLogger("kb.real")

_lock = threading.Lock()
_state = {
    "quotes": {}, "quotes_list": [], "ts": 0.0, "quote_date": "",
    "today_zt": [], "today_dt": [], "today_ladder": {}, "ladder_date": "",
    "yesterday_zt": [], "premium_end": None, "premium_open": None, "explosion": 0.0,
    "industry_stats": {}, "mkt_stats": {}, "last_error": None, "state": "init",
    "src": None, "latency_ms": None, "full_ticks": 0, "full_ok_ts": 0,
    "industry_data": None,
}

INDUSTRY_FILE = os.path.join(DATA_DIR, "real_industries.json")
MEMBERS_FILE = os.path.join(DATA_DIR, "real_members.json")

POLICY_KEYWORDS = [
    "算力", "AI", "人工智能", "芯片", "半导体", "光刻", "机器人", "具身",
    "低空", "eVTOL", "航天", "卫星", "商业航天", "数据要素", "信创", "数据安全",
    "创新药", "生物", "固态电池", "锂", "钠", "新能源", "储能", "光伏", "军工",
    "鸿蒙", "华为", "国产", "脑机", "量子", "6G", "液冷", "光模块",
]


def ensure_industry_cache(force=False):
    """新浪行业成员映射(49行业), 缓存至当日json"""
    if os.path.exists(INDUSTRY_FILE):
        try:
            with open(INDUSTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not force and data.get("date") == date.today().isoformat():
                return data
        except Exception:
            pass
    tree = sina.fetch_node_tree()
    industries = tree["industries"]
    ind_map = sina.fetch_industry_map(industries, threads=10)
    code2ind = {}
    for ind, codes in ind_map.items():
        for c in codes:
            code2ind[c] = code2ind.get(c, ind)
    data = {"date": date.today().isoformat(), "industries": ind_map, "code2industry": code2ind}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INDUSTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log.warning("save industry cache: %s", e)
    log.info("industry map refreshed: %d industries, %d stocks", len(ind_map), len(code2ind))
    return data


def get_industry_cache(force=False):
    with _lock:
        if _state.get("industry_data"):
            return _state["industry_data"]
    data = ensure_industry_cache(force)
    with _lock:
        _state["industry_data"] = data
    return data


def industry_of(code):
    return get_industry_cache().get("code2industry", {}).get(code, "")


def policy_hard(code, name):
    text = (industry_of(code) or "") + " " + (name or "")
    return any(k in text for k in POLICY_KEYWORDS)


# ---------------------------------------------------------------- 成员快照
def _save_members(rows):
    data = {"date": date.today().isoformat(),
            "rows": [{"code": r["code"], "symbol": r.get("symbol", sina.to_symbol(r["code"])),
                      "name": r["name"]} for r in rows]}
    try:
        with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log.warning("save members: %s", e)


def _load_members():
    try:
        with open(MEMBERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("rows", [])
    except Exception:
        return []


# ---------------------------------------------------------------- 昨日涨停存档
def _load_zt_prev():
    try:
        v = db.meta_get("real_zt_prev")
    except Exception:
        v = None
    if v:
        try:
            d = json.loads(v)
            return d.get("codes", []), d.get("date", "")
        except Exception:
            pass
    return [], ""


def _save_zt_prev(codes, d):
    db.meta_set("real_zt_prev", json.dumps({"codes": codes, "date": d}, ensure_ascii=False))


def _ladder_for_zt(zt_rows):
    """当日涨停股倒推连板数(基于日K, 新浪主/腾讯备)"""
    if not zt_rows:
        return {}
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def work(code, name):
        try:
            rows = router.fetch_kline_any(code, n=40)
            if not rows:
                return code, 1
            rows = [r for r in rows if r["day"] < _state["quote_date"]]
            if not rows:
                return code, 1
            rate = sina.limit_rate(code, name)
            streak = 0
            for i in range(len(rows) - 1, -1, -1):
                r = rows[i]
                pre = rows[i - 1]["close"] if i > 0 else None
                if pre and pre > 0 and sina.is_limit_up(r["close"], pre, rate):
                    streak += 1
                else:
                    break
            return code, streak + 1
        except Exception as e:
            log.warning("ladder %s: %s", code, e)
            return code, 1

    done = {}
    with ThreadPoolExecutor(min(12, len(zt_rows) or 1)) as ex:
        futs = [ex.submit(work, r["code"], r["name"]) for r in zt_rows]
        for fu in as_completed(futs):
            try:
                c, s = fu.result()
                done[c] = s
            except Exception:
                pass
    return done


# ---------------------------------------------------------------- 行情获取
def _make_quote(code, name, symbol, price, settle, op, high, low, volume, amount,
                turnover=None, nmc=None, mktcap=None, per=None, pb=None):
    rate = sina.limit_rate(code, name)
    pct = None
    zt = dt = False
    if price and price > 0 and settle and settle > 0:
        pct = round((price / settle - 1) * 100, 2)
        if not sina.is_new_listing(code, name):
            zt = sina.is_limit_up(price, settle, rate)
            dt = sina.is_limit_down(price, settle, rate)
    return {
        "code": code, "name": name, "symbol": symbol,
        "price": price, "settlement": settle, "pct": pct,
        "open": op, "high": high, "low": low,
        "volume": volume, "amount": amount, "turnover": turnover,
        "mktcap": mktcap, "nmc": nmc, "per": per, "pb": pb,
        "ticktime": "", "rate": rate, "zt": zt, "dt": dt,
    }


def _acq_full():
    """新浪行情中心全量(成分+行情, 约3~5s)。返回 (quotes, source, latency_ms)"""
    t0 = time.time()
    rows = sina.fetch_members_fast("hs_a", threads=10)
    quotes = {}
    for r in rows:
        code = r["code"]
        if not code:
            continue
        quotes[code] = _make_quote(code, r["name"], r.get("symbol", sina.to_symbol(code)),
                                   r.get("trade"), r.get("settlement"), r.get("open"),
                                   r.get("high"), r.get("low"), r.get("volume"),
                                   r.get("amount"), turnover=r.get("turnoverratio"),
                                   nmc=r.get("nmc"), mktcap=r.get("mktcap"),
                                   per=r.get("per"), pb=r.get("pb"))
        if quotes[code]["pct"] is None and r.get("changepercent") is not None:
            quotes[code]["pct"] = r["changepercent"]
    _save_members(rows)
    return quotes, "sina_market", (time.time() - t0) * 1000


def _acq_fast():
    """腾讯/新浪hq 快速批量(数百毫秒), 成员来自最近全量快照缓存"""
    members = _load_members()
    if not members:
        return None, None, None, "无成员快照(先执行一次全量同步)"
    symbols = [m["symbol"] for m in members if m.get("symbol")]
    t0 = time.time()
    src, qmap = router.fetch_fast_quotes(symbols)
    ms = (time.time() - t0) * 1000
    if not src or not qmap:
        return None, None, None, "双源快速行情均失败"
    quotes = {}
    for m in members:
        q = qmap.get(m["code"])
        if not q:
            continue
        quotes[m["code"]] = _make_quote(m["code"], m["name"], m["symbol"],
                                        q.get("price"), q.get("pre_close"), q.get("open"),
                                        q.get("high"), q.get("low"), q.get("volume"),
                                        q.get("amount"), turnover=q.get("turnover"))
    return quotes, src, ms, None


# ---------------------------------------------------------------- 主刷新
def refresh_quotes():
    """交易时段快速双源轮询; 需要全量时(启动/每REAL_FULL_EVERY次/日期切换)走新浪全量。"""
    db.init_db()
    with _lock:
        need_full = (not _state["quotes"]) or _state["full_ticks"] >= REAL_FULL_EVERY or _state["state"] != "ok"
    if need_full:
        try:
            quotes, src, ms = _acq_full()
            router._record("sina_market", ms, ok=True)
            _store_refresh(quotes, src, ms, full=True)
            return True
        except Exception as e:
            log.error("full market fetch fail: %s", e)
            _state["last_error"] = f"全量源失败: {e}"
            try:
                router._record("sina_market", 0, ok=False)
            except Exception:
                pass
            # 降级: 尝试快速双源
    quotes, src, ms, err = _acq_fast()
    if quotes:
        _store_refresh(quotes, src, ms, full=False)
        return True
    _state["last_error"] = f"快速双源失败: {err}"
    return False


def _store_refresh(quotes, src, ms, full):
    with _lock:
        today_zt = [{"code": c, "name": q["name"]} for c, q in quotes.items() if q["zt"]]
        today_dt = [{"code": c, "name": q["name"]} for c, q in quotes.items() if q["dt"]]
        # 行情日期: 从任意样例ticktime推导(腾讯), 否则本地交易日
        quote_date = _state.get("quote_date") or date.today().strftime("%Y-%m-%d")
        try:
            t = next((q["ticktime"] for q in quotes.values() if q.get("ticktime")), "")
            m = re.search(r"\d{4}-\d{2}-\d{2}", t) or re.search(r"\d{8}", t)
            if m:
                s = m.group(0)
                quote_date = f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s
        except Exception:
            pass
        prev_date = _state.get("quote_date") or ""
        _state["quotes"] = quotes
        _state["quote_date"] = quote_date
        _state["ts"] = time.time()
        _state["src"] = src
        _state["latency_ms"] = round(ms, 1) if ms else None
        _state["full_ticks"] = 0 if full else (_state.get("full_ticks", 0) + 1)
        _state["state"] = "ok"
        _state["last_error"] = None

        if prev_date and prev_date != quote_date:
            _save_zt_prev([x["code"] for x in _state["today_zt"]], prev_date)
            _state["yesterday_zt"] = [x["code"] for x in _state["today_zt"]]
        elif not _state.get("yesterday_zt"):
            yzt, yd = _load_zt_prev()
            _state["yesterday_zt"] = yzt if yd == quote_date else []

        # 连板(仅当日涨停股; 日期变更重算, 同日增量补拉新封板股)
        if _state.get("ladder_date") != quote_date:
            _state["today_ladder"] = _ladder_for_zt(today_zt)
            _state["ladder_date"] = quote_date
        else:
            missing = [r for r in today_zt if r["code"] not in _state["today_ladder"]]
            if missing:
                _state["today_ladder"].update(_ladder_for_zt(missing))
        _state["today_zt"] = today_zt
        _state["today_dt"] = today_dt

        # 溢价率(昨涨停今表现) + 炸板近似 + 大盘卡
        yzt = _state["yesterday_zt"]
        if yzt:
            pcts = [q["pct"] for c, q in quotes.items() if c in yzt and q["pct"] is not None]
            opens = []
            for c in yzt:
                qq = quotes.get(c)
                if qq and qq.get("open") and qq.get("settlement"):
                    opens.append((qq["open"] / qq["settlement"] - 1) * 100)
            _state["premium_end"] = round(sum(pcts) / len(pcts), 2) if pcts else None
            _state["premium_open"] = round(sum(opens) / len(opens), 2) if opens else None
        else:
            _state["premium_end"] = None
            _state["premium_open"] = None
        attempts = exploded = 0
        for qq in quotes.values():
            if qq["zt"] or qq["dt"] or sina.is_new_listing(qq["code"], qq["name"]):
                continue
            if qq["settlement"] and qq["high"] and qq["high"] >= round(qq["settlement"] * (1 + qq["rate"]), 2) - 1e-6:
                attempts += 1
                exploded += 1
        _state["explosion"] = round(exploded / attempts * 100, 1) if attempts else 0.0
        up = sum(1 for qq in quotes.values() if (qq["pct"] or 0) > 0)
        down = sum(1 for qq in quotes.values() if (qq["pct"] or 0) < 0)
        amt = sum((qq["amount"] or 0) for qq in quotes.values()) / 1e8
        bj = sum(1 for qq in quotes.values() if qq["code"].startswith(("4", "8", "92")))
        ladder_counts = {}
        for stk in _state["today_ladder"].values():
            ladder_counts[stk] = ladder_counts.get(stk, 0) + 1
        _state["mkt_stats"] = {
            "universe": len(quotes), "bj": bj,
            "zt": len(today_zt), "dt": len(today_dt),
            "up": up - bj, "down": down, "amount_yi": round(amt, 1),
            "quote_date": quote_date, "explosion": _state["explosion"],
            "premium_end": _state["premium_end"], "premium_open": _state["premium_open"],
            "max_streak": max(_state["today_ladder"].values()) if _state["today_ladder"] else 0,
            "ladder": ladder_counts,
            "src": src,
        }


def industry_stats_full():
    """按行业聚合全市场实时(用行业映射), 供大盘板块榜"""
    ind = get_industry_cache()
    c2i = ind.get("code2industry", {})
    ag = {}
    for c, qq in _state["quotes"].items():
        ind_name = c2i.get(c)
        if not ind_name:
            continue
        d = ag.setdefault(ind_name, {"pct": 0.0, "n": 0, "zt": 0, "dt": 0, "amt": 0.0, "up": 0})
        d["pct"] += qq["pct"] or 0
        d["n"] += 1
        d["amt"] += (qq["amount"] or 0) / 1e8
        if qq["zt"]:
            d["zt"] += 1
        if qq["dt"]:
            d["dt"] += 1
        if (qq["pct"] or 0) > 0:
            d["up"] += 1
    rows = []
    for name, d in ag.items():
        n = max(d["n"], 1)
        rows.append({"sector": name, "avg_pct": round(d["pct"] / n, 2),
                     "zt_today": d["zt"], "dt_today": d["dt"],
                     "amount": round(d["amt"], 1),
                     "up_ratio": round(d["up"] / n * 100), "zt_5d": None,
                     "is_dragon_sector": False})
    rows.sort(key=lambda r: -r["avg_pct"])
    return rows


def snapshot():
    return {k: v for k, v in _state.items()
            if k in ("quotes", "quote_date", "ts", "today_zt", "today_dt",
                     "today_ladder", "premium_end", "premium_open", "explosion",
                     "mkt_stats", "yesterday_zt", "state", "last_error",
                     "src", "latency_ms", "full_ticks")}


def get_index_kline(n=520):
    """上证指数日K(新浪主/腾讯备) — 缓存 data/real_index.json(按日)"""
    f = os.path.join(DATA_DIR, "real_index.json")
    try:
        if os.path.exists(f):
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if data.get("n", 0) >= n and data.get("date") == date.today().isoformat():
                return data["rows"]
    except Exception:
        pass
    rows = []
    try:
        rows = sina.fetch_kline("000001", n=n, symbol="sh000001")
    except Exception:
        try:
            rows = tencent.fetch_kline("000001", n=n, symbol="sh000001")
            if rows and rows[-1]["day"] == date.today().isoformat():
                rows = rows[:-1]
        except Exception:
            pass
    try:
        with open(f, "w", encoding="utf-8") as fp:
            json.dump({"rows": rows, "n": len(rows), "date": date.today().isoformat()}, fp, ensure_ascii=False)
    except Exception:
        pass
    return rows
