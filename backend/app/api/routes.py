"""REST API。视图数据由 analyze/engine 计算，此处仅做裁剪与 JSON 化。"""
from fastapi import APIRouter, HTTPException, Query

from .. import db, market_cache
from ..config import DATA_SOURCE
from ..core import risk
from ..core.text import PHASE_CN
from ..providers import mock_live

router = APIRouter(prefix="/api")

MOCK_DISCLAIMER = ("本系统为学习研究用的“决策辅助工具”，当前展示模拟行情(名称/代码虚构)，"
                   "不构成任何投资建议；最终交易决策须由用户自行判断，据此操作风险自负。")
REAL_DISCLAIMER = ("数据来源: 新浪财经公开接口(实时行情)，可能存在延迟或误差；本系统为学习研究用决策辅助工具，"
                   "不构成任何投资建议；最终交易决策须由用户自行判断，据此操作风险自负。")


def disclaimer():
    return REAL_DISCLAIMER if DATA_SOURCE == "real" else MOCK_DISCLAIMER


def mode_info(extra=None):
    real = DATA_SOURCE == "real"
    d = {"mock": not real, "label": "实时实盘" if real else "模拟数据",
         "data_source": DATA_SOURCE}
    if extra:
        d.update(extra)
    return d


# ---------------------------------------------------------------- meta
@router.get("/meta")
def meta():
    m = mode_info()
    extra = {}
    if DATA_SOURCE == "real":
        from ..real import market as real_mkt
        snap = real_mkt.snapshot()
        mkt = snap.get("mkt_stats") or {}
        from ..providers import router as src_router
        extra = {"quote_date": snap.get("quote_date"), "snapshot_ts": round(snap.get("ts", 0), 1),
                 "quote_state": snap.get("state"),
                 "universe": mkt.get("universe"),
                 "sample_n": int(db.meta_get("sample_n", 0)),
                 "src": snap.get("src"),
                 "latency_ms": snap.get("latency_ms"),
                 "sources": src_router.health_status()}
    return {
        "server_time": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": DATA_SOURCE,
        "mock_mode": m["mock"],
        "mode": m,
        "last_date": db.meta_get("last_date") or extra.get("quote_date"),
        "first_date": db.meta_get("first_date"),
        "days": int(db.meta_get("days", 0)),
        "bars": int(db.meta_get("bars_count") or db.meta_get("bars_count") or 0),
        "stocks": len(db.query("SELECT code FROM stocks")),
        "disclaimer": disclaimer(),
        "health": "ok",
        **extra,
    }


@router.get("/market/live")
def market_live():
    """盘中实时行情：real=全市场新浪快照; mock=模拟随机游走。"""
    if DATA_SOURCE == "real":
        from ..real import market as real_mkt
        snap = real_mkt.snapshot()
        quotes = snap.get("quotes") or {}
        rows = []
        for c, q in quotes.items():
            if q.get("price") is None or q.get("price") <= 0:
                continue
            settle = q.get("settlement") or q.get("price")
            rows.append({
                "code": c, "name": q["name"], "price": round(q["price"], 2),
                "pre_close": round(settle, 2),
                "pct": round((q["price"] / settle - 1) * 100, 2) if settle else 0,
                "high": q.get("high"), "low": q.get("low"),
                "amount": round((q.get("amount") or 0) / 1e8, 2),
                "turnover": q.get("turnover"),
                "ts": round(snap.get("ts", 0), 1), "base_pct": 0,
                "zt": q.get("zt"), "dt": q.get("dt"),
                "ticktime": q.get("ticktime"),
            })
        rows.sort(key=lambda r: -r["pct"])
        return {"rows": rows[:60], "tick_ts": round(snap.get("ts", 0), 1),
                "date": snap.get("quote_date"), "state": snap.get("state"),
                "src": snap.get("src"), "latency_ms": snap.get("latency_ms"),
                "mkt": snap.get("mkt_stats")}
    rows = mock_live.snapshot()
    if not rows:  # 未初始化则按最近行情初始化
        hist = _hist()
        mock_live.init([{"code": b["code"], "name": b["name"], "close": b["close"],
                         "pre_close": b["pre_close"], "high": b["high"], "low": b["low"],
                         "amount": b.get("amount"), "pct": b["pct"]}
                        for b in hist["day_bars"][-1]])
        rows = mock_live.snapshot()
    st = mock_live.state()
    return {"rows": rows[:40], "tick_ts": st["tick_ts"], "date": db.meta_get("last_date")}


