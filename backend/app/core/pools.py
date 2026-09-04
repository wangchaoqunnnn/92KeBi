"""补涨/切换股票池（需求 C-03/C-04；条件 B-01..B-06、S-01..S-05）。
纯规则评分，输出结构化候选 + 理由，便于前端展示与人工复核。
"""
from ..seed.universe import SECTORS

# ----------------------------- 通用小工具 -----------------------------
def _cap(x, hi=100.0):
    return min(hi, max(0.0, x))


def _score_b3(f):
    """三低原则(B-03)：低价/低流通盘/低估值"""
    s = 0.0
    price = f["price"]
    s += 40 if price <= 12 else 28 if price <= 20 else 16 if price <= 32 else 4
    fc = f.get("float_cap") or 50
    s += 40 if fc <= 25 else 28 if fc <= 40 else 15 if fc <= 60 else 4
    pe = f.get("pe")
    if pe is None:
        s += 20
    elif 0 < pe <= 45:
        s += 20
    elif pe <= 0:
        s += 6
    else:
        s += 8
    return _cap(s)


def _news_recent(ctx, code=None, sector=None, max_days=5, pos_only=False):
    """统计近 max_days(交易日近似按日期) 新闻条数；pos_only 仅正向。"""
    cnt = 0
    cur = ctx["date"]
    for n in ctx["hist"]["news"]:
        if (code and n.get("code") == code) or (sector and n.get("sector") == sector and not code):
            if n["date"] > cur:
                continue
            # 简单按“交易日差”过滤：用日期字符串差值近似
            y1, m1, d1 = map(int, cur.split("-"))
            y2, m2, d2 = map(int, n["date"].split("-"))
            days = (y1 - y2) * 372 + (m1 - m2) * 31 + (d1 - d2)
            if 0 <= days <= max_days * 1.6:
                if not pos_only or (n.get("sentiment") or 0) > 0:
                    cnt += 1
    return cnt


def _dragon_ctx(ctx):
    """当前主线板块/龙头信息（兼容龙头已断板情形）"""
    dragon = ctx.get("dragon")
    if dragon:
        return {"sector": dragon["sector"], "code": dragon["code"], "name": dragon["name"],
                "score": dragon.get("score"), "broken_days": 0}
    # 龙头断板后：取近 20 日最高辨识度股票作为“主升线”锚
    best = None
    for c, f in ctx["feats"].items():
        v = f["streak_max20"] * 10 + min(f["run60_pct"] or 0, 80) * 0.5
        if best is None or v > best[1]:
            best = (c, v, f)
    if best and best[1] >= 30:
        f = best[2]
        return {"sector": f["sector"], "code": f["code"], "name": f["name"], "score": best[1],
                "broken_days": None}
    return None


def _broken_recency(ctx):
    """距最近一次“高位龙头断板”的交易日数（无则 None）"""
    sts = ctx["stats_series"]
    for i in range(len(sts) - 1, -1, -1):
        if sts[i].get("dragon_broken"):
            return len(sts) - 1 - i
    return None


