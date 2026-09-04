"""实时实盘分析编排：基于真实样本池(成交额前N)历史 + 全市场实时快照,
生成与 mock 模式同构的 view(前端无需感知差异):
- stats/stats_history: 按“样本→全市场密度折算 + 今日全市场精确值”给出情绪序列
- 今日由实时快照合成“今日K线”, 涨停/跌停/连板/溢价/炸板均为真实口径
- 龙头/补涨/切换/信号/仓位复用 core 引擎(题材类辅助评分做真实化近似)
"""
import logging
import statistics

from .. import db
from ..config import REAL_CRAWL_N
from ..core import board, phase, leaders, pools, signals, risk
from ..core.text import PHASE_CN
from ..providers import router, sina
from . import market as real_mkt

log = logging.getLogger("kb.real.analyze")


def _stock_meta_map():
    """样本池股票元数据 + 真实化题材字段"""
    c2i = real_mkt.get_industry_cache().get("code2industry", {})
    ind_heat = {}
    st = real_mkt.snapshot()
    qmap = st.get("quotes") or {}
    for ind, codes in real_mkt.get_industry_cache().get("industries", {}).items():
        zt_n = 0
        for c in codes:
            qq = qmap.get(c)
            if qq and qq.get("zt"):
                zt_n += 1
        ind_heat[ind] = zt_n
    out = {}
    for r in db.query("SELECT * FROM stocks"):
        ind = c2i.get(r["code"], r.get("sector") or "")
        heat = 45 + min(45, ind_heat.get(ind, 0) * 6)
        policy = real_mkt.policy_hard(r["code"], r["name"])
        out[r["code"]] = {**r, "sector": ind or r.get("sector") or "未分类",
                          "sector_idx": 0, "tags": ind,
                          "sector_heat": min(100, heat),
                          "sector_policy": policy,
                          "sector_keywords": [ind] if ind else [r["name"]],
                          "float_cap": r.get("float_cap") or 0}
    return out


def _load_hist(stocks):
    """样本池真实日K → hist(含今日合成K线)。仅纳入样本池股票的K线。"""
    rows = [b for b in db.query("SELECT * FROM bars ORDER BY date ASC") if b["code"] in stocks]
    if not rows:
        return None
    dates, by_date = [], {}
    for b in rows:
        dates.append(b["date"])
        by_date.setdefault(b["date"], []).append(b)
    dates = sorted(set(dates))
    if not dates:
        return None
    # 注入 name/sector 等展示字段
    for d in dates:
        for b in by_date[d]:
            m = stocks.get(b["code"], {})
            b["name"] = m.get("name", b["code"])
            b["sector"] = m.get("sector") or b.get("sector") or ""
            b["sector_idx"] = m.get("sector_idx", 0)
            b["float_cap"] = m.get("float_cap") or 0

    quote_date = real_mkt.snapshot().get("quote_date") or ""
    if quote_date and quote_date > dates[-1]:
        # 今日实时合成日线(仅样本池)
        qmap = real_mkt.snapshot().get("quotes") or {}
        ladder = real_mkt.snapshot().get("today_ladder") or {}
        synth = []
        prev_lookup = {}
        for b in by_date[dates[-1]]:
            prev_lookup[b["code"]] = b["close"]
        for code, m in stocks.items():
            qq = qmap.get(code)
            if not qq or qq["price"] is None or qq["price"] <= 0:
                continue
            settle = qq["settlement"] or prev_lookup.get(code) or qq["price"]
            zt, dt = qq.get("zt"), qq.get("dt")
            streak = (ladder.get(code) or 0) if zt else 0
            one_word = int(bool(zt and qq["open"] and settle and
                                qq["open"] >= round(settle * (1 + qq["rate"]), 2) - 1e-6))
            pct = qq["pct"] if qq["pct"] is not None else 0.0
            synth.append({
                "date": quote_date, "code": code, "name": m["name"],
                "sector": m.get("sector") or "", "sector_idx": 0,
                "open": qq["open"] or settle, "high": qq["high"] or qq["price"],
                "low": qq["low"] or qq["price"], "close": qq["price"],
                "pre_close": settle, "pct": round(pct, 2),
                "turnover": qq["turnover"] or 0,
                "amount": round((qq["amount"] or 0) / 1e8, 4),
                "volume": qq["volume"] or 0,
                "streak": streak, "limit_up": int(zt), "limit_down": int(dt),
                "one_word": one_word,
            })
        if synth:
            dates.append(quote_date)
            by_date[quote_date] = synth
    return {"dates": dates, "day_bars": [by_date[d] for d in dates],
            "stocks": stocks, "news": []}