@router.get("/market/sector-zt")
def market_sector_zt(sector: str = Query("", max_length=30)):
    """板块内当日涨停股 + 龙头/补涨/跟风分层判断(实盘)"""
    if DATA_SOURCE != "real":
        raise HTTPException(400, "该接口仅实盘(real)模式可用")
    from ..real import market as real_mkt
    sector = sector.strip()
    if not sector:
        raise HTTPException(422, "缺少 sector 参数")
    return real_mkt.sector_zt_detail(sector)


# ---------------------------------------------------------------- dashboard
def _view():
    return market_cache.get_view()


@router.get("/dashboard/overview")
def dashboard_overview():
    v = _view()
    if not v:
        raise HTTPException(503, "数据尚未就绪(实盘行情初始化中或数据源不可达)")
    ph = v["phase"]
    buy_recs = []
    for s in v["signals"]["items"]:
        if s["dir"] == "buy":
            buy_recs.append({"code": s["code"], "name": s["name"], "sector": s["sector"],
                             "signal": s["signal"], "dir": "buy", "strength": s["strength"]})
    plan = risk.position_plan(ph["phase"], ph["conf"], buy_recs)
    sectors = _sector_rows(v)
    dragon = v["leaders"]["dragon"]
    mode = mode_info()
    real_extra = {}
    if DATA_SOURCE == "real":
        real_extra = {"market": v.get("market") or {},
                      "mode_note": ("情绪趋势与龙头池基于“成交额前N实时样本”折算全市场口径；"
                                    "涨停/跌停/溢价/炸板等大盘指标为全市场实时精确值")}
    overview = {
        "date": v["date"],
        "mode": {**mode, **real_extra},
        "phase": {k: ph[k] for k in ("phase", "label", "phase_cn", "conf", "reasons",
                                     "desc", "position_range_pct", "mode_text")},
        "stats": {k: v["stats"][k] for k in ("zt_count", "dt_count", "up_count", "down_count",
                                             "mean_pct", "amount_sum", "max_streak", "ladder",
                                             "premium_open", "premium_end", "explosion",
                                             "volume") if k in v["stats"]},
        "stats_history": v["stats_history"][-40:],
        "sectors": {"top": sectors[:5], "bottom": sectors[-3:][::-1]},
        "leaders": {
            "dragon": _leader_short(dragon),
            "count": len(v["leaders"]["sector_leaders"]),
        },
        "pools": {"buyang": (v["pools"].get("buyang") or {}).get("total", 0),
                  "qiehuan": (v["pools"].get("qiehuan") or {}).get("total", 0)},
        "signals": v["signals"]["count"],
        "plan": plan,
        "disclaimer": disclaimer(),
    }
    return overview


@router.get("/dashboard/sectors")
def dashboard_sectors():
    v = _view()
    if not v:
        raise HTTPException(503, "数据尚未就绪")
    return {"date": v["date"], "rows": _sector_rows(v)}


def _sector_rows(v):
    """板块聚合视图（含龙头锚点）"""
    if DATA_SOURCE == "real":
        from ..real import market as real_mkt
        rows = real_mkt.industry_stats_full()
        dragon = v["leaders"]["dragon"]
        for r in rows:
            r["is_dragon_sector"] = bool(dragon and r["sector"] == dragon["sector"])
        return rows
    from ..analyze import fetch_hist
    from ..core import board
    hist = fetch_hist()
    if not hist:
        return []
    today_bars = hist["day_bars"][-1]
    prev_bars = hist["day_bars"][-2] if len(hist["day_bars"]) > 1 else None
    zt5 = {}
    for bars in hist["day_bars"][-5:]:
        for b in bars:
            if b.get("limit_up"):
                zt5.setdefault(b["sector"], []).append(b["code"])
    rows = board.compute_sector_stats(today_bars, prev_bars, days_zt=zt5)
    dragon = v["leaders"]["dragon"]
    for r in rows:
        r["is_dragon_sector"] = bool(dragon and r["sector"] == dragon["sector"])
    return rows


def _leader_short(l):
    if not l:
        return None
    return {k: l[k] for k in ("code", "name", "sector", "score", "streak", "run60",
                              "pct_today", "turnover", "price", "limit_today", "broken_today")}


@router.get("/dashboard/history")
def dashboard_history(days: int = Query(30, ge=5, le=120)):
    v = _view()
    return {"series": v["stats_history"][-days:]}


