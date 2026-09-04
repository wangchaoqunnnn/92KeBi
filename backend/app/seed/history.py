"""确定性模拟市场生成器（history.py）：
生成 48 只股票、约 2 年多的日线 + 板块事件新闻，市场按
主跌 → 试错(低位震荡) → 主升 → 高位震荡 → 主跌 … 周期轮动，
每个主升周期由 12 个板块轮流充当主线，具备可复现的龙头/补涨/切换剧本。

约定（内部使用的“剧本”仅用于生成行情，判定与选股引擎只读行情数据）：
- cycle 长度 76 交易日：主跌 15 + 试错 13 + 主升 32 + 高位震荡 16
- 第 k 个周期主线板块 hot = k % 12（主跌期退潮的是上一周期主线）
"""
import random
from datetime import date, timedelta

from ..seed.universe import STOCKS, SECTORS

C_DECLINE, C_PROBE, C_ASCEND, C_HIGH = 15, 13, 32, 16
CYCLE = C_DECLINE + C_PROBE + C_ASCEND + C_HIGH  # 76

PHASES = ["main_decline", "probe", "main_ascend", "high_oscillate"]


def _trading_dates(total: int, today: date) -> list:
    dates, d = [], today
    while len(dates) < total:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    return dates


def _rnd(x, nd=2):
    return round(x, nd)