def _market_scale():
    """样本→全市场折算系数(用于历史序列换算)"""
    st = real_mkt.snapshot()
    whole = (st.get("mkt_stats") or {}).get("universe", 0)
    n = int(db.meta_get("sample_n", 0)) or REAL_CRAWL_N
    if whole <= 0 or n <= 0:
        return 10.0
    return round(max(1.5, whole / n), 2)


def _index_pct_map():
    """上证指数日涨跌幅(历史代理) day->pct"""
    out = {}
    rows = real_mkt.get_index_kline(560)
    prev = None
    for r in rows:
        if prev:
            pct = round((r["close"] / prev - 1) * 100, 2)
            out[r["day"]] = pct
        prev = r["close"]
    return out


def feat_for_code(code):
    """任意个股(不必在样本池)的量化特征: 实时报价 + 真实日K + 行业聚合"""
    import statistics as _st
    qmap = real_mkt.snapshot().get("quotes") or {}
    qq = qmap.get(code)
    meta = _stock_meta_map().get(code) or {}
    if not meta and qq:
        ind = real_mkt.industry_of(code)
        meta = {"code": code, "name": qq["name"], "sector": ind or "未分类", "sector_idx": 0,
                "tags": ind, "float_cap": (qq.get("nmc") or 0) / 1e4 or 0,
                "pe": qq.get("per"), "sector_heat": 50,
                "sector_policy": real_mkt.policy_hard(code, qq["name"]),
                "sector_keywords": [ind] if ind else [qq["name"]]}
    if not qq and not meta:
        return None
    try:  # 日K: 新浪主/腾讯备(前复权)
        rows = router.fetch_kline_any(code, n=260)
    except Exception:
        rows = []
    closes = [r["close"] for r in rows]
    lows = [r["low"] for r in rows]
    highs = [r["high"] for r in rows]
    closes_all = closes
    price = (qq or {}).get("price") or (closes[-1] if closes else 0)
    if closes and (qq or {}).get("settlement"):
        closes_all = closes + [price]  # 今日实时并入用于近端指标
    def _pct_arr(xs):
        out = []
        for i in range(1, len(xs)):
            if xs[i - 1]:
                out.append(round((xs[i] / xs[i - 1] - 1) * 100, 2))
        return out
    pcts = _pct_arr(closes_all)
    run60 = round((price / closes_all[-(60 + 1)] - 1) * 100, 2) if len(closes_all) >= 61 else 0.0
    run20 = round((price / closes_all[-(20 + 1)] - 1) * 100, 2) if len(closes_all) >= 21 else 0.0
    w = closes_all[-60:]
    high60 = max(w + ([qq["high"]] if qq and qq.get("high") else []))
    low60 = min(lows[-60:] + ([qq["low"]] if qq and qq.get("low") else []))
    vol20 = round(_st.pstdev(pcts[-20:]), 2) if len(pcts) >= 10 else 0
    flat = sum(1 for x in pcts[-40:] if -3 < x < 3) / max(len(pcts[-40:]), 1)
    streaks = []
    name = meta.get("name", "")
    rate = sina.limit_rate(code, name)
    streak = 0
    for i in range(len(closes) - 1, -1, -1):
        pre = closes[i - 1] if i > 0 else None
        if pre and sina.is_limit_up(closes[i], pre, rate):
            streak += 1
        else:
            break
    if qq and qq.get("zt"):
        streak = max(streak, 1)
    # 行业聚合(实时)
    ind = meta.get("sector") or ""
    ind_rows = real_mkt.industry_stats_full()
    sec_stat = next((r for r in ind_rows if r["sector"] == ind), {})
    sec_news_n = 0
    feat = {
        "code": code, "name": meta.get("name", code), "sector": ind, "sector_idx": 0,
        "close": price, "price": price,
        "float_cap": meta.get("float_cap") or 0, "pe": meta.get("pe"),
        "run20_pct": run20, "run60_pct": run60,
        "dist_high60_pct": round((high60 / price - 1) * 100, 2) if price else 0,
        "gain_low60_pct": round((price / low60 - 1) * 100, 2) if low60 else 0,
        "vol20_pct": vol20, "flat_ratio40": round(flat, 2),
        "streak_max20": streak,
        "turnover_avg5": 0.0, "turnover_max60": 0.0,
        "seal_ratio": round(((qq or {}).get("amount") or 0) / 1e8 / max(meta.get("float_cap") or 1, 0.01), 4),
        "news_code_n": 0, "news_sector_n": sec_news_n,
        "sector_avg_pct": sec_stat.get("avg_pct", 0),
        "sector_zt_5d": sec_stat.get("zt_5d") or sec_stat.get("zt_today", 0),
        "sector_zt_today": sec_stat.get("zt_today", 0),
        "sector_dt_today": sec_stat.get("dt_today", 0),
        "one_word_today": int(bool(qq and qq.get("zt") and qq.get("open") and qq.get("settlement")
                                   and qq["open"] >= round(qq["settlement"] * (1 + qq.get("rate", 0.1)), 2) - 1e-6)),
        "today": {
            "code": code, "name": meta.get("name", code), "sector": ind,
            "open": (qq or {}).get("open") or price, "high": (qq or {}).get("high") or price,
            "low": (qq or {}).get("low") or price, "close": price,
            "pre_close": (qq or {}).get("settlement") or (closes[-1] if closes else price),
            "pct": round((qq or {}).get("pct") or 0, 2),
            "turnover": (qq or {}).get("turnover") or 0,
            "amount": round(((qq or {}).get("amount") or 0) / 1e8, 4),
            "volume": (qq or {}).get("volume") or 0,
            "streak": streak, "limit_up": int(bool(qq and qq.get("zt"))),
            "limit_down": int(bool(qq and qq.get("dt"))),
        },
        "prev": None,
    }
    return feat, meta


