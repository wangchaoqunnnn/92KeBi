"""实时模式“样本池”与真实历史行情：
- 按当日成交额选前 REAL_CRAWL_N 只(排除北交所/新股前缀)作为分析/回测样本池
- 逐只拉取 REAL_CRAWL_DAYS 天真实日K入库(bars表), 计算涨停/连板/炸板状态(前复权口径按价格判定)
- 启动时若样本已最新则跳过(增量)
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from .. import db
from ..config import REAL_CRAWL_CONCURRENCY, REAL_CRAWL_DAYS, REAL_CRAWL_N
from ..providers import router, sina

log = logging.getLogger("kb.real")

_progress = {"state": "idle", "total": 0, "done": 0, "errors": 0, "started": None}


def progress():
    return dict(_progress)


def _pick_sample(quotes):
    """quotes: code->quote。按成交额降序取前N(沪深, 排除新股/北交所/停牌)"""
    cand = []
    for c, q in quotes.items():
        name = q["name"]
        if c.startswith(("4", "8", "92")) or sina.is_new_listing(c, name):
            continue
        amt = q["amount"] or 0
        if amt <= 0:
            continue
        cand.append((c, q, amt))
    cand.sort(key=lambda x: -x[2])
    top = cand[:REAL_CRAWL_N]
    out = []
    for c, q, _ in top:
        out.append({
            "code": c, "name": q["name"],
            "amount_yi": round((q["amount"] or 0) / 1e8, 2),
            "price": q["price"], "float_cap": (q["nmc"] or 0) / 1e4 if q["nmc"] else None,
            "pe": q["per"], "industry": "",
        })
    return out


def sync_sample_stocks():
    """用当前快照重选样本池并写入 stocks 表(保留行业映射)"""
    from . import market as real_mkt
    quotes = real_mkt.snapshot().get("quotes") or {}
    if not quotes:
        return 0, "无行情快照"
    sample = _pick_sample(quotes)
    ind_map = real_mkt.get_industry_cache().get("code2industry", {})
    rows = []
    for s in sample:
        ind = ind_map.get(s["code"], "")
        s["industry"] = ind
        rows.append((s["code"], s["name"], ind, 0, "", s["float_cap"] or 0,
                     s["price"] or 0, s["pe"] or 0, "沪深A"))
    with db.tx() as con:
        con.execute("DELETE FROM stocks")
        con.executemany(
            "INSERT INTO stocks(code,name,sector,sector_idx,tags,float_cap,start_price,pe,market) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)
    db.meta_set("sample_n", len(rows))
    db.meta_set("sample_date", date.today().isoformat())
    return len(rows), ""


def _bars_for_code(code, meta):
    """拉取一只股票日K → 生成 bars 行(含连板/一字标记)。新浪主/腾讯备。"""
    name = meta["name"]
    rows = router.fetch_kline_any(code, n=REAL_CRAWL_DAYS)
    rate = sina.limit_rate(code, name)
    out = []
    prev_close = None
    prev_streak = 0
    for r in rows:
        d, o, h, l, c = r["day"], r["open"], r["high"], r["low"], r["close"]
        if prev_close is None or prev_close <= 0:
            pct, lu, ld = 0.0, False, False
            streak = 0
        else:
            pct = round((c / prev_close - 1) * 100, 2)
            lu = not sina.is_new_listing(code, name) and sina.is_limit_up(c, prev_close, rate)
            ld = not sina.is_new_listing(code, name) and sina.is_limit_down(c, prev_close, rate)
            streak = prev_streak + 1 if lu else 0
        one_word = int(bool(lu and o and prev_close and o >= round(prev_close * (1 + rate), 2) - 1e-6))
        amt = None  # 该K线接口无成交额字段
        vol = r.get("volume") or 0
        out.append((d, code, o, h, l, c, prev_close if prev_close else c, pct,
                    0.0, amt, vol, streak, int(lu), int(ld), one_word))
        prev_close = c
        prev_streak = streak
    return out


def crawl_sample(force=False):
    """拉取样本池全部真实日K入库。返回统计 dict。"""
    stocks = db.query("SELECT code,name FROM stocks ORDER BY code")
    if not stocks:
        return {"state": "no_sample"}
    _progress.update({"state": "running", "total": len(stocks), "done": 0, "errors": 0,
                      "started": time.strftime("%H:%M:%S")})
    t0 = time.time()
    ok = 0
    with ThreadPoolExecutor(REAL_CRAWL_CONCURRENCY) as ex:
        futs = {}
        for s in stocks:
            futs[ex.submit(_bars_for_code, s["code"], s)] = s["code"]
        for fu in as_completed(futs):
            code = futs[fu]
            try:
                rows = fu.result()
                with db.tx() as con:
                    con.execute("DELETE FROM bars WHERE code=?", (code,))
                    if rows:
                        con.executemany(
                            "INSERT INTO bars(date,code,open,high,low,close,pre_close,pct,turnover,"
                            "amount,volume,streak,limit_up,limit_down,one_word) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                ok += 1
            except Exception as e:
                log.warning("crawl %s: %s", code, e)
                _progress["errors"] += 1
            _progress["done"] = ok
    db.meta_set("bars_updated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    _progress.update({"state": "done", "elapsed_s": round(time.time() - t0, 1), "ok": ok})
    log.info("crawl sample done ok=%d errors=%d %.1fs", ok, _progress["errors"], time.time() - t0)
    return _progress


def is_up_to_date():
    """今日/最近交易日数据是否已入库(且覆盖当前样本池)"""
    last = db.meta_get("bars_updated_at", "")
    if not last:
        return False
    r = db.query_one("SELECT MAX(date) AS d FROM bars")
    idx_rows = None
    try:
        from . import market as real_mkt
        idx_rows = real_mkt.get_index_kline(n=10)
    except Exception:
        return False
    if not idx_rows:
        return False
    last_idx_day = idx_rows[-1]["day"] if idx_rows else ""
    codes_in_bars = db.query_one("SELECT COUNT(DISTINCT code) AS n FROM bars")["n"]
    codes_in_stocks = len(db.query("SELECT code FROM stocks"))
    fresh = bool(r and r["d"] and last_idx_day and r["d"] >= last_idx_day)
    covered = codes_in_bars >= codes_in_stocks * 0.9
    return fresh and covered
