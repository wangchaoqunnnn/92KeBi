"""分析编排层：加载行情窗口 → 盘面统计 → 情绪判定 → 龙头识别 → 股票池/信号。
供 API 与回测共用，保证“展示即策略、策略即回测”同一套计算。"""
import statistics

from . import db
from .core import board, phase, leaders, pools, signals, risk
from .core.text import PHASE_CN
from .seed.universe import SECTORS

LOOKBACK = 60  # 特征窗口


def fetch_hist(upto_date=None, days_back=LOOKBACK + 8):
    """取 upto_date(含) 之前最近 days_back 个交易日行情 + 元数据 + 近期新闻。"""
    dates = [r["date"] for r in db.query(
        "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT ?", (days_back,))]
    dates.reverse()
    if upto_date:
        dates = [d for d in dates if d <= upto_date]
    if not dates:
        return None
    lo, hi = dates[0], dates[-1]
    rows = db.query("SELECT * FROM bars WHERE date>=? AND date<=? ORDER BY date, code", (lo, hi))
    stocks = {}
    for r in db.query("SELECT * FROM stocks"):
        stocks[r["code"]] = {**r, "sector_policy": bool(SECTORS[r["sector_idx"]]["policy"]),
                             "sector_heat": SECTORS[r["sector_idx"]]["heat"],
                             "sector_keywords": SECTORS[r["sector_idx"]]["keywords"]}
    by_date = {}
    for r in rows:
        m = stocks.get(r["code"], {})
        r["name"] = m.get("name", r["code"])
        r["sector"] = m.get("sector", "")
        r["sector_idx"] = m.get("sector_idx", 0)
        by_date.setdefault(r["date"], []).append(r)
    day_bars = [by_date[d] for d in dates]

    news = db.query("SELECT * FROM news WHERE date>=? ORDER BY date DESC LIMIT 400", (lo,))
    return {"dates": dates, "day_bars": day_bars, "stocks": stocks, "news": news}


def news_maps(news):
    by_code, by_sector = {}, {}
    for n in news:
        if n.get("code"):
            by_code.setdefault(n["code"], []).append(n)
        if n.get("sector"):
            by_sector.setdefault(n["sector"], []).append(n)
    return by_code, by_sector


def _ma(xs, w):
    if len(xs) < w:
        return None
    return sum(xs[-w:]) / w


def stock_feats_map(hist):
    """为最后一日计算全市场每只股票的量化特征（供选股/信号/详情页共用）。"""
    day_bars = hist["day_bars"]
    today_bars = day_bars[-1]
    prev_bars = day_bars[-2] if len(day_bars) >= 2 else []
    stocks = hist["stocks"]
    by_code, by_sector = news_maps(hist["news"])

    # 各股收盘/最高/涨跌序列
    closes, highs, lows, pcts = {}, {}, {}, {}
    for bars in day_bars:
        for b in bars:
            c = closes.setdefault(b["code"], [])
            c.append(b["close"])
            highs.setdefault(b["code"], []).append(b["high"])
            lows.setdefault(b["code"], []).append(b["low"])
            pcts.setdefault(b["code"], []).append(b["pct"])

    prev_map = {b["code"]: b for b in prev_bars}
    zt_codes_5d = {}
    for bars in day_bars[-5:]:
        for b in bars:
            if b.get("limit_up"):
                zt_codes_5d.setdefault(b["sector"], []).append(b["code"])

    sector_stats = {s["sector"]: s for s in board.compute_sector_stats(
        today_bars, prev_bars, days_zt=zt_codes_5d)}

    feats = {}
    for b in today_bars:
        code = b["code"]
        cl = closes.get(code, [])
        hx = highs.get(code, [])
        lo = lows.get(code, [])
        p = pcts.get(code, [])
        meta = stocks.get(code, {})
        close = b["close"]
        run20 = (close / cl[-(20 + 1)] - 1) * 100 if len(cl) >= 21 else None
        run60 = (close / cl[-(60 + 1)] - 1) * 100 if len(cl) >= 61 else None
        run60 = run60 if run60 is not None else ((close / cl[0] - 1) * 100 if cl and cl[0] else 0)
        high60 = max(hx[-60:]) if len(hx) >= 5 else max(hx) if hx else close
        low60 = min(lo[-60:]) if len(lo) >= 5 else min(lo) if lo else close
        dist_high60 = (high60 / close - 1) * 100 if close else 0
        gain_low60 = (close / low60 - 1) * 100 if low60 else 0
        pp = p[-20:] if p else []
        vol20 = statistics.pstdev(pp) if len(pp) >= 5 else 0
        turnover_hist = [x["turnover"] for bars in day_bars[-60:] for x in bars if x["code"] == code]
        flat_days = sum(1 for bars in day_bars[-40:] for x in bars
                        if x["code"] == code and -3 < x["pct"] < 3) / 40.0 if len(day_bars) >= 40 else 0.3
        streaks = [x["streak"] for bars in day_bars[-20:] for x in bars if x["code"] == code]
        news_code = [n for n in by_code.get(code, [])]
        sec_news = by_sector.get(b["sector"], [])
        sec_stat = sector_stats.get(b["sector"], {})
        pb = prev_map.get(code)
        feats[code] = {
            "code": code, "name": b["name"], "sector": b["sector"], "sector_idx": b["sector_idx"],
            "today": b, "prev": pb,
            "close": close, "price": close,
            "float_cap": meta.get("float_cap", 20), "pe": meta.get("pe"),
            "run20_pct": run20, "run60_pct": run60,
            "dist_high60_pct": round(dist_high60, 2), "gain_low60_pct": round(gain_low60, 2),
            "vol20_pct": round(vol20, 2), "flat_ratio40": round(flat_days, 2),
            "streak_max20": max(streaks) if streaks else 0,
            "turnover_avg5": round(statistics.mean(turnover_hist[-5:]), 2) if len(turnover_hist) >= 2 else 0,
            "turnover_max60": round(max(turnover_hist), 2) if turnover_hist else 0,
            "seal_ratio": round((b.get("amount") or 0) / max(meta.get("float_cap", 1), 0.01), 4),
            "news_code_n": len(news_code), "news_sector_n": len(sec_news),
            "sector_avg_pct": sec_stat.get("avg_pct", 0), "sector_zt_5d": len(zt_codes_5d.get(b["sector"], [])),
            "sector_zt_today": sec_stat.get("zt_today", 0), "sector_dt_today": sec_stat.get("dt_today", 0),
            "one_word_today": int(b.get("one_word") or (b.get("limit_up") and b["open"] >= b["close"] * 0.998)),
        }
    return feats, prev_bars, today_bars