def detail_view(code):
    """个股详情页(实盘): 结构与 mock stock_detail 同构"""
    from .. import market_cache
    from ..core import pools, signals as sig_mod
    base_ctx = market_cache.get_ctx()
    if not base_ctx or "phase" not in base_ctx:
        # 缓存空窗兜底: 从(可能存在的)视图还原上下文骨架
        v = market_cache.get_view(max_age=600) or {}
        ph = v.get("phase") or {}
        qd = real_mkt.snapshot().get("quote_date") or ""
        base_ctx = {
            "phase": ph.get("phase") or "probe", "phase_cn": ph.get("phase_cn") or "试错期",
            "date": v.get("date") or qd,
            "today_stats": v.get("stats") or {"date": qd},
            "stats_series": v.get("stats_history") or [],
            "feats": {}, "stocks": {}, "hist": {"news": []},
            "dragon": (v.get("leaders") or {}).get("dragon") or None,
        }
    res = feat_for_code(code)
    if not res:
        return None
    feat, meta = res
    ind_rows = real_mkt.industry_stats_full()
    ctx = dict(base_ctx)
    ctx["feats"] = {**ctx.get("feats", {}), code: feat}
    ctx["stocks"] = {**ctx.get("stocks", {}), code: meta}
    ctx["today_stats"] = ctx.get("today_stats") or {"date": real_mkt.snapshot().get("quote_date")}
    ctx.setdefault("hist", {"news": []})
    ctx.setdefault("stats_series", [])
    eval_res = pools.eval_stock(code, ctx)
    sigs = sig_mod.for_stock(code, feat, ctx)
    news = []
    return {"meta": meta, "feat": feat, "conds": eval_res, "signals": sigs,
            "sector_stats": ind_rows, "news": news,
            "date": real_mkt.snapshot().get("quote_date")}


