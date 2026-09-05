"""分时量能档案与对比（需求：仪表盘“放量/缩量”统计）：
1) 盘中逐tick归档“全市场成交额 + 各行业累计成交额”到 data/intraday/日期.jsonl
   → 次日/之后可精确对比“今日同时刻 vs 昨日同时刻”（两市与行业两个粒度）
2) 上证指数5分钟K(新浪, 含成交额, 可回溯数日) → 立即可对比“上证量能今日vs昨日同期”
   作为没有档案时的即时口径(标注 basis='上证指数分时'|'全市场分时档案')
"""
import json
import logging
import os
import time
from datetime import date, datetime, timedelta

from .. import cn_time
from ..config import DATA_DIR

log = logging.getLogger("kb.intraday")

ARCH_DIR = os.path.join(DATA_DIR, "intraday")
IDX_MIN_FILE = os.path.join(DATA_DIR, "index_min5.json")

_FETCH_IDX_AT = 0.0
_IDX_CACHE = None


def _mkdir():
    try:
        os.makedirs(ARCH_DIR, exist_ok=True)
    except Exception:
        pass


def _day_file(d):
    return os.path.join(ARCH_DIR, f"{d}.jsonl")


def _in_session_now():
    now = cn_time.now()
    return now.weekday() < 5 and (9 <= now.hour < 16)


