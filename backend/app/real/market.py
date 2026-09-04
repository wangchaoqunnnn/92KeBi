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
from . import intraday
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
        if q.get("outer") is not None or q.get("inner") is not None:
            quotes[m["code"]]["outer"] = q.get("outer")
            quotes[m["code"]]["inner"] = q.get("inner")
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
            _maybe_archive()
            _maybe_enrich_async()  # 板块历史覆盖不足时懒补齐(后台线程, 每日至多一轮)
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
        _maybe_archive()
        return True
    _state["last_error"] = f"快速双源失败: {err}"
    return False


def _maybe_archive():
    """盘中将“全市场+行业累计成交额”写入分时档案(供次日‘同时段’放量/缩量对比)"""
    try:
        st = _state
        qd = st.get("quote_date") or ""
        quotes = st.get("quotes") or {}
        if not qd or not quotes:
            return
        c2i = (st.get("industry_data") or {}).get("code2industry", {})
        ind_amt = {}
        mkt_amt = 0.0
        for c, q in quotes.items():
            a = q.get("amount") or 0
            mkt_amt += a
            ind = c2i.get(c)
            if ind:
                ind_amt[ind] = ind_amt.get(ind, 0) + a
        mkt_yi = mkt_amt / 1e8
        intraday.archive_row(qd, mkt_yi, {k: v / 1e8 for k, v in ind_amt.items()})
        # 上证指数量能: 今日 vs 昨日同时段(有内置45s缓存, 放锁外执行)
        vol = intraday.index_volume_compare()
        m = _state.get("mkt_stats")
        if m is not None and vol is not None:
            m = dict(m)
            m["volume"] = vol
            with _lock:
                _state["mkt_stats"] = m
    except Exception as e:
        log.warning("archive row: %s", e)


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
            "volume": None,  # 由 _maybe_archive 在锁外补充(上证量能对比, 45s缓存)
        }


def _sector_5d_series():
    """近6个交易日板块日收益序列(旧→新), 用于 5日涨幅 & 连涨跌。
    最新交易日(quote_date)使用全市场实时/收盘聚合(所有成员, 与页面“今日涨跌幅”一致),
    更早日用样本池日K(板块当日有效样本>=3才计值; 不足该日为空 → 5日或连涨受影响时显示—)。
    """
    try:
        from datetime import date as _date
        qd = _state.get("quote_date") or ""
        hist_dates = [r["date"] for r in db.query(
            "SELECT DISTINCT date FROM bars WHERE date<? ORDER BY date DESC LIMIT 6", (qd,))]
        c2i = get_industry_cache().get("code2industry", {})
        day_pct = {}   # date -> {industry: pct均值(样本K线)}
        if hist_dates:
            lo, hi = hist_dates[-1], hist_dates[0]
            bucket = {}
            for b in db.query("SELECT date, code, pct FROM bars WHERE date BETWEEN ? AND ?",
                              (lo, hi)):
                ind = c2i.get(b["code"])
                if not ind:
                    continue
                bucket.setdefault(b["date"], {}).setdefault(ind, []).append(b["pct"])
            for d in sorted(bucket):
                day_pct[d] = {ind: round(sum(ps) / len(ps), 4)
                              for ind, ps in bucket[d].items() if len(ps) >= 3}
        # 最新交易日: 全市场实时聚合(与榜单今日口径一致)
        quotes = _state.get("quotes") or {}
        if qd and quotes:
            live = {}
            for c, q in quotes.items():
                ind = c2i.get(c)
                p = q.get("pct")
                if not ind or p is None:
                    continue
                live.setdefault(ind, []).append(p)
            day_pct[qd] = {ind: round(sum(ps) / len(ps), 4)
                           for ind, ps in live.items() if len(ps) >= 3}
        elif qd and qd not in day_pct:
            # 行情快照暂缺时, 退化为使用样本K线中当日收盘(尽力)
            try:
                bucket = {}
                for b in db.query("SELECT code, pct FROM bars WHERE date=?", (qd,)):
                    ind = c2i.get(b["code"])
                    if ind:
                        bucket.setdefault(ind, []).append(b["pct"])
                day_pct[qd] = {ind: round(sum(ps) / len(ps), 4)
                               for ind, ps in bucket.items() if len(ps) >= 3}
            except Exception:
                pass
        order = sorted(day_pct.keys())
        if len(order) < 5:
            return {}
        out = {}
        for ind in set(x for d in order for x in day_pct[d]):
            vals = [day_pct[d].get(ind) for d in order]
            present = [v for v in vals if v is not None]
            # 5日累计涨幅(复利, 需最近5个交易日数据齐全)
            avg5 = None
            if len(present) >= 5:
                cum = 1.0
                for v in present[-5:]:
                    cum *= 1 + v / 100
                avg5 = round((cum - 1) * 100, 2)
            # 连续涨/跌: 从最新(今日)起向历史回数同向
            streak_n, streak_dir = 0, None
            for v in reversed(vals):
                if v is None:
                    break
                d = 1 if v > 0 else -1 if v < 0 else 0
                if d == 0:
                    break
                if streak_dir is None:
                    streak_dir = "up" if d > 0 else "down"
                if d != (1 if streak_dir == "up" else -1):
                    break
                streak_n += 1
            out[ind] = {"avg5_pct": avg5, "streak_n": streak_n, "streak_dir": streak_dir}
        return out
    except Exception as e:
        log.warning("sector 5d series: %s", e)
        return {}