def buyang_conds(f, ctx):
    """补涨战法逐条评估 → (总分, items[dict], reasons[])"""
    dragon = _dragon_ctx(ctx)
    phase = ctx["phase"]
    items, reasons = [], []
    same_sector = bool(dragon and f["sector"] == dragon["sector"])

    # B-01 时机正确
    rec = _broken_recency(ctx)
    if phase in ("main_ascend", "high_oscillate") and dragon:
        s1, ok1 = 80, True
        n1 = f"主线({dragon['sector']})赚钱效应仍在" + (f"；龙头断板第 {rec} 日，补涨窗口打开" if rec is not None and rec <= 6 else "")
    else:
        s1, ok1 = 15, False
        n1 = f"当前阶段({ctx['phase_cn'] if 'phase_cn' in ctx else phase})非补涨适用阶段"
    items.append({"id": "B-01", "name": "时机正确", "ok": ok1, "score": s1, "note": n1})
    if ok1:
        reasons.append(n1)

    # B-02 同题材（硬门槛）
    s2 = 100 if same_sector else 0
    items.append({"id": "B-02", "name": "同题材", "ok": same_sector, "score": s2,
                  "note": f"与主线({dragon['sector'] if dragon else '-'})同一题材/逻辑发散" if same_sector
                  else "与当前主线非同题材"})
    if same_sector:
        reasons.append("同题材: 与主线板块一致")

    # B-03 三低
    s3 = _score_b3(f)
    ok3 = s3 >= 60
    items.append({"id": "B-03", "name": "三低原则", "ok": ok3, "score": s3,
                  "note": f"股价 {f['price']:.1f} 元 / 流通 {f.get('float_cap', 0):.0f}亿 / PE {f.get('pe')}"})
    if ok3:
        reasons.append("三低: 低价低流通, 估值不高")

    # B-04 低位启动
    run60 = f["run60_pct"] or 0
    s4 = 0.0
    if f["streak_max20"] <= 2:
        s4 += 30
    if run60 <= 45:
        s4 += 35 - max(0, run60 - 15)
    if f["gain_low60_pct"] <= 25:
        s4 += 20
    if f["streak_max20"] == 1:
        s4 += 15
    s4 = _cap(s4)
    ok4 = f["streak_max20"] <= 2 and run60 <= 60
    items.append({"id": "B-04", "name": "低位启动", "ok": ok4, "score": round(s4, 1),
                  "note": f"60日涨幅 {run60:.0f}%、距60日低点 {f['gain_low60_pct']:.0f}%"
                          + ("；首板/二板内启动" if f["streak_max20"] <= 2 else "；连板数偏高")})
    if ok4:
        reasons.append("低位启动: 首板/二板区间, 60日涨幅有限")

    # B-05 图形良好
    s5 = 0.0
    if f["vol20_pct"] <= 4.5:
        s5 += 45
    elif f["vol20_pct"] <= 6.5:
        s5 += 25
    s5 += f["flat_ratio40"] * 40
    if f["dist_high60_pct"] <= 7:
        s5 += 15
    s5 = _cap(s5)
    ok5 = f["vol20_pct"] <= 6.5 and f["flat_ratio40"] >= 0.55 and f["dist_high60_pct"] <= 9
    items.append({"id": "B-05", "name": "图形良好", "ok": ok5, "score": round(s5, 1),
                  "note": f"20日波动 {f['vol20_pct']}%、40日盘整占比 {f['flat_ratio40'] * 100:.0f}%、距60日高 {f['dist_high60_pct']:.1f}%"})
    if ok5:
        reasons.append("图形: 长期盘整, 临近突破无压力位")

    # B-06 题材贴合
    meta = ctx["stocks"].get(f["code"], {})
    heat = meta.get("sector_heat", 50)
    sec_n = _news_recent(ctx, sector=f["sector"])
    s6 = _cap((heat - 45) * 1.2 + sec_n * 6)
    ok6 = heat >= 65
    items.append({"id": "B-06", "name": "题材贴合", "ok": ok6, "score": round(s6, 1),
                  "note": f"板块热度 {heat}/100" + (f"，近期板块新闻 {sec_n} 条" if sec_n else "")})
    if ok6:
        reasons.append("题材贴合主线热点")

    total = _cap(0.15 * s1 + 0.2 * s2 + 0.18 * s3 + 0.19 * s4 + 0.13 * s5 + 0.15 * s6)
    return round(total, 1), items, reasons


