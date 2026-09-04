"""回测引擎（需求 U-06）。
在历史行情上滚动执行“情绪周期 → 模式选择 → 买/卖信号”，全部信号只用当日及以前数据，
成交按信号日收盘价并计单边 0.15% 成本；止损按日内低点触发。
（简化约定：打板信号当日收盘成交，未模拟一字无法买入等微观约束，结果仅作策略研究参考。）
"""
from .. import db
from ..core import board, phase
from ..core.text import PHASE_CN
from ..config import DATA_SOURCE, RISK

STRATEGY_CN = {"leader": "龙头战法", "buyang": "补涨战法", "qiehuan": "切换战法", "generic": "通用"}

MODE_PHASE_MAP = {  # 用户选择的模式 → 仅在对应阶段出手
    "auto": ["main_decline", "probe", "main_ascend", "high_oscillate"],
    "leader": ["main_ascend"],
    "buyang": ["high_oscillate"],
    "qiehuan": ["probe"],
}


def _load(hist):
    """hist 简化视图：dates, bars_by_date, stocks"""
    dates = hist["dates"]
    rows = hist["bars"]
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    return dates, by_date


def run_backtest(start, end, capital=100000.0, mode="auto", seed_days=70):
    meta = {r["code"]: r for r in db.query("SELECT code,name,sector FROM stocks")}
    bars = db.query("SELECT * FROM bars ORDER BY date ASC")
    for b in bars:
        m = meta.get(b["code"], {})
        b["name"] = m.get("name", b["code"])
        b["sector"] = m.get("sector", "")
    hist = _load({
        "dates": [r["date"] for r in db.query("SELECT DISTINCT date FROM bars ORDER BY date ASC")],
        "bars": bars,
        "stocks": db.query("SELECT * FROM stocks"),
    })
    dates, by_date = hist
    if not end:
        end = dates[-1]

    # 实盘模式：样本池统计按“样本→全市场”折算后送入情绪引擎(阈值=REAL_RULE)
    if DATA_SOURCE == "real":
        try:
            from ..real import market as real_mkt
            st_snap = real_mkt.snapshot()
            whole = (st_snap.get("mkt_stats") or {}).get("universe", 0)
            n = int(db.meta_get("sample_n", 0))
            k = round(max(1.5, whole / n), 2) if whole and n else 1.0
        except Exception:
            k = 1.0
    else:
        k = 1.0

    def scale_counts(d):
        if k == 1.0:
            return d
        dd = dict(d)
        for key in ("zt_count", "dt_count", "up_count", "down_count"):
            dd[key] = int(round(d[key] * k))
        return dd

    s_i = next((i for i, d in enumerate(dates) if d >= start), None)
    e_i = next((i for i, d in enumerate(dates) if d > end), len(dates)) - 1
    if s_i is None or e_i is None or e_i - s_i < 10:
        return {"error": "日期范围无效或无足够数据", "trades": [], "equity": [], "stats": {}}
    i0 = max(0, s_i - seed_days)  # 回看窗(仅用于统计,不出信号)
    dates = dates[i0:e_i + 1]

    fees = RISK["fees_rate"]
    cash = capital
    holdings = []  # dict: code,name,sector,strategy,shares,entry_px,entry_date,days,max_px
    trades = []
    equity_curve = []
    prev_stats, prev_label = None, None
    fees_paid = 0.0

    def stock_map(ti):
        return {b["code"]: b for b in by_date[dates[ti]]}

    def news_sectors_recent(ti, days=4):
        out = {}
        for i in range(ti, max(0, ti - days) - 1, -1):
            for n in news_rows:
                if n["date"] == dates[i] and n.get("sector") and n.get("sentiment", 0) > 0:
                    out[n["sector"]] = out.get(n["sector"], 0) + 1
        return out

    news_rows = db.query("SELECT * FROM news ORDER BY date ASC")
    nrows_by_date = {}
    for n in news_rows:
        nrows_by_date.setdefault(n["date"], []).append(n)

    # ------- 预计算: 稠密数组(收盘/换手) 与 近25日连板峰值窗口 -------
    n_days = len(dates)
    closes, tos = {}, {}          # code -> [close/turnover] 对齐 ti (None=缺)
    deq = {}                      # code -> deque 近25日streak
    mem_last = {}                 # code -> 最近一次出现连板的 ti
    from collections import deque
    for ti in range(n_days):
        for b in by_date[dates[ti]]:
            c = b["code"]
            arr = closes.get(c)
            if arr is None:
                closes[c] = [None] * n_days
                tos[c] = [None] * n_days
                deq[c] = deque(maxlen=25)
                arr = closes[c]
            arr[ti] = b["close"]
            tos[c][ti] = b.get("turnover")
            st = b.get("streak") or 0
            if st:
                deq[c].append(st)
                mem_last[c] = ti
            else:
                deq[c].append(0)

    def _run60(code, ti):
        arr = closes.get(code)
        if not arr or ti < 60 or arr[ti] is None or arr[ti - 60] in (None, 0):
            return 0.0
        return (arr[ti] / arr[ti - 60] - 1) * 100

    def _tomax(code, ti):
        arr = tos.get(code)
        if not arr:
            return 0.0
        return max([x for x in arr[max(0, ti - 60):ti + 1] if x is not None] or [0.0])

    def runner_peak(ti):
        """近25日窗口内连板峰值最高的标的 (code, streak)"""
        best = None
        for code, last in mem_last.items():
            if ti - last > 25:
                continue
            q = deq.get(code)
            if not q:
                continue
            v = max(q)
            if v >= 3 and (best is None or v > best[1] or (v == best[1] and last > mem_last[best[0]])):
                best = (code, v)
        return best

    for ti in range(seed_days, len(dates)):
        d = dates[ti]
        bars_t = by_date[d]
        bars_p = by_date[dates[ti - 1]] if ti >= 1 else None
        st = board.compute_day_stats(bars_t, bars_p)
        st["date"] = d
        label, conf, _ = phase.classify_day(scale_counts(st), scale_counts(prev_stats) if prev_stats else None,
                                            prev_label)
        prev_label, prev_stats = label, st
        sm = stock_map(ti)
        phase_ok = label in MODE_PHASE_MAP[mode]

        # ===== 1) 卖出 =====
        still = []
        for h in holdings:
            b = sm.get(h["code"])
            if not b:
                still.append(h)
                continue
            h["days"] += 1
            h["max_px"] = max(h["max_px"], b["high"])
            exit_px, reason = None, None
            # 止损(盘中触发)
            if b["low"] <= h["entry_px"] * (1 - RISK["stop_loss"]):
                exit_px, reason = h["entry_px"] * (1 - RISK["stop_loss"]), "止损(-5%)"
            else:
                h_code = h["code"]
                if h["strategy"] == "leader":
                    if not b.get("limit_up") and b["pct"] <= -1.0:
                        exit_px, reason = b["close"], "龙头断板/转弱离场"
                    elif (b.get("turnover") or 0) >= 0.8 * max(_tomax(h_code, ti), 0.01):
                        exit_px, reason = b["close"], "流动性预警(换手过峰值80%)"
                    elif h["days"] >= 15:
                        exit_px, reason = b["close"], "持有超15日, 时间止损"
                elif h["strategy"] == "buyang":
                    if b.get("streak") and b["streak"] >= 3:
                        exit_px, reason = b["close"], "加速一致即卖"
                    elif not b.get("limit_up") and b["pct"] <= -4:
                        exit_px, reason = b["close"], "不及预期即走"
                    elif h["days"] >= 8:
                        exit_px, reason = b["close"], "补涨持有上限8日"
                elif h["strategy"] == "qiehuan":
                    if not b.get("limit_up") and b["pct"] <= -1.0:
                        exit_px, reason = b["close"], "不及预期即走(试错)"
                    elif h["days"] >= 6:
                        exit_px, reason = b["close"], "试错观察期到, 未发酵离场"
                else:
                    if h["days"] >= 10:
                        exit_px, reason = b["close"], "通用时间止损"
            if exit_px is not None:
                val = h["shares"] * exit_px
                fee = val * fees
                cash += val - fee
                fees_paid += fee
                pnl = (exit_px - h["entry_px"]) / h["entry_px"] - 2 * fees
                trades.append({"code": h["code"], "name": h["name"], "strategy": h["strategy"],
                               "strategy_cn": STRATEGY_CN[h["strategy"]],
                               "entry_date": h["entry_date"], "exit_date": d,
                               "entry_px": round(h["entry_px"], 2), "exit_px": round(exit_px, 2),
                               "shares": h["shares"], "pnl_pct": round(pnl * 100, 2),
                               "pnl_cash": round((val - h["cost"]) - fee, 2), "reason": reason,
                               "hold_days": h["days"]})
            else:
                still.append(h)
        holdings = still

        # ===== 2) 买入 =====
        invested = sum(h["shares"] * sm[h["code"]]["close"] for h in holdings if h["code"] in sm)
        eq_now = cash + invested
        cap_frac = {"main_decline": 0.0, "probe": 0.2, "main_ascend": 0.9,
                    "high_oscillate": 0.1}.get(label, 0.1)
        room = eq_now * cap_frac - invested
        buys = []
        if phase_ok and room > eq_now * 0.02 and ti >= seed_days + 2:
            run = runner_peak(ti)
            zt_rows = [b for b in bars_t if b.get("limit_up")]
            if label == "main_ascend" and mode in ("auto", "leader"):
                zt_rows.sort(key=lambda b: -(b.get("streak") or 0))
                for b in zt_rows:
                    code = b["code"]
                    streak = b.get("streak") or 0
                    if streak >= 2 and 5 <= (b.get("turnover") or 0) <= 28 \
                            and not b.get("one_word") and _run60(code, ti) >= 15 and streak <= 6:
                        if not any(h["code"] == code for h in holdings):
                            buys.append({"code": code, "strategy": "leader",
                                         "name": b["name"], "sector": b["sector"],
                                         "signal": f"主升买龙头(第{streak}板)"})
                        break
            elif label == "high_oscillate" and mode in ("auto", "buyang"):
                if run:
                    run_code = run[0]
                    rb = sm.get(run_code)
                    if rb and not rb.get("limit_up") and rb["pct"] <= -1.0:
                        dsec = rb["sector"]
                        cand = [b for b in zt_rows if b["sector"] == dsec
                                and (b.get("streak") or 0) == 1 and b["code"] != run_code
                                and _run60(b["code"], ti) <= 60]
                        cand.sort(key=lambda b: _run60(b["code"], ti))
                        for b in cand[:2]:
                            if not any(h["code"] == b["code"] for h in holdings):
                                buys.append({"code": b["code"], "strategy": "buyang",
                                             "name": b["name"], "sector": dsec,
                                             "signal": "高位震荡低位补涨首板"})
            elif label == "probe" and mode in ("auto", "qiehuan"):
                old_sec = None
                if run and sm.get(run[0]):
                    old_sec = sm[run[0]]["sector"]
                for b in zt_rows:
                    code = b["code"]
                    if (b.get("streak") or 0) == 1 and _run60(code, ti) <= 30 \
                            and (not old_sec or b["sector"] != old_sec):
                        if not any(h["code"] == code for h in holdings):
                            buys.append({"code": code, "strategy": "qiehuan",
                                         "name": b["name"], "sector": b["sector"],
                                         "signal": "试错期新题材首板"})
                        if len(buys) >= 2:
                            break
            # 主跌不买(空仓纪律)；高osc/decline 用较小仓位
            held_codes = {h["code"] for h in holdings}
            new_cap = min(room, max(0.0, eq_now * (cap_frac if cap_frac > 0.01 else 0.0) - invested))
            slots = [b for b in buys if b["code"] not in held_codes]
            if slots and new_cap > eq_now * 0.02:
                per = min(new_cap / len(slots), eq_now * RISK["single_position_max"])
                for b in slots:
                    bar = sm[b["code"]]
                    px = bar["close"] * (1 + fees)
                    if px <= 0:
                        continue
                    shares = int(per / px / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * px
                    if cost > cash:
                        shares = int(cash / px / 100) * 100
                        cost = shares * px
                    if shares <= 0:
                        continue
                    cash -= cost
                    fees_paid += cost * fees / (1 + fees)
                    holdings.append({"code": b["code"], "name": b["name"], "sector": b["sector"],
                                     "strategy": b["strategy"], "shares": shares,
                                     "entry_px": bar["close"], "cost": cost,
                                     "entry_date": d, "days": 0, "max_px": bar["close"],
                                     "signal": b["signal"]})

        # ===== 3) 净值曲线 =====
        invested = sum(h["shares"] * sm[h["code"]]["close"] for h in holdings if h["code"] in sm)
        eq = cash + invested
        avg_mkt = sum(b["pct"] for b in bars_t) / len(bars_t)
        equity_curve.append({"date": d, "equity": round(eq, 2), "phase": label,
                             "phase_cn": PHASE_CN[label],
                             "holdings": len(holdings),
                             "benchmark_ret": round(avg_mkt, 2),
                             "cash": round(cash, 2)})

    # 期末平仓
    last_sm = stock_map(len(dates) - 1)
    for h in holdings:
        b = last_sm[h["code"]]
        exit_px = b["close"]
        val = h["shares"] * exit_px
        cash += val - val * fees
        pnl = (exit_px - h["entry_px"]) / h["entry_px"] - 2 * fees
        trades.append({"code": h["code"], "name": h["name"], "strategy": h["strategy"],
                       "strategy_cn": STRATEGY_CN[h["strategy"]],
                       "entry_date": h["entry_date"], "exit_date": b["date"],
                       "entry_px": round(h["entry_px"], 2), "exit_px": round(exit_px, 2),
                       "shares": h["shares"], "pnl_pct": round(pnl * 100, 2),
                       "pnl_cash": round(val - h["cost"], 2), "reason": "期末了结",
                       "hold_days": h["days"]})
    holdings = []

    return _summarize(equity_curve, trades, capital, mode, start, end)


def _summarize(curve, trades, capital, mode, start, end):
    if len(curve) < 2:
        return {"error": "样本不足", "trades": trades, "equity": curve, "stats": {}}
    eqs = [c["equity"] for c in curve]
    final = eqs[-1]
    total_ret = final / capital - 1
    n_days = len(curve)
    ann = (1 + total_ret) ** (252 / n_days) - 1 if total_ret > -1 else -1
    peak, max_dd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e)
        max_dd = min(max_dd, e / peak - 1)
    wins = [t for t in trades if t["pnl_cash"] > 0]
    losses = [t for t in trades if t["pnl_cash"] <= 0]
    gross_w = sum(t["pnl_cash"] for t in wins)
    gross_l = abs(sum(t["pnl_cash"] for t in losses))
    win_rate = len(wins) / len(trades) if trades else 0
    pf = gross_w / gross_l if gross_l > 0 else (99 if gross_w > 0 else 0)
    # 基准：等权持有(每日市场均值累计)
    bm = 1.0
    bm_curve = []
    for c in curve:
        bm *= (1 + c["benchmark_ret"] / 100)
        bm_curve.append({"date": c["date"], "value": round(bm * 100, 2)})
    first = curve[0]
    base = 100.0
    eq_curve = [{"date": c["date"], "value": round(c["equity"] / first["equity"] * base, 3)}
                for c in curve]
    dd_curve = []
    pk = 0.0
    for c in eq_curve:
        pk = max(pk, c["value"])
        dd_curve.append({"date": c["date"], "value": round((c["value"] / pk - 1) * 100, 2)})
    by_strategy = {}
    for t in trades:
        by_strategy.setdefault(t["strategy_cn"], []).append(t)
    strat_stats = {k: {"n": len(v), "win": round(sum(1 for t in v if t["pnl_cash"] > 0) / len(v) * 100, 1),
                       "pnl_sum": round(sum(t["pnl_cash"] for t in v), 2)} for k, v in by_strategy.items()}
    return {"trades": trades, "equity": eq_curve, "benchmark": bm_curve, "drawdown": dd_curve,
            "phases": [{"date": c["date"], "phase": c["phase_cn"]} for c in curve],
            "stats": {
                "mode": mode, "start": start, "end": end, "days": n_days,
                "init_capital": capital, "final_equity": round(final, 2),
                "total_ret_pct": round(total_ret * 100, 2),
                "annual_ret_pct": round(ann * 100, 2),
                "max_drawdown_pct": round(max_dd * 100, 2),
                "win_rate_pct": round(win_rate * 100, 1),
                "profit_factor": round(min(pf, 99), 2),
                "trade_count": len(trades),
                "gross_profit": round(gross_w, 2), "gross_loss": round(gross_l, 2),
                "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0,
                "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0,
                "by_strategy": strat_stats,
            }}
