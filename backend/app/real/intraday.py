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
    now = datetime.now()
    return now.weekday() < 5 and (9 <= now.hour < 16)


def archive_row(quote_date, market_amt_yi, sectors_amt):
    """写入一条分时档案。sectors_amt: {industry: 累计成交额(亿)}"""
    if not quote_date or not _in_session_now():
        return False
    if market_amt_yi is None or market_amt_yi <= 0:
        return False
    _mkdir()
    t = time.strftime("%H:%M:%S")
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
    now_t = now_t or time.strftime("%H:%M")
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
    now_t = now_t or time.strftime("%H:%M")
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
        today = date.today().isoformat()
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
        bucket = _minute_bucket(time.strftime("%H:%M"))

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