def qiehuan_conds(f, ctx):
    """切换战法逐条评估 → (总分, items, reasons)"""
    phase = ctx["phase"]
    dragon = _dragon_ctx(ctx)
    rec = _broken_recency(ctx)
    items, reasons = [], []
    old_sector = dragon["sector"] if dragon else None
    fresh = f["sector"] != old_sector

    # S-01 时机正确：老龙头见顶日(≤5日) 或 试错期
    in_window = (phase == "probe") or (rec is not None and rec <= 5)
    s1 = 90 if in_window else 25
    items.append({"id": "S-01", "name": "时机正确", "ok": in_window, "score": s1,
                  "note": (f"主跌后试错期, 老龙头已见顶" if phase == "probe"
                           else f"老龙头见顶第 {rec} 日(≤5日触发)" if rec is not None
                           else "非切换窗口(主升/高位震荡延续中)")})
    if in_window:
        reasons.append("时机: 情绪大幅受挫后试错/老龙头见顶日 = 新周期启动日")

    # S-02 全新题材
    s2 = 100 if fresh else 25
    note2 = (f"题材({f['sector']})与上一主线({old_sector or '无'})完全不同逻辑" if fresh
             else "仍属上一主线逻辑, 不符合切换")
    items.append({"id": "S-02", "name": "全新题材", "ok": fresh, "score": s2, "note": note2})
    if fresh:
        reasons.append("全新: 与之前炒作完全不同题材(改变共识)")

    # S-03 题材新颖 + 出现时机
    nt = _news_recent(ctx, sector=f["sector"], max_days=3)
    s3 = 30 if nt else 10
    s3 += 25 if f["news_sector_n"] >= 1 else 0
    if phase == "probe":
        s3 += 20
    s3 = _cap(s3)
    items.append({"id": "S-03", "name": "题材新颖/催化", "ok": nt >= 1 or f["news_sector_n"] >= 2,
                  "score": s3, "note": f"近3日题材新闻 {nt} 条; 题材出现的时机比内容更重要"})
    if nt:
        reasons.append("催化: 新题材出现时点领先, 有新闻驱动")

    # S-04 低位首板（非高位接力）
    run60 = f["run60_pct"] or 0
    s4 = 0.0
    if f["streak_max20"] <= 1:
        s4 += 45
    elif f["streak_max20"] == 2:
        s4 += 20
    if run60 <= 20:
        s4 += 40
    elif run60 <= 45:
        s4 += 20
    s4 = _cap(s4)
    items.append({"id": "S-04", "name": "低位首板", "ok": f["streak_max20"] <= 1 and run60 <= 30,
                  "score": round(s4, 1), "note": f"60日涨幅 {run60:.0f}%、最高{max(f['streak_max20'], 1)}板以内"})
    if s4 >= 60:
        reasons.append("低位: 首板启动, 非高位接力")

    # S-05 新闻驱动（个股/板块）
    sc_news = _news_recent(ctx, code=f["code"], max_days=4) + _news_recent(ctx, sector=f["sector"], max_days=2)
    s5 = _cap(20 + sc_news * 18)
    items.append({"id": "S-05", "name": "新闻驱动", "ok": sc_news >= 1, "score": round(s5, 1),
                  "note": f"近4日相关新闻 {sc_news} 条(对新闻敏感度是切换核心能力)"})
    if sc_news:
        reasons.append("驱动: 存在个股/板块新闻催化")

    total = _cap(0.22 * s1 + 0.2 * s2 + 0.16 * s3 + 0.22 * s4 + 0.2 * s5)
    return round(total, 1), items, reasons


def _entry_state(f):
    """判断个股当前是否处于可识别买点形态"""
    t, p = f["today"], f.get("prev")
    limit = bool(t.get("limit_up"))
    streak = t.get("streak") or 0
    if limit and streak == 1:
        return "first_board"      # 低位首板涨停(今日)
    if limit and streak == 2 and p and p.get("limit_up"):
        return "one_to_two"       # 一进二(今日放量二板)
    return None


