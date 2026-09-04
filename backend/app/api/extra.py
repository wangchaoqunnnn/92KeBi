"""个股/新闻/回测/管理 路由。"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, market_cache
from ..analyze import fetch_hist
from ..config import DATA_SOURCE
from ..core import backtest as bt
from ..core import pools as pool_mod
from ..core import signals as sig_mod
from ..providers import mock_live, sina
from ..seed.universe import SECTORS

router = APIRouter(prefix="/api")

CANDLE_FIELDS = ("date", "open", "high", "low", "close", "pct", "turnover",
                 "amount", "volume", "streak", "limit_up", "limit_down")


def _ctx():
    ctx = market_cache.get_ctx()
    if not ctx:
        market_cache.force_refresh()
        ctx = market_cache.get_ctx()
    return ctx


# ---------------------------------------------------------------- 个股
def _stock_real_detail(code: str):
    """实盘个股详情(任意代码, 按需拉真实K线)"""
    from ..real import analyze_real
    from ..real import market as real_mkt
    d = analyze_real.detail_view(code)
    if not d:
        return None
    bars = db.query("SELECT * FROM bars WHERE code=? ORDER BY date DESC LIMIT 300", (code,))
    if not bars:
        from ..real import sample as real_sample
        meta = {"code": code, "name": d["meta"].get("name", code)}
        try:
            bar_rows = real_sample._bars_for_code(code, meta)
            if bar_rows:
                with db.tx() as con:
                    con.execute("DELETE FROM bars WHERE code=?", (code,))
                    con.executemany(
                        "INSERT INTO bars(date,code,open,high,low,close,pre_close,pct,turnover,"
                        "amount,volume,streak,limit_up,limit_down,one_word) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", bar_rows)
                bars = db.query("SELECT * FROM bars WHERE code=? ORDER BY date DESC LIMIT 300", (code,))
        except Exception as e:  # noqa
            pass
    bars = bars[::-1]
    view = market_cache.get_view(max_age=120) or {}
    ctx = market_cache.get_ctx() or {}
    dragon = ctx.get("dragon") or {}
    feat = d.get("feat") or {}
    role = ("市场总龙" if dragon and dragon.get("code") == code else
            "主线板块" if dragon and feat.get("sector") == dragon.get("sector") else "普通")
    sec_stat = next((r for r in (d.get("sector_stats") or [])
                     if r["sector"] == (d["meta"].get("sector") or "")), {})
    heat = min(100, 45 + (sec_stat.get("zt_today") or 0) * 6)
    return {
        "meta": {**d["meta"], "sector": d["meta"].get("sector") or "未分类", "market": "沪深A"},
        "sector_heat": heat,
        "sector_policy": real_mkt.policy_hard(code, d["meta"].get("name", "")),
        "sector_keywords": [d["meta"].get("sector")] if d["meta"].get("sector") else [],
        "date": d.get("date"),
        "phase": (view.get("phase") or {}).get("phase_cn") or ctx.get("phase_cn"),
        "feat": {k: feat.get(k) for k in ("close", "run20_pct", "run60_pct", "dist_high60_pct",
                                          "gain_low60_pct", "vol20_pct", "flat_ratio40", "streak_max20",
                                          "turnover_avg5", "turnover_max60", "seal_ratio",
                                          "news_code_n", "news_sector_n", "sector_avg_pct",
                                          "sector_zt_5d", "sector_zt_today", "one_word_today")},
        "role": role,
        "conds": d.get("conds"),
        "signals": d.get("signals") or [],
        "kline": [{f: b[f] for f in CANDLE_FIELDS} for b in bars],
        "news": [],
        "mode": "real",
    }


@router.get("/stocks/{code}")
def stock_detail(code: str):
    real_res = _stock_real_detail(code)
    if real_res is not None:
        return real_res
    meta = db.query_one("SELECT * FROM stocks WHERE code=?", (code,))
    if not meta:
        raise HTTPException(404, "股票不存在(模拟代码见 数据说明)")
    ctx = _ctx()
    feat = ctx["feats"].get(code)
    bars = db.query("SELECT * FROM bars WHERE code=? ORDER BY date DESC LIMIT 300",
                    (code,))[::-1]
    eval_res = pool_mod.eval_stock(code, ctx) if feat else None
    sigs = sig_mod.for_stock(code, feat, ctx) if feat else []
    news = db.query("SELECT * FROM news WHERE (code=? OR sector=?) ORDER BY date DESC LIMIT 20",
                    (code, meta["sector"]))
    dragon = ctx.get("dragon")
    return {
        "meta": meta,
        "sector_heat": SECTORS[meta["sector_idx"]]["heat"],
        "sector_policy": SECTORS[meta["sector_idx"]]["policy"],
        "sector_keywords": SECTORS[meta["sector_idx"]]["keywords"],
        "date": ctx["date"],
        "phase": ctx["phase_cn"] if "phase_cn" in ctx else None,
        "feat": {k: feat[k] for k in ("close", "run20_pct", "run60_pct", "dist_high60_pct",
                                      "gain_low60_pct", "vol20_pct", "flat_ratio40", "streak_max20",
                                      "turnover_avg5", "turnover_max60", "seal_ratio",
                                      "news_code_n", "news_sector_n", "sector_avg_pct",
                                      "sector_zt_5d", "sector_zt_today", "one_word_today")} if feat else None,
        "role": ("市场总龙" if dragon and dragon["code"] == code else
                 ("主线板块" if dragon and feat and feat["sector"] == dragon["sector"] else "普通")),
        "conds": eval_res,   # leader/buyang/qiehuan 三套逐条
        "signals": sigs,
        "kline": [{f: b[f] for f in CANDLE_FIELDS} for b in bars],
        "news": news,
        "mode": "mock",
    }


@router.get("/stocks/{code}/compare")
def stock_compare(code: str):
    from ..config import DATA_SOURCE
    if DATA_SOURCE == "real":
        from ..real import market as real_mkt
        ind = real_mkt.industry_of(code)
        peers = []
        for c, q in (real_mkt.snapshot().get("quotes") or {}).items():
            if c != code and real_mkt.industry_of(c) == ind:
                peers.append({"code": c, "name": q["name"]})
            if len(peers) >= 8:
                break
        return {"sector": ind or "未分类", "peers": peers}
    meta = db.query_one("SELECT * FROM stocks WHERE code=?", (code,))
    if not meta:
        raise HTTPException(404, "not found")
    peers = db.query("SELECT code,name FROM stocks WHERE sector=? AND code!=? LIMIT 8",
                     (meta["sector"], code))
    return {"sector": meta["sector"], "peers": peers}


@router.get("/news")
def news(code: str = None, sector: str = None, limit: int = Query(30, le=100)):
    from ..config import DATA_SOURCE
    if DATA_SOURCE == "real":
        return {"items": [], "note": "新闻/公告接口尚未接入：请使用行情+盘面人工研判题材；"
                                     "切换池 S-05 新闻驱动条件暂按未触发处理。"}
    sql, params = "SELECT * FROM news WHERE 1=1", []
    if code:
        sql += " AND (code=? OR sector IN (SELECT sector FROM stocks WHERE code=?))"
        params += [code, code]
    if sector:
        sql += " AND sector=?"
        params.append(sector)
    sql += " ORDER BY date DESC, id DESC LIMIT ?"
    params.append(limit)
    return {"items": db.query(sql, params)}


# ---------------------------------------------------------------- 回测
class BacktestReq(BaseModel):
    mode: str = Field("auto", description="auto/leader/buyang/qiehuan")
    start: str = "2024-01-01"
    end: str = ""
    capital: float = Field(100000, gt=1000)


@router.get("/backtest/meta")
def backtest_meta():
    from ..config import DATA_SOURCE as _ds
    hi = db.query_one("SELECT MAX(date) AS d FROM bars")
    lo = db.query_one("SELECT MIN(date) AS d FROM bars")
    if _ds == "real":
        sample_n = int(db.meta_get("sample_n", 0))
        note = (f"信号当日收盘成交(含单边0.15%成本)，止损-5%按盘中触发；"
                f"回测基于{db.meta_get('sample_n', 0)}只真实日K样本(成交额前{sample_n}, 前复权口径)；"
                f"未模拟一字封死无法买入、停牌/涨跌停不可成交、滑点等微观约束。情绪判定按样本折算全市场口径。")
    else:
        note = "信号当日收盘成交(含单边0.15%成本)，止损-5%按盘中触发；行情为内置模拟(名称代码虚构)，仅供演示计算管线。"
    return {"range": [lo["d"] if lo else None, hi["d"] if hi else None],
            "mode": "real" if _ds == "real" else "mock",
            "modes": [{"key": "auto", "cn": "全周期轮动(按情绪自动切换模式)", "phases": "完整四阶段"},
                      {"key": "leader", "cn": "龙头战法", "phases": "仅主升阶段"},
                      {"key": "buyang", "cn": "补涨战法", "phases": "仅高位震荡阶段"},
                      {"key": "qiehuan", "cn": "切换战法", "phases": "仅试错期"}],
            "note": note}


@router.post("/backtest/run")
def backtest_run(req: BacktestReq):
    hi = db.meta_get("last_date")
    end = req.end or hi
    result = bt.run_backtest(req.start, end, req.capital, req.mode)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# ---------------------------------------------------------------- 管理
@router.get("/admin/status")
def admin_status():
    from ..tasks.scheduler import status as sstatus
    st = sstatus()
    n_stocks = len(db.query("SELECT code FROM stocks"))
    st["db"] = {"stocks": n_stocks, "bars": db.query_one("SELECT COUNT(*) AS n FROM bars")["n"],
                "news": int(db.meta_get("news_count") or 0), "days": int(db.meta_get("days", 0))}
    st["data_source"] = DATA_SOURCE
    st["view"] = {"ts": round(market_cache._cache.get("ts", 0), 1),
                  "date": (market_cache._cache.get("view") or {}).get("date"),
                  "phase": ((market_cache._cache.get("view") or {}).get("phase") or {}).get("phase_cn")}
    if DATA_SOURCE == "real":
        from ..real import market as real_mkt
        from ..real import sample as real_sample
        from ..providers import router as src_router
        snap = real_mkt.snapshot()
        mkt = snap.get("mkt_stats") or {}
        st["real"] = {"quote_date": snap.get("quote_date"), "state": snap.get("state"),
                      "snapshot_ts": round(snap.get("ts", 0), 1),
                      "market": mkt,
                      "src": snap.get("src"), "latency_ms": snap.get("latency_ms"),
                      "poll_count": st.get("poll_count"),
                      "sample_progress": real_sample.progress(),
                      "sources": src_router.health_status(),
                      "industry_codes": len(real_mkt.get_industry_cache().get("code2industry", {}))}
    else:
        st["live"] = mock_live.state()
    return st


@router.post("/admin/refresh")
def admin_refresh():
    if DATA_SOURCE == "real":
        from ..real import market as real_mkt
        real_mkt.refresh_quotes()   # 强制取最新全市场快照
    v = market_cache.force_refresh()
    return {"ok": True, "date": v["date"], "phase": v["phase"]["phase_cn"],
            "zt": v["stats"]["zt_count"], "dt": v["stats"]["dt_count"]}


class AdvanceReq(BaseModel):
    days: int = Field(1, ge=1, le=20)


@router.post("/admin/advance")
def admin_advance(req: AdvanceReq):
    """推进模拟交易日：仅 mock 模式可用(确定性重建更长历史)。实盘请等待真实行情推进。"""
    if DATA_SOURCE == "real":
        raise HTTPException(400, "advance 仅适用于 mock(模拟)模式；实盘行情随时间自然演进")
    from ..seed.run_seed import advance_days
    res = advance_days(req.days)
    v = market_cache.force_refresh()
    return {"ok": True, "days_total": res["days"], "new_last_date": v["date"],
            "phase": v["phase"]["phase_cn"]}


@router.post("/admin/probe-sina")
def probe_sina():
    """新浪数据源连通性自检(两种模式均可)"""
    try:
        rows = sina.fetch_hq_quotes(["sh600519", "sz300750", "sh600000"], timeout=6)
        ok = bool(rows)
        return {"ok": ok, "samples": list(rows.values()),
                "note": "新浪实时行情接口可达。实盘模式需要外网能访问 vip.stock.finance.sina.com.cn / quotes.sina.cn。"}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e),
                "note": "无法访问新浪行情接口——实盘模式需要外网；可临时用 DATA_SOURCE=mock 离线演示。"}
