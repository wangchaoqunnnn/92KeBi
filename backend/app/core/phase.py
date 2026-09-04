"""情绪周期判定（需求 C-01，规则引擎）。
输入：连续若干交易日的盘面统计(口径：涨停/跌停/涨跌家数、连板、昨日涨停溢价、炸板率、市场均值)，
输出 主跌 / 试错(低位震荡) / 主升 / 高位震荡 之一 + 置信度 + 依据。

设计为“刻度无关”：
- mock(48只虚构小市场) 使用 config.RULE(每48只刻度阈值)
- 实盘样本折算到全市场口径后使用 config.REAL_RULE(全市场5000+刻度阈值)
计数类证据按“相对阈值”归一化，状态机(Markov)按 主跌→试错→主升→高位震荡 演进。
"""
from .text import PHASE_CN, PHASE_DESC
from ..config import RULE as _MOCK_RULE, REAL_RULE as _REAL_RULE

_ACTIVE = None  # None=mock规则; dict=实盘规则


def set_rule(rule):
    """实盘模式注入阈值表(通常 REAL_RULE)"""
    global _ACTIVE
    _ACTIVE = rule


def reset_rule():
    global _ACTIVE
    _ACTIVE = None


def _r(name, default=None):
    rule = _ACTIVE if _ACTIVE is not None else _MOCK_RULE
    if name in rule:
        return rule[name]
    return default


def _clip(x, lo=0.0, hi=1.2):
    return max(lo, min(hi, x))


def _rel(x, thr):
    """相对阈值归一(0起, 超过1.5×thr封顶1)"""
    if not thr:
        return 1.0 if x > 0 else 0.0
    if x <= thr:
        return max(0.0, x / thr)
    return min(1.0, 0.6 + 0.4 * min(1.0, (x - thr) / thr))


def _e_decline(st, p):
    """主跌证据 0..1.2"""
    e = 0.0
    thr = _r("dt_decline_min", 6)
    dt_t, dt_p = st["dt_count"], (p or {}).get("dt_count", 0)
    if dt_t >= thr:
        e += 0.6 + 0.35 * _rel(dt_t, thr)
    if dt_p >= thr:
        e += 0.45
    prem = st.get("premium_end")
    if prem is not None and prem <= 0:
        e += 0.3
    if st.get("mean_pct") is not None and st["mean_pct"] <= -1.2:
        e += 0.3
    if st.get("dragon_broken") and (prem is not None and prem <= -0.8):
        e += 0.3
    if dt_t >= thr * 0.4 and st.get("mean_pct") is not None and st["mean_pct"] <= -1.8:
        e += 0.45
    return e


def _e_ascend(st, p):
    e = 0.0
    thr_zt = _r("zt_boom_min", 8)
    thr_ld = _r("ladder_ascend_min", 4)
    zt = st["zt_count"]
    if zt >= thr_zt:
        e += 0.6 + 0.35 * _rel(zt, thr_zt)
    elif zt >= thr_zt * 0.45:
        e += 0.25
    if st["max_streak"] >= thr_ld:
        e += 0.45 + 0.3 * _rel(st["max_streak"], thr_ld)
    elif st["max_streak"] >= max(2, thr_ld - 2):
        e += 0.15
    prem = st.get("premium_end")
    if prem is not None and prem >= 1.5:
        e += 0.3
    if st.get("mean_pct") is not None and st["mean_pct"] >= _r("mean_strong", 0.8):
        e += 0.25
    if st["dt_count"] >= _r("dt_decline_min", 6) * 0.5:
        e -= 0.7
    if st["dt_count"] >= _r("dt_decline_min", 6) * 0.8:
        e -= 0.4
    return max(0.0, e)


