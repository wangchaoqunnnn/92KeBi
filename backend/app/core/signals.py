"""买卖信号生成（需求 C-05 / 规则引擎）。输入：个股特征+盘面上下文 → 结构化信号。"""
from .text import SIGNAL_DIR_CN

STRATEGY_CN = {"leader": "龙头战法", "buyang": "补涨战法", "qiehuan": "切换战法", "generic": "通用"}


def _mk(code, f, strategy, name, dir_, strength, reason, ctx, score=None):
    return {
        "code": code, "name": f["name"], "sector": f["sector"],
        "strategy": strategy, "strategy_cn": STRATEGY_CN[strategy],
        "signal": name, "dir": dir_, "dir_cn": SIGNAL_DIR_CN[dir_],
        "strength": strength, "reason": reason, "date": ctx["date"], "score": score,
        "price": f["price"], "pct_today": f["today"]["pct"],
        "turnover": f["today"].get("turnover"),
    }


def _sector_fade(ctx, f):
    """板块跟风溃散/亏钱效应扩散检查"""
    if f["sector_dt_today"] >= 2:
        return f"板块内跌停 {f['sector_dt_today']} 家, 亏钱效应扩散"
    if f["sector_avg_pct"] <= -2.5:
        return f"板块平均 {f['sector_avg_pct']}%, 集体走弱"
    return None


def leader_signals(f, ctx):
    """龙头战法买入/卖出信号（L系买入条件+卖出纪律）"""
    out = []
    t, p, code = f["today"], f.get("prev"), f["code"]
    streak = t.get("streak") or 0
    prev_streak = (p.get("streak") or 0) if p else 0
    turn, avg5 = t.get("turnover") or 0, f["turnover_avg5"] or 0
    gap = (t["open"] / t["pre_close"] - 1) * 100 if t["pre_close"] else 0
    limit = bool(t.get("limit_up"))
    phase = ctx["phase"]

    if phase not in ("main_ascend", "high_oscillate"):
        return out
    if streak == 0 and prev_streak == 0:
        return out  # 无板态不评估龙头买卖点(持有/观察逻辑在通用段)

    # ---- 买入 ----
    if limit:
        if p and not p.get("limit_up") and p["pct"] <= 3.5 and p["high"] >= p["pre_close"] * 1.09 \
                and 0 <= gap <= 6 and not f["one_word_today"]:
            out.append(_mk(code, f, "leader", "弱转强买入", "buy", "强",
                           f"昨日冲板未遂({p['pct']}%), 今日低开高走叠量涨停({gap:.1f}%高开) → 弱转强, 优先", ctx))
        elif streak >= 2 and avg5 > 0 and turn >= min(2 * avg5, 30) and prev_streak <= 1:
            out.append(_mk(code, f, "leader", "分歧买入", "buy", "强",
                           f"首次爆量分歧日: 换手 {turn}%(5日均 {avg5}%)放量涨停 → 看好,分歧,买入", ctx))
        elif streak >= 3 and turn < avg5 * 0.6:
            out.append(_mk(code, f, "leader", "一致缩量(不追)", "watch", "警示",
                           f"第{streak}板缩量一致({turn}%<5日均{avg5}%), 买点已过, 追高违背纪律", ctx))
    # ---- 卖出/警示 ----
    fade = _sector_fade(ctx, f)
    if prev_streak >= 3 and not limit and t["pct"] <= -1.0:
        out.append(_mk(code, f, "leader", "龙头断板即撤", "sell", "强",
                       f"昨日{prev_streak}板龙头今日断板({t['pct']}%) → 断板即离场信号", ctx))
    if t["turnover"] and f["turnover_max60"] and t["turnover"] >= 0.8 * f["turnover_max60"]:
        out.append(_mk(code, f, "leader", "流动性预警", "sell", "警示",
                       f"换手 {t['turnover']}% 达 60 日峰值 {f['turnover_max60']}% 的 80% → 流动性陷阱风险", ctx))
    if p and p.get("limit_up") and not limit and gap >= 3 and t["pct"] <= -2.0:
        out.append(_mk(code, f, "leader", "一致转分歧(高开出货)", "sell", "中",
                       f"昨日涨停今日高开 {gap:.1f}% 后回落 {t['pct']}% → 卖在一致转分歧", ctx))
    if fade:
        out.append(_mk(code, f, "leader", "板块跟风溃散", "sell", "中", fade, ctx))
    return out