class _BarState:
    __slots__ = ("price", "streak", "last_pct")


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def generate_market(total_days: int, seed: int = 920202406):
    """返回 (bars_by_date: dict[date->list[bar]], news: list[dict], dates: list)"""
    rng = random.Random(seed)          # 行情流
    nrng = random.Random(seed + 101)   # 新闻流

    dates = _trading_dates(total_days, date.today())
    n = len(STOCKS)

    # 静态画像（按流通市值排序给出板块内“主升角色”选择依据）
    float_cap = [s[4] for s in STOCKS]
    sectors = [s[2] for s in STOCKS]  # sector_idx per stock
    sector_stocks = {}  # sector_idx -> [股票下标按市值降序]
    for i in range(n):
        sector_stocks.setdefault(sectors[i], []).append(i)
    for k in sector_stocks:
        sector_stocks[k].sort(key=lambda i: -float_cap[i])

    state = [_BarState() for _ in range(n)]
    for i in range(n):
        state[i].price = float(STOCKS[i][5])
        state[i].streak = 0
        state[i].last_pct = None

    bars_by_date, news = {}, []

    def one_word_turnover():
        return rng.uniform(0.5, 1.6)

    def plan_for(cycle_idx: int) -> dict:
        """周期主线/角色剧本：leader=周期龙头(总龙), fade=中位跟风股(示范亏钱), low=低位未启动(补涨角)"""
        hot = cycle_idx % len(SECTORS)
        grp = sector_stocks[hot]
        cap0, cap1, cap2, cap3 = grp[0], grp[1], grp[2], grp[3]
        leader = cap0 if cycle_idx % 2 == 0 else cap1
        mid = cap1 if leader == cap0 else cap0
        fade = cap2
        low = cap3
        return {"hot": hot, "leader": leader, "mid": mid, "fade": fade, "low": low,
                "height": 6 + cycle_idx % 3, "prev_hot": (cycle_idx - 1) % len(SECTORS)}

    prev_snaps = {}  # code -> pct (用于跌停期"昨日涨停股溢价"偏压)

    def leader_move(a: int, height: int):
        """主升阶段内 leader 的剧本走势；返回 (pct, turnover, is_one_word) 或 None。
        主升前期蓄势，a=8 起连板至高度封满（如 6~8 板），封顶后高位换手分歧，
        高度回落由高位震荡阶段第 1 天的一根大阴断板完成。"""
        first = 8
        if first <= a < first + height:
            h = a - first + 1  # 第几板
            if h == 2 and rng.random() < 0.45:
                return 10.0, one_word_turnover(), True
            if h >= 3:
                return 10.0, rng.uniform(9.0, 15.0), False
            return 10.0, rng.uniform(7.0, 11.0), False
        if a < first:
            return rng.uniform(0.6, 3.0), rng.uniform(2.5, 6.0), False   # 蓄势
        return rng.uniform(-1.8, 2.2), rng.uniform(10.0, 18.0), False    # 高位换手分歧

    for day_idx, dt in enumerate(dates):
        cycle_idx = day_idx // CYCLE
        off = day_idx % CYCLE
        plan = plan_for(cycle_idx)
        hot, prev_hot = plan["hot"], plan["prev_hot"]
        phase_idx = 0 if off < C_DECLINE else 1 if off < C_DECLINE + C_PROBE else 2 if off < C_DECLINE + C_PROBE + C_ASCEND else 3
        phase = PHASES[phase_idx]
        di = off - (C_DECLINE if phase_idx >= 1 else 0) - (C_PROBE if phase_idx >= 2 else 0) - (C_ASCEND if phase_idx >= 3 else 0) + 1  # 阶段内第几天(1起)
        a = di  # ascend day
        # 阶段基调
        if phase == "main_decline":
            bias = -2.1 - rng.uniform(0, 1.4)
            sigma = 4.6
        elif phase == "probe":
            bias = 0.05 + rng.uniform(-0.3, 0.5)
            sigma = 3.0
        elif phase == "main_ascend":
            bias = 1.1 + rng.uniform(0, 0.9)
            sigma = 2.9
        else:
            bias = -0.35 - rng.uniform(0, 0.5)
            sigma = 3.4

        today_rows = []
        pcts = []
        zt_codes, dt_codes = [], []
        leader_code = STOCKS[plan["leader"]][1]
        dt_k = 6 + (day_idx % 4) if phase == "main_decline" else 0  # 主跌期保证跌停家数

        # 生成各股 pct（脚本优先）
        for i in range(n):
            s = STOCKS[i]
            code = s[1]
            si = sectors[i]
            prev_price = state[i].price
            prev_streak = state[i].streak
            special = None
            is_hot = (si == hot)
            if is_hot:
                if phase == "main_ascend":
                    if i == plan["leader"]:
                        mv = leader_move(a, plan["height"])
                        if mv:
                            special = mv
                    elif i == plan["mid"]:
                        # 中位跟风：龙头加速期滞后 4 天连拉 3 板(梯队 3板/4板)，示范跟风利润与风险
                        if 12 <= a <= 14:
                            special = (10.0, rng.uniform(7.0, 12.0), False)
                        elif a == 15:
                            special = (rng.uniform(2.0, 5.0), rng.uniform(6.0, 9.0), False)
                        elif a >= 16:
                            special = (rng.uniform(-0.6, 1.6), rng.uniform(4.0, 9.0), False)
                    elif i == plan["fade"]:
                        if a in (16, 17):
                            special = (10.0, rng.uniform(8.0, 13.0), False)
                        elif a >= 18:
                            special = (rng.uniform(-1.2, 1.2), rng.uniform(4.0, 8.0), False)
                    else:  # low 低位补涨角：全程蛰伏，留到高位震荡补涨
                        if a >= 6:
                            special = (rng.uniform(-0.8, 1.2), rng.uniform(2.0, 5.0), False)
                elif phase == "high_oscillate":
                    if i == plan["leader"]:
                        if di == 1:
                            special = (-7.6, rng.uniform(13.0, 22.0), False)   # 断板见顶
                        elif di == 2:
                            special = (rng.uniform(-4.5, -1.5), rng.uniform(10.0, 18.0), False)
                        else:
                            special = (rng.uniform(-1.6, 1.8), rng.uniform(7.0, 15.0), False)
                    elif i == plan["mid"]:
                        # 中位股在龙头断板后逆势2板(追高诱惑) → 第5天崩 → 示范“中位股亏钱效应”
                        if di in (3, 4):
                            special = (10.0, rng.uniform(7.0, 11.0), False)
                        elif di == 5:
                            special = (-8.2, rng.uniform(8.0, 14.0), False)
                        elif di == 2:
                            special = (rng.uniform(2.0, 5.0), rng.uniform(5.0, 9.0), False)
                        else:
                            special = (rng.uniform(-2.2, 0.8), rng.uniform(5.0, 10.0), False)
                    elif i == plan["fade"]:
                        if di <= 2:
                            special = (rng.uniform(1.0, 4.0), rng.uniform(5.0, 9.0), False)
                        elif di == 4:
                            special = (-7.0, rng.uniform(7.0, 12.0), False)
                        else:
                            special = (rng.uniform(-2.0, 1.0), rng.uniform(4.0, 9.0), False)
                    elif i == plan["low"]:
                        if di == 6:
                            special = (10.0, rng.uniform(6.5, 10.5), False)     # 低位首板补涨
                        elif di == 7:
                            special = (rng.uniform(0.8, 2.8), rng.uniform(4.0, 7.5), False)
                        elif di == 8:
                            special = (10.0, rng.uniform(8.0, 13.5), False)     # 一进二换手确认
                        elif di >= 10:
                            special = (rng.uniform(-1.2, 2.6), rng.uniform(4.5, 9.0), False)
                elif phase == "probe":
                    if i == plan["leader"] and di == 2:
                        special = (10.0, rng.uniform(6.0, 10.0), False)   # 新题材萌芽首板(未来主线) -> 切换首板样本
                    elif i == plan["mid"] and di == 7:
                        special = (10.0, rng.uniform(6.5, 10.5), False)
                    elif i == plan["low"] and di == 11:
                        special = (rng.uniform(6.0, 9.5), rng.uniform(5.0, 8.5), False)
                    elif is_hot:
                        special = (rng.uniform(-0.8, 1.6), rng.uniform(2.0, 5.5), False)
                elif phase == "main_decline":
                    if is_hot:  # 上周期主线退潮
                        special = (rng.uniform(-4.0, -1.0), rng.uniform(3.0, 7.0), False)

            if special:
                pct, turnover, _is_one = special
                pct = _clamp(pct, -10, 10)
            else:
                sec_adj = 0.0
                if is_hot:
                    sec_adj = {"main_ascend": 2.2, "probe": 1.0, "high_oscillate": -0.9,
                               "main_decline": -1.8}[phase]
                if si == prev_hot and phase == "main_decline":
                    sec_adj -= 1.4
                mom = 0.0
                if state[i].last_pct is not None:
                    mom = 0.12 * state[i].last_pct
                if phase == "main_decline" and prev_snaps.get(code, 0) >= 9.5:
                    mom -= 3.2  # 昨日涨停股今日低开走弱 → 溢价率转负
                pct = _clamp(bias + sec_adj + mom + rng.gauss(0, sigma), -10.5, 10.5)
                if pct >= 9.6 and rng.random() < 0.6:
                    pct = 10.0
                elif pct <= -9.6 and rng.random() < 0.8:
                    pct = -10.0
                turnover = _clamp(rng.gauss(3.2, 1.4) + max(0, pct) * 0.35, 0.6, 26.0)

            # 主跌期：确定性挑选约 k 只跌停(退潮/补跌)
            if dt_k and ((i * 29 + day_idx * 7 + cycle_idx * 11) % 48) < dt_k:
                pct = -10.0
                turnover = rng.uniform(2.0, 8.0)

            pct = round(_clamp(pct, -10.0, 10.0), 2)
            pcts.append(pct)
            prev_snaps[code] = pct

            # 连板/跌停快照
            limit_up = pct >= 9.9
            limit_down = pct <= -9.9
            if limit_up:
                zt_codes.append(code)
                state[i].streak = prev_streak + 1
            elif limit_down:
                dt_codes.append(code)
                state[i].streak = 0
            else:
                state[i].streak = 0
            streak = state[i].streak

            pre_close = prev_price
            close = _rnd(prev_price * (1 + pct / 100.0))
            # 一字(开盘即封板,缩量) 或普通高开
            gap = 0.0
            one_word = False
            if limit_up and prev_streak >= 1 and rng.random() < 0.35:
                gap = pct  # 一字
                one_word = True
            elif limit_down:
                gap = rng.uniform(-2.5, -0.2)
            else:
                gap = _clamp(pct * 0.30 + rng.gauss(0, 0.8), -4.5, 4.5)
            open_p = _rnd(pre_close * (1 + gap / 100.0))
            if limit_up and not one_word:
                open_p = min(open_p, _rnd(close - 0.01))
            if limit_up:
                high = close
                low = _rnd(min(open_p, pre_close * (1 - rng.uniform(0, 1.8) / 100)))
            else:
                hi = max(open_p, close) * (1 + abs(rng.gauss(0, 1.0)) / 100)
                lo = min(open_p, close) * (1 - abs(rng.gauss(0, 1.0)) / 100)
                high = _rnd(max(hi, pre_close * (1 + 0.002)))
                low = _rnd(min(lo, pre_close * (1 - 0.002)))
            high = max(high, open_p, close)
            low = min(low, open_p, close)
            if limit_down:
                close = _rnd(pre_close * 0.9)
                low = close
            turnover = round(_clamp(turnover, 0.3, 40.0), 2)
            amount = round(float_cap[i] * turnover / 100.0, 2)
            vol = int(amount * 1e8 / max(close, 0.01) / 100)  # 手
            bar = {
                "date": dt.isoformat(), "code": code, "name": s[0],
                "sector": SECTORS[si]["name"], "sector_idx": si,
                "open": open_p, "high": high, "low": low, "close": close,
                "pre_close": _rnd(pre_close), "pct": pct, "turnover": turnover,
                "amount": amount, "volume": vol,
                "streak": streak, "limit_up": int(limit_up), "limit_down": int(limit_down),
                "one_word": int(one_word),
            }
            today_rows.append(bar)
            state[i].price = close
            state[i].last_pct = pct

        bars_by_date[dt.isoformat()] = today_rows

        # ---------- 新闻 ----------
        hot_name = SECTORS[hot]["name"]
        prev_name = SECTORS[prev_hot]["name"]
        if phase == "main_ascend" and (di in (2, 10, 20) or rng.random() < 0.4):
            tpl = nrng.choice([
                f"{hot_name}主线持续强化，资金抢筹明显，赚钱效应集中",
                f"{hot_name}再迎催化：政策与产业共振，机构上调景气预期",
                f"{hot_name}龙头加速，板块内跟风资金踊跃",
            ])
            news.append({"date": dt.isoformat(), "code": None, "sector": hot_name,
                         "title": tpl, "sentiment": round(nrng.uniform(0.6, 0.95), 2),
                         "source": "模拟·政策研报", "kind": "sector_catalyst"})
        elif phase == "probe":
            if di == 1:
                news.append({"date": dt.isoformat(), "code": None, "sector": hot_name,
                             "title": f"新方向出现：{hot_name}获得重磅事件催化，资金开始试错新题材",
                             "sentiment": 0.85, "source": "模拟·财联社", "kind": "new_theme"})
            elif di == 5:
                news.append({"date": dt.isoformat(), "code": None, "sector": hot_name,
                             "title": f"{hot_name}政策预期升温，关注低位首板试错机会",
                             "sentiment": 0.7, "source": "模拟·证券时报", "kind": "new_theme"})
            elif nrng.random() < 0.25:
                news.append({"date": dt.isoformat(), "code": None, "sector": None,
                             "title": "市场缩量震荡，情绪仍处冰点修复初期，谨慎为主",
                             "sentiment": 0.15, "source": "模拟·市场综述", "kind": "market_note"})
        elif phase == "main_decline":
            if di in (1, 2):
                news.append({"date": dt.isoformat(), "code": None, "sector": prev_name,
                             "title": f"{prev_name}高位股集体退潮，亏钱效应显著，控制回撤为第一要务",
                             "sentiment": -0.9, "source": "模拟·复盘", "kind": "crash_warn"})
            elif di == 9 and nrng.random() < 0.5:
                news.append({"date": dt.isoformat(), "code": None, "sector": None,
                             "title": "连续跌停后情绪接近冰点，历史上大级别机会多在冰点之后",
                             "sentiment": 0.1, "source": "模拟·复盘", "kind": "market_note"})
        else:  # high_oscillate
            if di == 1:
                news.append({"date": dt.isoformat(), "code": STOCKS[plan["leader"]][1],
                             "sector": hot_name,
                             "title": f"{STOCKS[plan['leader']][0]}高位巨震断板，主升或告一段落",
                             "sentiment": -0.55, "source": "模拟·盘面", "kind": "leader_top"})
            elif di in (5, 8) and nrng.random() < 0.6:
                news.append({"date": dt.isoformat(), "code": None, "sector": hot_name,
                             "title": f"{hot_name}内部高低切换，低位品种出现补涨迹象",
                             "sentiment": 0.5, "source": "模拟·盘面", "kind": "rotation"})

        # 龙头个股新闻（主升中后期增强"逻辑硬/市场共识"）
        if phase == "main_ascend" and di in (3, 6, 12):
            lcode = STOCKS[plan["leader"]][1]
            lname = STOCKS[plan["leader"]][0]
            news.append({"date": dt.isoformat(), "code": lcode, "sector": hot_name,
                         "title": f"{lname}：{SECTORS[hot]['keywords'][0]}核心标的，订单/产能催化获关注",
                         "sentiment": round(nrng.uniform(0.65, 0.95), 2),
                         "source": "模拟·公司公告", "kind": "stock_news"})

    return bars_by_date, news, dates