def _reasons(st, p):
    r = []
    thr_dt = _r("dt_decline_min", 6)
    thr_zt = _r("zt_boom_min", 8)
    if st["dt_count"]:
        r.append(f"跌停 {st['dt_count']} 家")
    if p and p["dt_count"] >= thr_dt:
        r.append(f"昨日跌停 {p['dt_count']} 家")
    if st["zt_count"]:
        r.append(f"涨停 {st['zt_count']} 家")
    else:
        r.append("今日无涨停(情绪冰点)")
    if st["max_streak"] >= 3:
        r.append(f"最高 {st['max_streak']} 连板")
    if st["premium_end"] is not None:
        r.append(f"昨涨停今表现 {st['premium_end']}%")
    if st["premium_open"] is not None and st["premium_open"] >= 2:
        r.append(f"昨涨停今高开 {st['premium_open']}%")
    if st["explosion"] and st["explosion"] >= 35:
        r.append(f"炸板率 {st['explosion']}%")
    if st.get("dragon_broken"):
        r.append("高位龙头断板回落")
    if st.get("mid_loss"):
        r.append("中位股出现亏钱效应")
    if st.get("mean_pct") is not None:
        r.append(f"上证指数 {st['mean_pct']}%(涨{st['up_count']}/跌{st['down_count']})")
    if not r:
        r.append("盘面平淡")
    return r[:6]


def classify_day(st, p, prev_label):
    """单日分类(状态机)。返回 (label, conf, reasons)"""
    de = _e_decline(st, p)
    ae = _e_ascend(st, p)
    reasons = _reasons(st, p)
    thr_dt = _r("dt_decline_min", 6)
    hard_decline = bool(st["dt_count"] >= thr_dt and p and p["dt_count"] >= thr_dt)

    label = None
    if hard_decline or de >= 1.0:
        label = "main_decline"
    elif prev_label in (None,):
        # 冷启动：按盘面直接归类
        if st["zt_count"] >= _r("zt_boom_min", 8) and st["max_streak"] >= _r("ladder_ascend_min", 4):
            label = "main_ascend"
        elif st["dt_count"] >= thr_dt:
            label = "main_decline"
        elif st["zt_count"] >= 1:
            label = "probe"
        else:
            label = "main_decline"
    elif prev_label == "main_decline":
        # 主跌结束即进入试错(低位震荡)修复
        label = "probe"
        if ae >= 1.3 and st["max_streak"] >= _r("ladder_ascend_min", 4) and st["dt_count"] <= thr_dt * 0.25:
            label = "main_ascend"  # 强修复直接转主升
    elif prev_label in ("main_ascend", "high_oscillate"):
        if ae >= 1.15:
            label = "main_ascend"      # 再度加速(晋级延续)
        else:
            label = "high_oscillate"   # 高度回落/滞涨/钝化 → 高位震荡
    elif prev_label == "probe":
        if ae >= 1.0:
            label = "main_ascend"
        else:
            label = "probe"

    # 置信度：状态延续高置信；边界日略降
    conf = 0.86
    if label == "main_decline":
        conf = min(0.96, 0.72 + _rel(st["dt_count"], thr_dt) * 0.2 +
                   (0.1 if st.get("premium_end") is not None and st["premium_end"] < 0 else 0))
    elif label == "main_ascend":
        conf = min(0.96, 0.6 + _rel(st["zt_count"], _r("zt_boom_min", 8)) * 0.25 +
                   min(0.15, st["max_streak"] * 0.02))
    elif label == "probe":
        conf = 0.78 + (0.08 if (st.get("premium_end") or 0) >= 1 else 0) + (0.06 if st["zt_count"] >= 1 else 0)
        conf = min(0.94, conf)
    elif label == "high_oscillate":
        conf = 0.8 - min(0.2, (ae - de) * 0.2)
    conf = round(max(0.45, min(0.97, conf)), 2)
    return label, conf, reasons


def classify_series(stats_list):
    """对一段连续日期的盘面统计逐日判定，返回附加结构。"""
    out, prev_label = [], None
    for i, st in enumerate(stats_list):
        p = stats_list[i - 1] if i > 0 else None
        label, conf, reasons = classify_day(st, p, prev_label)
        prev_label = label
        out.append({"date": st["date"], "phase": label, "conf": conf,
                    "reasons": reasons, "phase_cn": PHASE_CN[label]})
    return out


def advice_for_phase(phase):
    """阶段 → 仓位区间/模式建议（数据来自 config.PHASE_POSITION）"""
    from ..config import PHASE_POSITION
    m = PHASE_POSITION.get(phase, PHASE_POSITION["probe"])
    return {
        "phase": phase,
        "label": PHASE_CN.get(phase, phase),
        "desc": PHASE_DESC.get(phase, ""),
        "position_range_pct": m["pct"],
        "mode_text": m["mode"],
        "confidence": None,
    }
