"""文本与格式化小工具（中文原因描述等）。"""
from datetime import datetime

PHASE_CN = {
    "main_decline": "主跌阶段",
    "probe": "低位震荡/试错期",
    "main_ascend": "主升阶段",
    "high_oscillate": "高位震荡",
}

PHASE_DESC = {
    "main_decline": "亏钱效应主导，高位股A杀、昨日涨停股溢价率转负，跌停家数连续放大。空仓是美德，保护本金等待冰点。",
    "probe": "主跌之后情绪冰点修复，新题材开始萌芽轮动，昨日涨停股出现正溢价。小仓试错新题材首板，为下一轮主线做准备。",
    "main_ascend": "主线确立、赚钱效应最强，龙头持续领涨、连板梯队完整。分歧买龙头，敢于重仓核心，不做中位跟风。",
    "high_oscillate": "龙头滞涨/断板，中位股开始出现亏钱效应，资金高低切换。轻仓做补涨，不博弈穿越，随时准备撤退。",
}

PHASE_COLOR = {
    "main_decline": "#ef4444",
    "probe": "#f59e0b",
    "main_ascend": "#22c55e",
    "high_oscillate": "#eab308",
}

STRATEGY_CN = {"leader": "龙头战法", "buyang": "补涨战法", "qiehuan": "切换战法", "rest": "空仓休息"}

SIGNAL_DIR_CN = {"buy": "买入", "sell": "卖出", "watch": "观察/警示"}


def fmt_num(x, nd=2):
    if x is None:
        return "-"
    return f"{x:.{nd}f}"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