def analyze_real():
    """构建与 analyze() 同构的 view(实时口径)"""
    stocks = _stock_meta_map()
    hist = _load_hist(stocks)
    if not hist:
        return None

    # ---------- 盘面统计序列(样本原始) ----------
    raw_stats = []
    day_bars = hist["day_bars"]
    for i, bars in enumerate(day_bars):
        s = board.compute_day_stats(bars, day_bars[i - 1] if i > 0 else None)
        s["date"] = hist["dates"][i]
        raw_stats.append(s)

    # ---------- 折算到全市场口径 ----------
    k = _market_scale()
    idx = _index_pct_map()
    full = real_mkt.snapshot()
    mkt = full.get("mkt_stats") or {}
    ladder_map = full.get("today_ladder") or {}
    mkt_ladder = {}
    for c, stk in ladder_map.items():
        mkt_ladder[stk] = mkt_ladder.get(stk, 0) + 1
    today_stats = {
        "date": full.get("quote_date") or hist["dates"][-1],
        "zt_count": mkt.get("zt", 0), "dt_count": mkt.get("dt", 0),
        "up_count": mkt.get("up", 0), "down_count": mkt.get("down", 0),
        "mean_pct": idx.get(full.get("quote_date")) or mkt.get("mean_pct"),
        "amount_sum": mkt.get("amount_yi", 0),
        "volume": mkt.get("volume"),
        "max_streak": max(ladder_map.values()) if ladder_map else 0,
        "ladder": mkt_ladder,
        "premium_end": full.get("premium_end"),
        "premium_open": full.get("premium_open"),
        "explosion": full.get("explosion", 0.0),
        "zt_codes": [x["code"] for x in full.get("today_zt", [])],
        "dt_codes": [x["code"] for x in full.get("today_dt", [])],
        "dragon_broken": False, "mid_loss": False, "prev_zt_n": len(full.get("yesterday_zt", [])),
    }
    scaled = []
    for i, s in enumerate(raw_stats):
        if hist["dates"][i] == today_stats["date"]:
            continue
        d = dict(s)
        d["zt_count"] = int(round(s["zt_count"] * k))
        d["dt_count"] = int(round(s["dt_count"] * k))
        d["up_count"] = int(round(s["up_count"] * k))
        d["down_count"] = int(round(s["down_count"] * k))
        d["mean_pct"] = idx.get(s["date"])
        scaled.append(d)
    if scaled and scaled[-1]["date"] == today_stats["date"]:
        pass
    else:
        scaled.append(today_stats)

    phase_series = phase.classify_series(scaled)
    last_ps = phase_series[-1]
    phase_label = last_ps["phase"]
    advice = phase.advice_for_phase(phase_label)
    advice["confidence"] = last_ps["conf"]

    # ---------- 龙头/池/信号(基于样本池 feats) ----------
    from .. import analyze as an
    feats, _, _ = an.stock_feats_map(hist)
    today_d = hist["dates"][-1]
    ctx = {
        "phase": phase_label, "phase_cn": PHASE_CN[phase_label],
        "phase_series": phase_series,
        "today_stats": today_stats, "prev_stats": scaled[-2] if len(scaled) > 1 else None,
        "stats_series": scaled, "feats": feats, "stocks": stocks,
        "hist": hist, "date": today_d, "real": True,
    }
    leader_view = leaders.identify(feats, today_stats, ctx["prev_stats"], phase_label, stocks)
    ctx["dragon"] = leader_view["dragon"]
    ctx["leaders"] = leader_view
    ctx["leaders_pool"] = leader_view["pool"]
    pool_view = pools.identify(ctx)
    sig_view = signals.collect(ctx, pool_view)
    ctx["pools"] = pool_view
    ctx["signals"] = sig_view
    # 供个股详情/信号复用(与 mock 模式一致)
    from .. import market_cache
    market_cache.remember(ctx)

    return {
        "date": today_d,
        "phase": {**advice, "reasons": last_ps["reasons"], "conf": last_ps["conf"],
                  "phase_cn": PHASE_CN[phase_label]},
        "stats": today_stats,
        "stats_history": [{"date": x["date"], "phase": phase_series[i]["phase"],
                           "zt": x["zt_count"], "dt": x["dt_count"], "max_streak": x["max_streak"],
                           "premium": x["premium_end"], "explosion": x["explosion"],
                           "mean_pct": x["mean_pct"], "up": x["up_count"], "down": x["down_count"],
                           "amount": x["amount_sum"]} for i, x in enumerate(scaled)],
        "leaders": leader_view,
        "pools": pool_view,
        "signals": sig_view,
        "market": {**mkt, "scale": k, "sample_n": int(db.meta_get("sample_n", 0))},
    }