def archive_row(quote_date, market_amt_yi, sectors_amt):
    """写入一条分时档案。sectors_amt: {industry: 累计成交额(亿)}"""
    if not quote_date or not _in_session_now():
        return False
    if market_amt_yi is None or market_amt_yi <= 0:
        return False
    _mkdir()
    t = cn_time.now_str("%H:%M:%S")
    rec = {"t": t, "m": round(market_amt_yi, 2),
           "s": {k: round(v, 2) for k, v in sectors_amt.items()}}
    try:
        with open(_day_file(quote_date), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        log.warning("archive write: %s", e)
        return False


def load_day(d):
    rows = []
    try:
        with open(_day_file(d), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("archive read %s: %s", d, e)
    return rows


def recent_prev_day(d):
    """在 d 之前的最近一个工作日档案(有内容)"""
    dt = datetime.strptime(d, "%Y-%m-%d").date()
    for _ in range(7):
        dt -= timedelta(days=1)
        if dt.weekday() >= 5:
            continue
        rows = load_day(dt.isoformat())
        if rows:
            return dt.isoformat(), rows
    return None, []


def _bucket(time_str):
    hh, mm, _ = time_str.split(":")
    return hh + ":" + mm


def nearest_row(rows, now_t, tol_min=4):
    """找与 now_t(HH:MM) 最接近且不晚于其的档案行(容忍 tol_min 分钟)"""
    target_min = int(now_t[:2]) * 60 + int(now_t[3:5])

    def m(row):
        h, mi, _ = row["t"].split(":")
        return int(h) * 60 + int(mi)

    best = None
    for row in rows:
        rm = m(row)
        if rm <= target_min + 1:
            if best is None or rm > m(best):
                best = row
    if best and target_min - m(best) <= tol_min * 60:
        return best
    return None


def sector_compare_today(today_d, sectors_amt_today, now_t=None):
    """行业量能: 今日累计 vs 昨日同时段累计(分时档案)。返回 {sector: {today, prev, ratio, ok}}"""
    now_t = now_t or cn_time.now_str("%H:%M")
    prev_d, prev_rows = recent_prev_day(today_d)
    if not prev_rows:
        return {}, None
    out = {}
    prev_row = nearest_row(prev_rows, now_t)
    for sec, amt in sectors_amt_today.items():
        prev = prev_row["s"].get(sec) if prev_row else None
        out[sec] = {"today_yi": round(amt, 2), "prev_yi": prev,
                    "ratio": round((amt / prev - 1) * 100, 1) if prev else None,
                    "ok": bool(prev_row and prev is not None)}
    return out, prev_d


def market_compare_archive(today_d, market_amt, now_t=None):
    """两市累计成交额 vs 昨日同时段(全市场分时档案)"""
    now_t = now_t or cn_time.now_str("%H:%M")
    prev_d, prev_rows = recent_prev_day(today_d)
    if not prev_rows:
        return None, None
    prev = nearest_row(prev_rows, now_t)
    if not prev or not prev.get("m"):
        return None, None
    ratio = round((market_amt / prev["m"] - 1) * 100, 1)
    return {"prev_yi": prev["m"], "ratio": ratio, "basis": "全市场分时档案"}, prev_d


# ---------------------------------------------------------------- 上证指数5分钟(含成交额)
_vol_cache = {"ts": 0.0, "val": None}
_idxcache = {"ts": 0.0, "rows": []}


def _fetch_min_full():
    """新浪上证指数5分钟K(含 close/amount), 60s内存缓存。失败返回[]"""
    now = time.time()
    if _idxcache["rows"] and now - _idxcache["ts"] < 60:
        return _idxcache["rows"]
    import urllib.request
    try:
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService."
               "getKLineData?symbol=sh000001&scale=5&ma=no&datalen=800")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        import json as _j
        rows = _j.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
        out = []
        for r in rows or []:
            try:
                day = str(r["day"])  # 形如 2026-09-04 10:05:00
                close = float(r.get("close") or 0)
                amt = float(r.get("amount") or 0)
                if day and close > 0:
                    out.append({"day": day, "close": close, "amount": amt})
            except Exception:
                continue
        out.sort(key=lambda x: x["day"])
        _idxcache.update({"ts": now, "rows": out})
        return out
    except Exception:
        return _idxcache["rows"]


def index_day_bars(d, with_amount=True):
    """某交易日的上证5分钟K线序列(按时间升序):
    [{t:'HH:MM', close, yi(该5分钟成交额,亿)}]; 无数据返回 []"""
    rows = [r for r in _fetch_min_full() if r["day"][:10] == d]
    out = []
    for r in rows:
        t = r["day"][11:16]  # HH:MM
        out.append({"t": t, "close": r["close"],
                    "yi": round(r["amount"] / 1e8, 2) if with_amount else None})
    return out


def idx_days():
    """有5分钟K数据的交易日列表(近N日)"""
    seen = []
    for r in _fetch_min_full():
        d = r["day"][:10]
        if not seen or seen[-1] != d:
            seen.append(d)
    return seen


def _bucket_end_label(t):
    """HH:MM:SS → 所在5分钟桶结束标签(与指数K线口径一致, 如 09:35/10:05)"""
    hh, mm, ss = int(t[0:2]), int(t[3:5]), int(t[6:8])
    mins = hh * 60 + mm + (1 if ss else 0)
    # 午休(11:31-12:59)与集合竞价不归档, 归入相邻桶即可
    end = ((mins + 4) // 5) * 5
    return f"{end // 60:02d}:{end % 60:02d}"


def sector_flow_buckets(rows):
    """分时档案(rows) → 每5分钟桶末: {t: {industry: 该桶内成交额增量(亿)}}"""
    last = {}      # industry -> 上一桶末累计
    bucket = {}    # t -> {industry: last值(累计)}
    for row in rows:
        t = _bucket_end_label(row["t"])
        b = bucket.setdefault(t, {})
        for k, v in (row.get("s") or {}).items():
            b[k] = v
    flow = {}
    for t in sorted(bucket):
        cur = bucket[t]
        f = {}
        for k, v in cur.items():
            prev = last.get(k) or 0.0
            f[k] = round(v - prev, 2)
            last[k] = v
        flow[t] = f
    return flow


def _sector_day_pct(d):
    """某日板块涨跌幅(样本K线当日均值, >=3只才计) → {industry: pct}"""
    from .. import db
    try:
        from . import market as real_mkt
        c2i = real_mkt.get_industry_cache().get("code2industry", {})
    except Exception:
        return {}
    rows = db.query("SELECT code, pct FROM bars WHERE date=? AND pct IS NOT NULL", (d,))
    bucket = {}
    for r in rows:
        ind = c2i.get(r["code"])
        if ind:
            bucket.setdefault(ind, []).append(r["pct"])
    return {k: round(sum(v) / len(v), 2) for k, v in bucket.items() if len(v) >= 3}


def sector_move_board(d=None):
    """“板块异动”看板数据(真实口径):
    - 上证指数5分钟K: 收盘价线 + 每5分钟成交额(亿) [异动统计]
    - 分时档案(如存在) → 每5分钟板块成交额增量; 叠加板块当日涨跌(样本K线),
      在指数单桶异动(|涨跌|>=阈值)时标记“拉动/拖累”的板块。
    返回 dict 或 None(无数据)。"""
    days = idx_days()
    if not days:
        return None
    avail = [x for x in days if index_day_bars(x)]
    if not avail:
        return None
    if not d or d not in avail:
        d = avail[-1]
    bars = index_day_bars(d)
    if len(bars) < 2:
        return None
    arch_rows = load_day(d) if os.path.exists(_day_file(d)) else []
    # 完整性守卫: 指数K含上午(<=11:30)但档案缺上午行(如旧版仅收盘后误记) → 档案不可信, 丢弃
    if arch_rows:
        bars_morning = any(b["t"] <= "11:30" for b in bars)
        arch_morning = any(str(r["t"]) <= "11:30" for r in arch_rows)
        if bars_morning and not arch_morning:
            arch_rows = []
    flow = sector_flow_buckets(arch_rows) if arch_rows else {}
    sp = _sector_day_pct(d) or {}
    # 今日盘中: 板块方向以实时快照补足(档案未生成时也有方向可标)
    if not sp:
        try:
            from .. import db as _db
            from . import market as real_mkt
            qd = real_mkt.snapshot().get("quote_date") or ""
            if qd == d:
                c2i = real_mkt.get_industry_cache().get("code2industry", {})
                live = {}
                for c, q in (real_mkt.snapshot().get("quotes") or {}).items():
                    ind = c2i.get(c)
                    p = q.get("pct")
                    if ind and p is not None:
                        live.setdefault(ind, []).append(p)
                sp = {k: round(sum(v) / len(v), 2) for k, v in live.items() if len(v) >= 3}
        except Exception:
            pass

    # 每桶: 指数涨跌(相对上一桶) —— 与板块贡献无关, 只描述指数本身
    buckets = []
    prev_close = None
    for b in bars:
        t, close, yi = b["t"], b["close"], b["yi"]
        move = round((close / prev_close - 1) * 100, 3) if prev_close else None
        buckets.append({"t": t, "close": close, "yi": yi, "move_pct": move})
        prev_close = close

    # 板块标记: 首选“个股5分钟K×市值权重”归因(真实具体板块); 失败再退回档案口径
    moves = _movers_by_stocks(d, bars)
    note = "上证5分钟K × 沪市成交额居前80只×市值权重估算板块贡献"
    if moves is None and arch_rows:
        moves = []
        prev_close2 = None
        for b in bars:
            t, close = b["t"], b["close"]
            move = round((close / prev_close2 - 1) * 100, 3) if prev_close2 else None
            prev_close2 = close
            bflow = flow.get(t)
            if not bflow or move is None or abs(move) < 0.05:
                continue
            mv = 1 if move >= 0 else -1
            scored = []
            for sec, delta in bflow.items():
                dp = sp.get(sec)
                if dp is None:
                    continue
                match = 1 if (dp > 0) == (mv > 0) else (0.6 if dp == 0 else 0)
                scored.append((sec, delta, dp, match))
            scored.sort(key=lambda x: -(x[1] * x[3]))
            picks = [{"sector": s, "pct": dp, "delta_yi": round(dl, 2)}
                     for s, dl, dp, _m in scored[:3] if dl > 0.5]
            if picks:
                moves.append({"t": t, "close": close, "yi": b["yi"], "move_pct": move,
                              "dir": "up" if mv > 0 else "down", "sectors": picks})
        note = "分时档案口径(盘后自动积累, 仅供兜底)"
    moves = (moves or [])[:24]
    return {"date": d, "days": avail, "buckets": buckets, "moves": moves,
            "has_archive": bool(arch_rows), "note": note}


# ---------------------------------------------------------------- 板块异动归因: 个股5分钟K × 市值权重
_sm_memo = {"d": "", "val": None, "ts": 0.0}
_sm_lock = __import__("threading").Lock()


def _fetch_stock_min_full(symbol):
    """个股5分钟K(新浪) → [{day, close, amount}], 失败返回 []"""
    import urllib.request
    import json as _j
    try:
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService."
               "getKLineData?symbol=%s&scale=5&ma=no&datalen=400" % symbol)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        rows = _j.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "ignore"))
        out = []
        for r in rows or []:
            try:
                out.append({"day": str(r["day"]), "close": float(r.get("close") or 0),
                            "amount": float(r.get("amount") or 0)})
            except Exception:
                continue
        return out
    except Exception:
        return []