def buyang_signals(f, ctx, in_pool=False):
    """补涨战法信号。in_pool=True 表示该股已在补涨池(同题材低位)"""
    out = []
    t, p, code = f["today"], f.get("prev"), f["code"]
    streak = t.get("streak") or 0
    prev_streak = (p.get("streak") or 0) if p else 0
    limit = bool(t.get("limit_up"))
    turn = t.get("turnover") or 0
    phase = ctx["phase"]
    dragon = ctx.get("dragon")
    in_topic = bool(dragon and f["sector"] == dragon["sector"])

    if phase not in ("main_ascend", "high_oscillate"):
        return out

    if in_topic and not in_pool:
        return out

    if limit and streak == 1:
        out.append(_mk(code, f, "buyang", "低位首板确认", "buy", "中",
                       f"低位首板涨停, 题材与主线({dragon['sector'] if dragon else '-'})一致 → 补涨买点", ctx))
    elif limit and streak == 2 and prev_streak == 1 and turn >= 6 and not f["one_word_today"]:
        out.append(_mk(code, f, "buyang", "一进二换手确认", "buy", "中",
                       f"昨日首板今日放量换手二板(换手{turn}%) → 确认补涨地位, 可跟进", ctx))
    if streak >= 3:
        out.append(_mk(code, f, "buyang", "加速即卖", "sell", "中",
                       f"已{streak}连板, 补涨股往往直接一致加速 → 加速后及时止盈, 不格局", ctx))
    if p and p.get("limit_up") and not limit and t["pct"] <= -4:
        out.append(_mk(code, f, "buyang", "不及预期即走", "sell", "中",
                       f"昨板今砸({t['pct']}%) → 走势不及预期立即离场", ctx))
    fade = _sector_fade(ctx, f)
    if fade and (in_pool or streak >= 1):
        out.append(_mk(code, f, "buyang", "板块退潮离场", "sell", "中", fade, ctx))
    if dragon and p and not limit and (p.get("streak") or 0) >= 3 and streak == 0 and in_pool:
        out.append(_mk(code, f, "buyang", "龙头倒下, 逻辑失效", "sell", "强",
                       f"主线龙头({dragon['name']})断板 → 补涨逻辑失效, 清仓", ctx))
    return out


def qiehuan_signals(f, ctx):
    """切换战法信号"""
    out = []
    t, p, code = f["today"], f.get("prev"), f["code"]
    streak = t.get("streak") or 0
    limit = bool(t.get("limit_up"))
    phase = ctx["phase"]
    run60 = f["run60_pct"] or 0
    gap = (t["open"] / t["pre_close"] - 1) * 100 if t["pre_close"] else 0

    if phase != "probe":
        return out
    if limit and streak == 1 and run60 <= 30:
        strength = "强" if f["sector_avg_pct"] > 1.5 else "中"
        out.append(_mk(code, f, "qiehuan", "新题材首板跟随", "buy", strength,
                       f"低位首板涨停(60日涨幅{run60:.0f}%)、板块指数({f['sector_avg_pct']}%)走强 → 预判/跟随新周期",
                       ctx))
    elif streak == 0 and p and p.get("limit_up"):
        if t["pct"] <= -2:
            out.append(_mk(code, f, "qiehuan", "不及预期即走", "sell", "强",
                           f"昨首板今回落 {t['pct']}% → 试错失败, 立即割肉不犹豫", ctx))
        elif gap < 0 and t["pct"] < 0:
            out.append(_mk(code, f, "qiehuan", "题材未发酵, 离场", "sell", "中",
                           f"新题材未能获得市场认可(低开 {gap:.1f}%) → 及时离场", ctx))
    if streak >= 2:
        out.append(_mk(code, f, "qiehuan", "试错连板(谨慎持有)", "watch", "警示",
                       "已2板, 切换仓位本就轻仓试错, 谨防情绪反复", ctx))
    return out


def for_stock(code, f, ctx):
    """个股信号汇总（详情页）：按股票自身形态 + 是否在池/是否总龙判断。"""
    sigs = []
    dragon = ctx.get("dragon")
    is_dragon = bool(dragon and dragon["code"] == code)
    pool_codes = set()
    pools = ctx.get("pools") or {}
    for pkey in ("buyang", "qiehuan"):
        pv = pools.get(pkey)
        if pv:
            pool_codes.update(i["code"] for i in pv["items"])
    if is_dragon or f["streak_max20"] >= 3:
        sigs += leader_signals(f, ctx)
    if code in pool_codes or (dragon and f["sector"] == dragon["sector"]):
        sigs += buyang_signals(f, ctx, in_pool=(code in pool_codes))
    sigs += qiehuan_signals(f, ctx)
    # 通用止损纪律提示
    p = f.get("prev")
    if p and p["pct"] <= -5:
        sigs.append(_mk(code, f, "generic", "触及止损纪律", "sell", "警示",
                        "上一交易日下跌超过5% → 单笔止损线为-5%, 无条件执行", ctx))
    return sigs


def collect(ctx, pools):
    """汇总当前全部候选信号的看板"""
    out = []
    dragon = ctx.get("dragon")
    if dragon:
        f = ctx["feats"][dragon["code"]]
        out += leader_signals(f, ctx)
        # 高位震荡中 断板龙头给总龙层面的警示
        if ctx["phase"] in ("high_oscillate",) and not f["today"].get("limit_up") and f["streak_max20"] >= 4:
            pass
    for key in ("buyang", "qiehuan"):
        pv = pools.get(key)
        if not pv:
            continue
        for it in pv["items"]:
            f = ctx["feats"][it["code"]]
            if key == "buyang":
                out += buyang_signals(f, ctx, in_pool=True)
            else:
                out += qiehuan_signals(f, ctx)
    seen = set()
    uniq = []
    for s in out:
        k = (s["code"], s["signal"], s["dir"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
    order = {"buy": 0, "watch": 1, "sell": 2}
    uniq.sort(key=lambda s: (order.get(s["dir"], 3), -({"强": 3, "中": 2, "警示": 1}.get(s["strength"], 0))))
    return {
        "asof": ctx["date"], "items": uniq,
        "count": {"buy": sum(1 for s in uniq if s["dir"] == "buy"),
                  "sell": sum(1 for s in uniq if s["dir"] == "sell"),
                  "watch": sum(1 for s in uniq if s["dir"] == "watch")},
    }