def build_stats_series(hist):
    """逐日盘面统计（供情绪判定与趋势图）。"""
    day_bars = hist["day_bars"]
    dates = hist["dates"]
    stats = []
    for i, bars in enumerate(day_bars):
        s = board.compute_day_stats(bars, day_bars[i - 1] if i > 0 else None)
        s["date"] = dates[i]
        stats.append(s)
    return stats


def analyze(hist, today_date=None):
    """核心：对 hist 的最后一日做完整分析，返回 view dict。"""
    stats_series = build_stats_series(hist)
    phase_series = phase.classify_series(stats_series)
    last_ps = phase_series[-1]
    phase_label = last_ps["phase"]
    today_stats = stats_series[-1]
    prev_stats = stats_series[-2] if len(stats_series) > 1 else None

    feats, prev_bars, today_bars = stock_feats_map(hist)
    stocks = hist["stocks"]
    dates = hist["dates"]

    # 阶段原因 + 建议
    advice = phase.advice_for_phase(phase_label)
    advice["confidence"] = last_ps["conf"]

    # 龙头识别
    leader_view = leaders.identify(feats, today_stats, prev_stats, phase_label,
                                   {c: stocks[c] for c in feats})

    # 盘面背景 ctx
    ctx = {
        "phase": phase_label, "phase_cn": PHASE_CN[phase_label],
        "phase_series": phase_series,
        "today_stats": today_stats, "prev_stats": prev_stats,
        "stats_series": stats_series, "feats": feats, "stocks": stocks,
        "hist": hist, "date": dates[-1],
        "dragon": leader_view["dragon"], "leaders": leader_view,
        "leaders_pool": leader_view["pool"],
    }

    pool_view = pools.identify(ctx)
    sig_view = signals.collect(ctx, pool_view)
    ctx["pools"] = pool_view
    ctx["signals"] = sig_view
    from . import market_cache
    market_cache.remember(ctx)

    return {
        "date": dates[-1],
        "phase": {**advice, "reasons": last_ps["reasons"], "conf": last_ps["conf"],
                  "phase_cn": PHASE_CN[phase_label]},
        "stats": today_stats,
        "stats_history": [{"date": s["date"], "phase": phase_series[i]["phase"],
                           "zt": s["zt_count"], "dt": s["dt_count"], "max_streak": s["max_streak"],
                           "premium": s["premium_end"], "explosion": s["explosion"],
                           "mean_pct": s["mean_pct"], "up": s["up_count"], "down": s["down_count"],
                           "amount": s["amount_sum"]} for i, s in enumerate(stats_series)],
        "leaders": leader_view,
        "pools": pool_view,
        "signals": sig_view,
    }


def analyze_today():
    """以最新行情日做一次分析（api/任务共用，带内存缓存）。"""
    from . import market_cache
    return market_cache.get_view()
