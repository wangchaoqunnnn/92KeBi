"""风控与仓位规则（需求 C-06 + 通用风控表）。"""
from ..config import PHASE_POSITION, RISK

RULES = [
    {"name": "单票仓位 ≤25%", "text": "不能把账户压在一只票上, 小资金也要分仓"},
    {"name": "永远不加杠杆", "text": "放大杠杆无异于赌博"},
    {"name": "止损线 -5%", "text": "单笔亏损≥5%无条件止损, 不及预期就割肉"},
    {"name": "杀伐果断", "text": "走势不及预期立即走, 犹豫不决是交易大忌"},
    {"name": "空仓是美德", "text": "主跌期必须空仓, 空仓本身就是一种策略"},
    {"name": "稳定复利", "text": "靠稳定模式与复利, 而非重仓豪赌"},
]

PHASE_CAP = {"main_decline": 0.10, "probe": 0.20, "main_ascend": 0.90, "high_oscillate": 0.10}
PHASE_MODE_TIP = {
    "main_decline": "空仓为主; 尾盘可轻仓博弈修复(仅作观察)",
    "probe": "小仓试错新题材首板(切换战法)",
    "main_ascend": "分歧买龙头, 敢于重仓核心 (不做中位跟风/不追一致高潮/不格局杂毛)",
    "high_oscillate": "轻仓补涨应对, 不博弈穿越",
}


def position_plan(phase_label, conf=None, rec_buys=None):
    """根据情绪阶段输出建议仓位与候选个股分配（占总资金比例）。"""
    phase = phase_label
    cap = PHASE_CAP[phase]
    rec = rec_buys if rec_buys is not None else []
    # 仅采纳"买入"信号候选
    pool = [r for r in rec if r.get("dir") == "buy"][:4] if rec else []
    alloc = []
    if pool and cap > 0.01:
        per = min(RISK["single_position_max"], cap / len(pool))
        for i, r in enumerate(pool):
            alloc.append({"code": r["code"], "name": r["name"], "sector": r["sector"],
                          "pct": round(per * 100, 1),
                          "sig": r.get("signal"), "strength": r.get("strength")})
    return {
        "phase": phase,
        "conf": conf,
        "cap_label": PHASE_POSITION[phase]["pct"],
        "cap_frac": cap,
        "mode_tip": PHASE_MODE_TIP[phase],
        "single_max_pct": int(RISK["single_position_max"] * 100),
        "stop_loss_pct": int(RISK["stop_loss"] * 100),
        "allocations": alloc,
        "rules": RULES,
    }
