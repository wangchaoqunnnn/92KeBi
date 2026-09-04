"""市场/盘面统计：给定某日全部个股 bar 与前一交易日 bar，计算情绪盘面指标。"""
from collections import Counter


def _pre_map(prev_bars):
    """prev 日 bar 快速检索: code -> bar"""
    if not prev_bars:
        return {}
    return {b["code"]: b for b in prev_bars}


def compute_day_stats(today_bars, prev_bars=None):
    """返回某交易日盘面统计 dict（用于情绪判定与展示）。"""
    prev = _pre_map(prev_bars)
    zt_codes, dt_codes = [], []
    up = down = 0
    pct_sum = 0.0
    amt_sum = 0.0
    ladder = Counter()
    attempts = exploded = 0
    prev_zt_gaps, prev_zt_pcts = [], []   # 昨日涨停股 今日开盘溢价 / 收盘表现
    dragon_broken = False
    mid_loss = False
    hot_break = None

    # 昨日连板>=3 的最高标（用于“龙头断板”启发式）
    prev_dragons = [b for b in prev_bars or [] if b["streak"] >= 3]

    for b in today_bars:
        code = b["code"]
        pct = b["pct"]
        pct_sum += pct
        amt_sum += b.get("amount", 0) or 0
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        if b.get("limit_up"):
            zt_codes.append(code)
            ladder[b["streak"]] += 1
        if b.get("limit_down"):
            dt_codes.append(code)
        # 炸板统计：盘中触及涨停区(>=9.5%) 未封住
        limit_zone = b["high"] >= b["pre_close"] * 1.095
        if limit_zone:
            attempts += 1
            if not b.get("limit_up"):
                exploded += 1
        # 昨日涨停股今日表现
        pb = prev.get(code)
        if pb and pb.get("limit_up"):
            prev_zt_pcts.append(pct)
            gap = (b["open"] / pb["close"] - 1) * 100 if pb["close"] else 0
            prev_zt_gaps.append(gap)
        # 龙头断板启发式：昨日最高标(simulated 4+)今日大幅回落且未涨停
        if pb and pb.get("streak", 0) >= 4 and not b.get("limit_up") and pct <= -3:
            dragon_broken = True
            hot_break = {"code": code, "name": b["name"], "prev_streak": pb["streak"]}
        # 中位股亏钱启发式：昨日 2-3 连板今日大跌超 -6.5%
        if pb and 2 <= pb.get("streak", 0) < 4 and not b.get("limit_up") and pct <= -6.5:
            mid_loss = True

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    max_streak = max(ladder.keys()) if ladder else 0
    # 昨日涨停股今日“平均表现(收盘溢价)”
    premium_end = _avg(prev_zt_pcts)
    premium_open = _avg(prev_zt_gaps)
    n = len(today_bars) or 1
    return {
        "zt_count": len(zt_codes), "dt_count": len(dt_codes),
        "up_count": up, "down_count": down,
        "mean_pct": round(pct_sum / n, 2),
        "amount_sum": round(amt_sum, 2),
        "max_streak": max_streak,
        "ladder": {int(k): int(v) for k, v in sorted(ladder.items())},
        "zt_codes": zt_codes, "dt_codes": dt_codes,
        "premium_open": premium_open, "premium_end": premium_end,
        "explosion": round(exploded / attempts * 100, 1) if attempts else 0.0,
        "dragon_broken": dragon_broken, "mid_loss": mid_loss,
        "hot_break": hot_break,
        "prev_zt_n": len(prev_zt_pcts),
    }


def compute_sector_stats(today_bars, prev_bars=None, days_zt=None):
    """板块聚合：涨跌幅均值、板块内今日涨停、5日涨停、成交额。
    days_zt: {sector_name: [code,...]} 近5日各板块涨停代码（可选）。
    """
    ag = {}
    for b in today_bars:
        sec = b["sector"]
        d = ag.setdefault(sec, {"pct_sum": 0.0, "n": 0, "zt_today": 0, "amt": 0.0, "dt_today": 0, "up": 0})
        d["pct_sum"] += b["pct"]
        d["n"] += 1
        d["amt"] += b.get("amount", 0) or 0
        if b.get("limit_up"):
            d["zt_today"] += 1
        if b.get("limit_down"):
            d["dt_today"] += 1
        if b["pct"] > 0:
            d["up"] += 1
    rows = []
    for sec, d in ag.items():
        n = max(d["n"], 1)
        rows.append({
            "sector": sec,
            "avg_pct": round(d["pct_sum"] / n, 2),
            "zt_today": d["zt_today"], "dt_today": d["dt_today"],
            "amount": round(d["amt"], 2),
            "up_ratio": round(d["up"] / n * 100, 0),
            "zt_5d": len(days_zt.get(sec, [])) if days_zt else None,
        })
    rows.sort(key=lambda r: -r["avg_pct"])
    return rows