def _movers_by_stocks(d, bars):
    """“哪几个具体板块拖动了上证指数”：取沪市(6开头)当日成交额居前80只个股,
    拉取各自5分钟K, 用 市值(nmc) 权重把每5分钟涨幅折算为对上证指数的贡献(%), 再按板块聚合。
    仅保留指数单桶|涨跌|≥0.045%的时段, 标记同向贡献最大的板块。无数据/失败返回 []。"""
    global _sm_memo
    try:
        now = time.time()
        # 今日盘中每4分钟刷新; 历史日/已收盘直接用磁盘缓存
        cache_file = os.path.join(ARCH_DIR, f"sm_{d}.json")
        if _sm_memo["d"] == d and now - _sm_memo["ts"] < 240:
            return list(_sm_memo["val"])
        if os.path.exists(cache_file) and d < cn_time.today_str():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                _sm_memo.update({"d": d, "val": cached, "ts": now})
                return list(cached)
            except Exception:
                pass
        from . import market as real_mkt
        snap = real_mkt.snapshot()
        quotes = snap.get("quotes") or {}
        c2i = real_mkt.get_industry_cache().get("code2industry", {})
        # 指数每桶涨跌(相对上一桶), 由自身K线序列现算(外部 bars 无 move 字段)
        bs = sorted(bars, key=lambda x: x["t"])
        idx_t, _pv = {}, None
        for b in bs:
            mv = round((b["close"] / _pv - 1) * 100, 3) if _pv else None
            idx_t[b["t"]] = {"close": b["close"], "yi": b.get("yi"), "move_pct": mv}
            _pv = b["close"]
        if len(idx_t) < 2:
            return []
        # 候选池: 沪市且可归板块、有成交额与市值
        cand = [(c, q) for c, q in quotes.items()
                if c.startswith(("60", "68")) and c2i.get(c)
                and (q.get("amount") or 0) > 0 and (q.get("nmc") or 0) > 0]
        cand.sort(key=lambda x: -(x[1]["amount"] or 0))
        top = cand[:80]
        if len(top) < 15:
            return []
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=12) as ex:
            futs = {ex.submit(_fetch_stock_min_full, "sh" + c): c for c, _ in top}
            raws = {}
            for fu in as_completed(futs):
                c = futs[fu]
                try:
                    rows = fu.result()
                except Exception:
                    rows = []
                raws[c] = rows
        # 每只股: 保留 d-1 最后一根收盘(算09:35涨幅) + d 全天5分钟
        prev_close = {}
        day_rows = {}
        for c, rows in raws.items():
            byday = {}
            for r in rows:
                byday.setdefault(r["day"][:10], []).append(r)
            ds = sorted(byday)
            if d not in byday:
                continue
            prevs = [x for x in ds if x < d]
            if prevs:
                prev_close[c] = byday[prevs[-1]][-1]["close"]
            day_rows[c] = byday[d]
        if len(day_rows) < 15:
            return []
        nmc = {c: q["nmc"] for c, q in top}
        wsum = sum(nmc.values()) or 1.0
        # 板块贡献聚合: t -> {板块: {"pct": 加权贡献%, "amt_yi": 该桶板块内成交额(亿)}}
        agg = {}
        for c, rows in day_rows.items():
            sec = c2i.get(c) or "其他"
            prev = prev_close.get(c)
            w = nmc[c] / wsum
            for r in rows:
                t = r["day"][11:16]
                if t not in idx_t:
                    continue
                close = r["close"]
                pct = (close / prev - 1) * 100 if prev else 0.0
                a = agg.setdefault(t, {})
                s = a.setdefault(sec, {"p": 0.0, "amt": 0.0})
                s["p"] += pct * w
                s["amt"] += (r["amount"] or 0) / 1e8
                prev = close
        moves = []
        for t, secs in agg.items():
            mv = (idx_t[t] or {}).get("move_pct")
            if mv is None or abs(mv) < 0.045:
                continue
            arr = [(s, v["p"], v["amt"]) for s, v in secs.items() if v["p"] != 0]
            if not arr:
                continue
            arr.sort(key=lambda x: -abs(x[1]))
            sgn = 1 if mv > 0 else -1
            picks = [x for x in arr if (x[1] > 0) == (sgn > 0)][:3]
            if not picks:
                picks = arr[:1]
            moves.append({
                "t": t, "close": idx_t[t]["close"], "yi": idx_t[t]["yi"],
                "move_pct": mv, "dir": "up" if mv > 0 else "down",
                "sectors": [{"sector": s, "pct": round(p, 3), "amt_yi": round(a, 1)}
                            for s, p, a in picks],
            })
        moves.sort(key=lambda x: -abs(x["move_pct"]))
        moves = moves[:18]
        moves.sort(key=lambda x: x["t"])
        _sm_memo.update({"d": d, "val": moves, "ts": time.time()})
        if d < cn_time.today_str():
            try:
                _mkdir()
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(moves, f, ensure_ascii=False)
            except Exception:
                pass
        return moves
    except Exception as e:
        log.warning("movers by stocks: %s", e)
        return []


