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

    # 每桶: 指数涨跌(相对上一桶) + 板块流量
    buckets, moves = [], []
    prev_close = None
    for i, b in enumerate(bars):
        t, close, yi = b["t"], b["close"], b["yi"]
        move = round((close / prev_close - 1) * 100, 3) if prev_close else None
        rec = {"t": t, "close": close, "yi": yi, "move_pct": move}
        buckets.append(rec)
        prev_close = close
        bflow = flow.get(t)
        # 指数异动桶: 5分钟涨跌幅度≥0.05% 或 极端量能(>全天桶均值2.2倍)
        if (move is not None and abs(move) >= 0.05) or (bflow and yi and yi >= 3.5 * _bucket_avg_yi(bars)):
            if not bflow:
                continue
            mv = 1 if (move or 0) >= 0 else -1
            scored = []
            for sec, delta in bflow.items():
                dp = sp.get(sec)
                if dp is None:
                    continue
                match = 1 if (dp > 0) == (mv > 0) else (0.6 if dp == 0 else 0)
                scored.append((sec, delta, dp, match))
            scored.sort(key=lambda x: -(x[1] * x[3]))
            picks = []
            for sec, delta, dp, _m in scored[:3]:
                if delta <= 0.5:   # 小于0.5亿的桶内增量忽略
                    continue
                picks.append({"sector": sec, "pct": dp, "delta_yi": round(delta, 2)})
            if picks:
                moves.append({"t": t, "close": close, "yi": yi, "move_pct": move,
                              "dir": "up" if mv > 0 else "down", "sectors": picks})
    moves.sort(key=lambda x: -abs(x["move_pct"] or 0))
    moves = moves[:24]
    moves.sort(key=lambda x: x["t"])
    return {"date": d, "days": avail, "buckets": buckets, "moves": moves,
            "has_archive": bool(arch_rows)}


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