_ENRICH_DAY = ""
_ENRICH_LOCK = threading.Lock()
_ENRICH_ATTEMPT_TS = 0.0
_ENRICH_CODES = 0
_ENRICH_LAST = ""


def _insert_mini_kline(code, name, rows, max_day=""):
    """把单只股票近N日日K写入 bars(最小字段, 已存在日期忽略)。
    仅写入历史日(day < max_day; 当日由全市场实时聚合覆盖, 不落K)。"""
    rate = sina.limit_rate(code, name)
    prev = None
    out = []
    for r in rows:
        try:
            d = str(r["day"])
            if max_day and d >= max_day:
                continue
            c = float(r["close"])
            o = float(r["open"]) if r.get("open") is not None else c
            h = float(r["high"]) if r.get("high") is not None else max(o, c)
            l = float(r["low"]) if r.get("low") is not None else min(o, c)
            if prev is None:
                pct = 0.0
            else:
                pct = round((c / prev - 1) * 100, 2)
            out.append((d, code, o, h, l, c, round(prev, 2) if prev else c, pct,
                        0.0, None, float(r.get("volume") or 0), 0, 0, 0, 0))
            prev = c
        except Exception:
            continue
    if out:
        with db.tx() as con:
            con.executemany(
                "INSERT OR IGNORE INTO bars(date,code,open,high,low,close,pre_close,pct,turnover,"
                "amount,volume,streak,limit_up,limit_down,one_word) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)


def _enrich_targets(qd, quotes):
    """找出“近5个历史交易日有效样本<3只”的板块及其待补代码(当日成交额靠前)"""
    c2i = get_industry_cache().get("code2industry", {})
    hist_dates = [r["date"] for r in db.query(
        "SELECT DISTINCT date FROM bars WHERE date<? ORDER BY date DESC LIMIT 6", (qd,))]
    if len(hist_dates) < 5:
        return None  # 历史日不足, 无法判断
    lo, hi = hist_dates[-1], hist_dates[0]
    # 每只股票在近5个历史日各有几根K(有则基本可贡献该日板块均值)
    day_counts = {}
    for b in db.query("SELECT code, COUNT(DISTINCT date) n FROM bars "
                      "WHERE date BETWEEN ? AND ? GROUP BY code", (lo, hi)):
        day_counts[b["code"]] = b["n"]
    members = {}
    for c, q in quotes.items():
        ind = c2i.get(c)
        if not ind:
            continue
        members.setdefault(ind, []).append((c, q.get("amount") or 0))
    needed = []
    for ind, lst in members.items():
        if len(lst) < 3:
            continue
        lst.sort(key=lambda x: -x[1])
        good = [c for c, _ in lst if day_counts.get(c, 0) >= 3]
        if len(good) >= 3:
            continue
        want = 3 - len(good)
        for c, _ in lst:
            if want <= 0:
                break
            if day_counts.get(c, 0) < 3:  # 该股缺历史K或覆盖不足
                needed.append(c)
                want -= 1
    return needed or []


def enrich_sector_history():
    """为样本池覆盖不足的板块补齐近12日历史K线(取板块当日成交额靠前成员)。
    每交易日仅做一轮(完成或放弃后置 _ENRICH_DAY)。由刷新线程在盘后/启动时懒触发,
    亦可在 http 管理端手动调用。返回统计。"""
    global _ENRICH_DAY, _ENRICH_ATTEMPT_TS, _ENRICH_CODES, _ENRICH_LAST
    if not _ENRICH_LOCK.acquire(blocking=False):
        return {"ok": False, "reason": "busy"}
    try:
        qd = _state.get("quote_date") or ""
        quotes = _state.get("quotes") or {}
        if not qd or not quotes:
            return {"ok": False, "reason": "无行情"}
        if _ENRICH_DAY == qd:
            return {"ok": True, "cached": True, "enriched": _ENRICH_CODES}
        targets = _enrich_targets(qd, quotes)
        if targets is None:
            _ENRICH_DAY = qd  # 历史日不足也无从补, 记当日避免空转
            return {"ok": False, "reason": "历史日不足"}
        if not targets:
            _ENRICH_DAY = qd
            _ENRICH_CODES = 0
            _ENRICH_LAST = "ok(覆盖足够)"
            return {"ok": True, "enriched": 0}
        targets = targets[:60]  # 单轮上限, 防极端空转
        from ..providers import router
        from concurrent.futures import ThreadPoolExecutor, as_completed
        meta = {r["code"]: r for r in db.query("SELECT code,name FROM stocks")}
        done = 0

        def work(code):
            nm = (meta.get(code) or {}).get("name") or code
            try:
                rows = router.fetch_kline_any(code, n=12)
                _insert_mini_kline(code, nm, rows, max_day=qd)
                return True
            except Exception:
                return False

        with ThreadPoolExecutor(max(3, min(10, len(targets)))) as ex:
            futs = [ex.submit(work, c) for c in targets]
            for fu in as_completed(futs):
                try:
                    if fu.result():
                        done += 1
                except Exception:
                    pass
        _ENRICH_DAY = qd
        _ENRICH_CODES = done
        _ENRICH_LAST = "ok" if done else "全部失败"
        log.info("sector history enrich(%s): codes=%d ok=%d", qd, len(targets), done)
        return {"ok": True, "enriched": done, "total": len(targets)}
    except Exception as e:
        log.warning("sector history enrich: %s", e)
        return {"ok": False, "error": str(e)[:120]}
    finally:
        _ENRICH_ATTEMPT_TS = time.time()
        _ENRICH_LOCK.release()


def enrich_status():
    return {"quote_date": _ENRICH_DAY, "codes": _ENRICH_CODES, "last": _ENRICH_LAST,
            "attempt_ts": _ENRICH_ATTEMPT_TS,
            "attempt_time": time.strftime("%H:%M:%S", time.localtime(_ENRICH_ATTEMPT_TS))
            if _ENRICH_ATTEMPT_TS else None}


def _maybe_enrich_async():
    """全量同步后后台触发一次板块历史补齐(由 _ENRICH_DAY 保证每交易日一轮)"""
    qd = _state.get("quote_date") or ""
    if not qd or _ENRICH_DAY == qd:
        return
    t = threading.Thread(target=enrich_sector_history, daemon=True,
                         name="sector-enrich")
    t.start()


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
    # 5日涨幅 & 连续涨跌(样本池日K; 覆盖不足返回—)
    try:
        s5 = _sector_5d_series()
        for r in rows:
            v = s5.get(r["sector"]) or {}
            r["avg5_pct"] = v.get("avg5_pct")
            r["streak_n"] = v.get("streak_n", 0)
            r["streak_dir"] = v.get("streak_dir")
    except Exception as e:
        log.warning("sector 5d merge: %s", e)
    # 量能: 今日板块累计 vs 昨日同时段(分时档案; 首个运行日无档案 → prev=None)
    try:
        amt_map = {r["sector"]: r["amount"] for r in rows}
        cmp, _pd = intraday.sector_compare_today(_state.get("quote_date") or "", amt_map)
        for r in rows:
            v = cmp.get(r["sector"]) or {}
            r["vol_prev_yi"] = v.get("prev_yi")
            r["vol_ratio"] = v.get("ratio")
    except Exception as e:
        log.warning("sector vol compare: %s", e)
    return rows


def _hot_industry():
    """当前热度最高的行业(当日涨停/连板最高者所在)作为‘主线近似’"""
    st = snapshot()
    ladder = st.get("today_ladder") or {}
    best = None
    for c, s in ladder.items():
        if best is None or s > best[1]:
            best = (c, s)
    if not best:
        return None
    return industry_of(best[0])


def _sector_anchor(industry):
    """板块近日高标(样本日K): 若板块总龙/高标今日未封板(断板/歇整), 作为下拉里的情绪锚提示"""
    try:
        dates = [r["date"] for r in db.query(
            "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT 10")]
        if len(dates) < 2:
            return []
        rows = db.query("SELECT date,code,MAX(streak) s FROM bars "
                        "WHERE date BETWEEN ? AND ? AND streak>=2 GROUP BY code",
                        (dates[-1], dates[0]))
        c2i = get_industry_cache().get("code2industry", {})
        codes = []
        for r in rows:
            if c2i.get(r["code"]) == industry:
                last = db.query_one("SELECT date,streak FROM bars WHERE code=? AND streak>=2 "
                                    "ORDER BY date DESC LIMIT 1", (r["code"],))
                codes.append({"code": r["code"], "max_streak": r["s"],
                              "last_date": last["date"] if last else None,
                              "last_streak": last["streak"] if last else 0})
        quotes = _state.get("quotes") or {}
        codes.sort(key=lambda x: -x["max_streak"])
        out = []
        for c in codes[:2]:
            q = quotes.get(c["code"]) or {}
            zt_now = bool(q.get("zt"))
            out.append({**c,
                        "name": q.get("name") or c["code"],
                        "zt_today": zt_now,
                        "note": (f"近10日最高 {c['max_streak']} 连板(最近 {c.get('last_date')})；"
                                 f"今日{'涨停续板' if zt_now else '未封板(断板/歇整)——仍是板块情绪锚, 其倒下=板块退潮'}")})
        return out
    except Exception as e:
        log.warning("sector anchor: %s", e)
        return []


def sector_zt_detail(industry):
    """某行业当日涨停股明细 + 龙头/补涨/跟风 分层判断(92框架规则, 供板块榜下拉)"""
    st = snapshot()
    quotes = st.get("quotes") or {}
    ladder = st.get("today_ladder") or {}
    c2i = get_industry_cache().get("code2industry", {})
    zt = []
    for c, q in quotes.items():
        if q.get("zt") and c2i.get(c) == industry:
            zt.append(c)

    def _key(c):
        q = quotes.get(c) or {}
        return (-(ladder.get(c) or 0), -((q.get("amount") or 0)))

    zt.sort(key=_key)
    hot = _hot_industry()
    is_dragon = bool(hot and hot == industry)
    if not zt:
        return {"sector": industry, "is_dragon_sector": is_dragon, "items": [],
                "leader": None, "note": "该板块今日无涨停"}

    max_s = max((ladder.get(c) or 0) for c in zt)
    items = []
    leaders = [c for c in zt if (ladder.get(c) or 0) == max_s]
    primary = leaders[0]
    for idx, c in enumerate(zt):
        q = quotes[c]
        s = ladder.get(c) or 0
        if s >= 2 and c in leaders:
            if c == primary:
                role, note = ("板块龙头", f"板块内最高 {s} 连板、成交额居前——辨识度核心；"
                              f"适用龙头战法(分歧买入/弱转强)，断板即撤")
            else:
                role, note = ("同高卡位", f"与龙头同 {s} 板高度, 需次日淘汰确认; 高位卡位追高风险大")
        elif s >= 2:
            role, note = ("中位跟风", f"{s} 板, 高度低于板块龙头——中位跟风(忌讳追高/亏钱效应最先出现在中位)")
        else:
            if c == zt[0]:
                role, note = ("首板领涨", "板块内最强首板(按成交额)，题材萌芽观察")
            else:
                role, note = ("首板跟风", "同板块首板跟风，未确立地位前只看不做")
        if is_dragon and c == primary:
            note += "；【主线板块·总龙候选】"
        items.append({
            "code": c, "name": q["name"], "price": round(q.get("price") or 0, 2),
            "pct": q.get("pct"), "amount_yi": round((q.get("amount") or 0) / 1e8, 2),
            "streak": s, "limit_time": q.get("ticktime"),
            "role": role, "is_leader": c == primary, "note": note,
            "is_dragon_stock": bool(is_dragon and c == primary),
        })
    return {"sector": industry, "is_dragon_sector": is_dragon, "items": items,
            "anchors": _sector_anchor(industry),
            "leader": {"code": primary, "name": quotes[primary]["name"], "streak": max_s},
            "note": "分层口径：龙头=今日板块内最高连板且成交额居前；同高度/中位=跟风(谨慎)；首板=补涨/试错观察。"
                    "若存在上方‘板块高标(anchors)’且今日未封板，表示总龙断板/歇整，需按‘断板即撤’管理。"}


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