def identify(ctx):
    """返回补涨池 + 切换池。ctx 来自 analyze()。"""
    feats = ctx["feats"]
    phase = ctx["phase"]
    dragon = _dragon_ctx(ctx)
    out = {"buyang": None, "qiehuan": None}

    # ---------------- 补涨池 ----------------
    if dragon and phase in ("main_ascend", "high_oscillate"):
        items = []
        for code, f in feats.items():
            if f["sector"] != dragon["sector"] or code == dragon["code"]:
                continue
            # 排除已主升过的前龙头/中位高位股：补涨要找"低位"未大涨的同类
            if (f["run60_pct"] or 0) >= 85 or f["streak_max20"] >= 5:
                continue
            total, conds, reasons = buyang_conds(f, ctx)
            if total < 50:
                continue
            es = _entry_state(f)
            items.append({
                "code": code, "name": f["name"], "sector": f["sector"],
                "score": total, "conds": conds, "reasons": reasons[:4],
                "entry_state": es, "limit_today": bool(f["today"].get("limit_up")),
                "streak": f["today"].get("streak") or 0, "run60": round(f["run60_pct"] or 0, 1),
                "price": f["price"], "float_cap": f.get("float_cap"),
                "turnover": f["today"].get("turnover"), "pct_today": f["today"]["pct"],
                "vol20": f["vol20_pct"], "dist_high60": f["dist_high60_pct"],
            })
        items.sort(key=lambda x: -x["score"])
        out["buyang"] = {
            "asof": ctx["date"], "phase": phase, "strategy": "buyang",
            "trigger_note": f"主线板块 {dragon['sector']}，龙头 {dragon['name']}(评分{dragon.get('score')})"
                            f"，赚钱效应确认/未散 → 板块内低位同题材补涨",
            "items": items[:8], "total": len(items),
        }

    # ---------------- 切换池 ----------------
    rec = _broken_recency(ctx)
    in_switch = (phase == "probe") or (rec is not None and rec <= 5)
    if in_switch:
        items = []
        for code, f in feats.items():
            # 硬门槛：只收“新题材低位首板(今日涨停, 首板)”——切换的本质是低位新方向试错
            t = f["today"]
            if not (t.get("limit_up") and (t.get("streak") or 0) == 1):
                continue
            if (f["run60_pct"] or 0) > 35:
                continue
            if (f["gain_low60_pct"] or 0) > 60:
                continue
            total, conds, reasons = qiehuan_conds(f, ctx)
            if total < 60:
                continue
            es = _entry_state(f)
            items.append({
                "code": code, "name": f["name"], "sector": f["sector"],
                "score": total, "conds": conds, "reasons": reasons[:4],
                "entry_state": es, "limit_today": bool(t.get("limit_up")),
                "streak": t.get("streak") or 0, "run60": round(f["run60_pct"] or 0, 1),
                "price": f["price"], "float_cap": f.get("float_cap"),
                "turnover": t.get("turnover"), "pct_today": t["pct"],
                "news_n": f["news_code_n"],
            })
        items.sort(key=lambda x: -x["score"])
        out["qiehuan"] = {
            "asof": ctx["date"], "phase": phase, "strategy": "qiehuan",
            "trigger_note": (f"试错期开启, 新题材萌芽(改变共识)" if phase == "probe"
                             else f"老龙头见顶第 {rec} 日 = 新周期启动日"),
            "items": items[:8], "total": len(items),
        }
    return out


def eval_stock(code, ctx):
    """个股详情页：对单只股票做 龙头/补涨/切换 三套条件评估。"""
    f = ctx["feats"].get(code)
    if not f:
        return None
    from . import leaders
    lscore, litems = leaders._conds(f, ctx["today_stats"], ctx["stocks"].get(code))
    bscore, bitems, _ = buyang_conds(f, ctx)
    qscore, qitems, _ = qiehuan_conds(f, ctx)
    return {
        "code": code, "feat": f,
        "leader": {"score": lscore, "items": litems},
        "buyang": {"score": bscore, "items": bitems},
        "qiehuan": {"score": qscore, "items": qitems},
    }
