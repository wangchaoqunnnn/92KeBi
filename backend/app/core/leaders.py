"""龙头识别（需求 C-02 / 条件 L-01..L-06）。
纯函数：输入当日全市场特征 feats + 盘面统计，输出候选评分、总龙、板块龙。
"""
W = {"L01": 0.24, "L02": 0.16, "L03": 0.20, "L04": 0.15, "L05": 0.10, "L06": 0.15}


def _cap(x, hi=100.0):
    return min(hi, max(0.0, x))


def _conds(feat, today_stats, ctx_stock=None):
    """返回 L-01..L-06 逐条评分与说明。"""
    f = feat
    t = f["today"]
    streak, run60 = f["streak_max20"], f["run60_pct"] or 0
    sec = f["sector"]
    policy = bool((ctx_stock or {}).get("sector_policy"))

    l1 = _cap(streak * 10 + min(max(run60, 0), 60) * 0.55)
    l1_ok = streak >= 3 or run60 >= 25
    l1_note = (f"阶段最高{streak}连板、60日涨幅{run60:.1f}%" if streak or run60 else
               "当前无连板辨识度")

    base = 60 if policy else 25
    heat = (ctx_stock or {}).get("sector_heat", 50)
    l2 = _cap(base + max(0, heat - 40) * 0.3 + f["news_sector_n"] * 4 + f["news_code_n"] * 5)
    l2_ok = l2 >= 55
    kw = (ctx_stock or {}).get("sector_keywords", [])
    l2_note = f"题材: {'/'.join(kw[:2])}；{'国家级政策/硬逻辑支撑' if policy else '题材偏主题炒作'}；近7日新闻 {f['news_code_n'] + f['news_sector_n']} 条"

    l3 = _cap(f["sector_zt_5d"] * 9 + f["sector_zt_today"] * 8)
    l3_ok = l3 >= 30
    l3_note = f"板块近5日涨停 {f['sector_zt_5d']} 只(今日 {f['sector_zt_today']} 只)"

    turn = t.get("turnover") or 0
    if f["one_word_today"] and streak >= 3:
        l4 = _cap(20 + turn * 1.5)
    else:
        l4 = _cap(90 - abs(turn - 13) * 2.2)
    l4_ok = turn >= 5 and turn <= 28 and not (f["one_word_today"] and streak >= 4)
    l4_note = f"换手 {turn}%（avg5 {f['turnover_avg5']}%）；{'缩量一字,换手不充分' if f['one_word_today'] else '换手充足' if l4_ok else '换手偏离理想区间'}"

    price = f["price"]
    l5 = _cap(100 - max(0, price - 4) * 1.6) if price > 4 else 90
    l5_ok = 3 <= price <= 45
    l5_note = f"股价 {price:.2f} 元——{'价格适中,有想象空间' if l5_ok else '价格偏高' if price > 45 else '低价'}"

    seal = f["seal_ratio"] * 100  # 成交额/流通市值 %
    l6 = _cap(min(turn, 15) * 4 + f["news_code_n"] * 8 + f["news_sector_n"] * 3)
    l6_ok = (turn >= 5 or f["news_code_n"] >= 2)
    l6_note = f"人气: 换手 {turn}%、新闻 {f['news_code_n']} 条; 成交额/流通市值≈{seal:.1f}%"

    total = _cap(sum(W[k] * v for k, v in
                     [("L01", l1), ("L02", l2), ("L03", l3), ("L04", l4), ("L05", l5), ("L06", l6)]))
    items = [
        {"id": "L-01", "name": "辨识度", "ok": l1_ok, "score": round(l1, 1), "note": l1_note},
        {"id": "L-02", "name": "逻辑硬", "ok": l2_ok, "score": round(l2, 1), "note": l2_note},
        {"id": "L-03", "name": "带动性", "ok": l3_ok, "score": round(l3, 1), "note": l3_note},
        {"id": "L-04", "name": "换手充分", "ok": l4_ok, "score": round(l4, 1), "note": l4_note},
        {"id": "L-05", "name": "价格适中", "ok": l5_ok, "score": round(l5, 1), "note": l5_note},
        {"id": "L-06", "name": "市场共识", "ok": l6_ok, "score": round(l6, 1), "note": l6_note},
    ]
    return round(total, 1), items


def identify(feats, today_stats, prev_stats, phase_label, stocks_meta):
    candidates = []
    for code, f in feats.items():
        streak, run60 = f["streak_max20"], f["run60_pct"] or 0
        t = f["today"]
        # 入围门槛：有阶段辨识度或今日异动
        if not (streak >= 2 or run60 >= 15 or (t.get("limit_up") and streak >= 1)):
            continue
        if t["pct"] <= -9.5:  # 跌停不参与龙头竞选
            continue
        total, items = _conds(f, today_stats, stocks_meta.get(code))
        candidates.append({
            "code": code, "name": f["name"], "sector": f["sector"],
            "score": total, "conds": items, "feat": f,
            "streak": streak, "run60": round(run60, 1), "pct_today": t["pct"],
            "turnover": t.get("turnover"), "price": f["price"], "limit_today": bool(t.get("limit_up")),
            "broken_today": bool((f.get("prev") or {}).get("streak", 0) >= 3 and not t.get("limit_up")
                                 and t["pct"] < 0),
        })
    candidates.sort(key=lambda c: -c["score"])
    pool = candidates[:14]

    dragon = None
    if candidates and candidates[0]["score"] >= 45:
        dragon = candidates[0]
        dragon["role"] = "total"

    # 板块龙（每板块最强且具备一定辨识度）
    by_sector = {}
    for c in candidates:
        if c["streak"] >= 2 or c["run60"] >= 20 or (c["limit_today"] and c["score"] >= 40):
            by_sector.setdefault(c["sector"], []).append(c)
    sector_leaders = []
    for sec, cs in by_sector.items():
        top = cs[0]
        if dragon and top["code"] == dragon["code"]:
            continue
        sector_leaders.append({**top, "role": "sector"})
    sector_leaders.sort(key=lambda c: -c["score"])
    return {"dragon": dragon, "sector_leaders": sector_leaders[:10], "pool": pool,
            "asof": today_stats.get("date")}