# ---------------------------------------------------------------- leaders
@router.get("/leaders")
def leaders():
    v = _view()
    if not v:
        raise HTTPException(503, "数据尚未就绪")
    dr = v["leaders"]["dragon"]
    note = ("龙头识别依据：辨识度/逻辑硬/带动性/换手/价格/市场共识六维评分(L-01..L-06)；"
            "总龙 = 最高分(≥45)，板块龙 = 各板块内最强。断板/炸板按当日行情自动标记。")
    if DATA_SOURCE == "real":
        note += " 实盘口径：样本池为成交额前N只，逻辑硬(L-02)采用题材关键词近似，需人工复核。"
    return {
        "date": v["date"],
        "phase": PHASE_CN[v["phase"]["phase"]],
        "mode": mode_info(),
        "dragon": _expand_leader(dr),
        "sector_leaders": [_expand_leader(l) for l in v["leaders"]["sector_leaders"]],
        "pool": [{k: l[k] for k in ("code", "name", "sector", "score", "streak", "run60",
                                    "pct_today", "turnover", "price", "limit_today",
                                    "broken_today")} for l in v["leaders"]["pool"]],
        "note": note,
    }


def _expand_leader(l):
    if not l:
        return None
    d = {k: l[k] for k in ("code", "name", "sector", "score", "streak", "run60",
                           "pct_today", "turnover", "price", "limit_today", "broken_today", "role")}
    d["conds"] = l.get("conds") or []
    return d


@router.get("/leaders/history")
def leaders_history(days: int = Query(40, ge=10, le=120)):
    """龙头历史回溯：逐日找出最高连板标的，形成“谁是当时龙头”时间线。"""
    dates = [r["date"] for r in db.query(
        "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT ?", (days,))]
    dates.reverse()
    if not dates:
        return {"rows": []}
    rows_all = db.query("SELECT * FROM bars WHERE date BETWEEN ? AND ? ORDER BY date, streak DESC",
                        (dates[0], dates[-1]))
    meta = {r["code"]: r for r in db.query("SELECT code,name,sector FROM stocks")}
    by_date = {}
    for b in rows_all:
        m = meta.get(b["code"], {})
        b["name"] = m.get("name", b["code"])
        b["sector"] = m.get("sector", "")
        by_date.setdefault(b["date"], []).append(b)
    out = []
    for d in dates:
        bars = by_date.get(d, [])
        best = None
        for b in bars:
            v = (b.get("streak") or 0) * 100 + (b["pct"] if b.get("limit_up") else 0)
            if b.get("streak", 0) >= 3 and (best is None or b["streak"] > best["streak"]):
                best = b
        if best:
            out.append({"date": d, "code": best["code"], "name": best["name"],
                        "sector": best["sector"], "streak": best["streak"],
                        "limit": bool(best.get("limit_up"))})
        else:
            out.append({"date": d, "code": None, "name": None, "sector": None,
                        "streak": 0, "limit": False})
    return {"rows": out}


# ---------------------------------------------------------------- pools & signals
@router.get("/pools")
def pools():
    v = _view()
    if not v:
        raise HTTPException(503, "数据尚未就绪")
    return {
        "date": v["date"], "phase": v["phase"]["phase_cn"], "mode": mode_info(),
        "buyang": v["pools"].get("buyang"),
        "qiehuan": v["pools"].get("qiehuan"),
        "note": ("股票池为规则初筛，仅供研究；题材判断与最终决策需结合新闻/盘面人工复核。"
                 + ("实盘模式下新闻接口未接入，S-05 等新闻依赖条件暂为中性。" if DATA_SOURCE == "real" else "")),
    }


@router.get("/signals")
def signals():
    v = _view()
    if not v:
        raise HTTPException(503, "数据尚未就绪")
    return {
        "date": v["date"], "mode": mode_info(),
        "items": v["signals"]["items"],
        "count": v["signals"]["count"],
        "note": "信号由规则引擎自动生成：\"买入/卖出/观察\"仅表示条件触发，不构成投资建议。",
    }


@router.get("/search")
def search(q: str = Query("", max_length=20), limit: int = Query(20, le=60)):
    q = q.strip().upper()
    if DATA_SOURCE == "real":
        from ..real import market as real_mkt
        quotes = real_mkt.snapshot().get("quotes") or {}
        items = []
        for c, qq in quotes.items():
            if q and q not in c and q.lower() not in qq["name"].lower():
                continue
            items.append({"code": c, "name": qq["name"],
                          "sector": real_mkt.industry_of(c) or "未分类"})
            if len(items) >= limit:
                break
        return {"items": items}
    rows = db.query("SELECT code,name,sector FROM stocks")
    if q:
        rows = [r for r in rows if q in r["code"] or q.lower() in r["name"].lower()]
    return {"items": rows[:limit]}
