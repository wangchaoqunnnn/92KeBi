"""种子导入：把生成的模拟市场写入 SQLite（全量重建，事务内完成）。"""
import sys
import time

from .. import db
from .universe import STOCKS, SECTORS
from .history import generate_market
from ..config import MOCK_TRADING_DAYS, MOCK_SEED

MARKETS = {"600": "沪市主板", "601": "沪市主板", "603": "沪市主板", "000": "深市主板",
           "002": "深市主板", "300": "创业板", "301": "创业板"}


def seed_market(total_days: int = None, seed: int = None, force: bool = False) -> dict:
    total_days = total_days or MOCK_TRADING_DAYS
    seed = seed or MOCK_SEED
    db.init_db()
    last_date = db.meta_get("last_date")
    if last_date and not force:
        return {"ok": True, "cached": True, "days": int(db.meta_get("days", 0))}

    t0 = time.time()
    bars_by_date, news, dates = generate_market(total_days, seed)
    db.execute("DELETE FROM bars")
    db.execute("DELETE FROM news")
    db.execute("DELETE FROM meta")

    stocks_rows = []
    for (name, code, sector_idx, tags, fcap, price, pe) in STOCKS:
        sec = SECTORS[sector_idx]["name"]
        mkt = MARKETS.get(code[:3], "其他")
        stocks_rows.append((code, name, sec, sector_idx, tags, fcap, price, pe, mkt))
    db.execute("DELETE FROM stocks")
    with db.tx() as con:
        con.executemany(
            "INSERT INTO stocks(code,name,sector,sector_idx,tags,float_cap,start_price,pe,market) "
            "VALUES(?,?,?,?,?,?,?,?,?)", stocks_rows)
        bar_rows = []
        for dt, rows in bars_by_date.items():
            for b in rows:
                bar_rows.append((b["date"], b["code"], b["open"], b["high"], b["low"], b["close"],
                                 b["pre_close"], b["pct"], b["turnover"], b["amount"], b["volume"],
                                 b["streak"], b["limit_up"], b["limit_down"], b["one_word"]))
        con.executemany(
            "INSERT INTO bars(date,code,open,high,low,close,pre_close,pct,turnover,amount,volume,"
            "streak,limit_up,limit_down,one_word) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", bar_rows)
        news_rows = [(n["date"], n.get("code"), n.get("sector"), n["title"], n["sentiment"],
                      n["source"], n["kind"]) for n in news]
        con.executemany("INSERT INTO news(date,code,sector,title,sentiment,source,kind) "
                        "VALUES(?,?,?,?,?,?,?)", news_rows)
    db.meta_set("last_date", dates[-1].isoformat())
    db.meta_set("first_date", dates[0].isoformat())
    db.meta_set("days", total_days)
    db.meta_set("seed", seed)
    db.meta_set("bars_count", len(bar_rows))
    db.meta_set("news_count", len(news_rows))
    return {"ok": True, "cached": False, "days": total_days, "bars": len(bar_rows),
            "news": len(news_rows), "elapsed_s": round(time.time() - t0, 2)}


def advance_days(extra: int = 1) -> dict:
    """推进模拟交易日：按当前 seed 重生成 total+extra 天（确定性前缀不变，全量重建）。"""
    cur = int(db.meta_get("days", MOCK_TRADING_DAYS))
    seed = int(db.meta_get("seed", MOCK_SEED))
    return seed_market(total_days=cur + extra, seed=seed, force=True)


if __name__ == "__main__":
    sys.path.insert(0, ".")
    from app.config import DB_PATH
    print("db:", DB_PATH)
    res = seed_market(force=True)
    print("seed result:", res)