def _bucket_avg_yi(bars):
    vals = [b["yi"] for b in bars if b["yi"]]
    return sum(vals) / len(vals) if vals else 0.0


def _fetch_min_raw():
    """新浪指数5分钟K(含 amount 字段), 失败返回[]"""
    import urllib.request
    try:
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService."
               "getKLineData?symbol=sh000001&scale=5&ma=no&datalen=800")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        import json as _j
        rows = _j.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
        out = []
        for r in rows or []:
            try:
                out.append({"day": str(r["day"]), "amount": float(r.get("amount") or 0)})
            except Exception:
                continue
        return out
    except Exception:
        return []


def _minute_bucket(now_t):
    """当前时间 → 所在5分钟桶 'HH:MM'(截至该桶, 排除未来)"""
    hh, mm = int(now_t[:2]), int(now_t[3:5])
    mins = (hh * 60 + mm)
    mins = max(9 * 60 + 30, min(mins, 15 * 60))
    bucket = (mins // 5) * 5
    return f"{bucket // 60:02d}:{bucket % 60:02d}"


def index_volume_compare():
    """上证量能: 今日累计成交额(5min档) vs 昨日同时段。45s缓存。返回 dict|None"""
    now = time.time()
    if _vol_cache["val"] is not None and now - _vol_cache["ts"] < 45:
        return _vol_cache["val"]
    try:
        rows = _fetch_min_raw()
        if len(rows) < 200:
            _vol_cache.update({"ts": now, "val": None})
            return None
        today = cn_time.today_str()
        days = {}
        for r in rows:
            days.setdefault(r["day"][:10], []).append(r)
        dates = sorted(days.keys())
        if today not in days:
            today = dates[-1]
        cur_date = today
        prev_date = None
        for d in reversed(dates):
            if d < cur_date:
                prev_date = d
                break
        if not prev_date:
            _vol_cache.update({"ts": now, "val": None})
            return None
        bucket = _minute_bucket(cn_time.now_str("%H:%M"))

        def cum(d):
            total = 0.0
            for r in days[d]:
                if r["day"][11:16] <= bucket:
                    total += r["amount"]
            return total

        cur, prev = cum(cur_date), cum(prev_date)
        if not cur or not prev:
            _vol_cache.update({"ts": now, "val": None})
            return None
        val = {"today_yi": round(cur / 1e8, 1), "prev_yi": round(prev / 1e8, 1),
               "ratio": round((cur / prev - 1) * 100, 1),
               "basis": "上证指数分时(5min)", "date": cur_date}
        _vol_cache.update({"ts": now, "val": val})
        return val
    except Exception as e:
        log.warning("index volume compare: %s", e)
        _vol_cache.update({"ts": now, "val": None})
        return None
