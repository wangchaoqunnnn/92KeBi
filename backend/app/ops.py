"""打板操作台账（ops）：按 92 模式在龙头/补涨/切换标的出现买点→买点提示并入买入池；
出现卖点→卖点提示并结算进卖出池(含买入/卖出时间价与理由、盈亏)；符合模式的标的进观察池。
- 数据源: 每次分析视图重建后由调度自动执行 sweep(约每10s), 也可 POST /api/ops/flush 手动触发
- 口径: 买入/卖出价格为提示时点的实时价(样本池行情), 盈亏按百分比口径(系统不涉及资金)
"""
import logging
import time
from datetime import datetime

from . import db, market_cache
from .core.text import PHASE_CN
from .config import DATA_SOURCE

log = logging.getLogger("kb.ops")

TABLE = """
CREATE TABLE IF NOT EXISTS ops_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL, name TEXT, sector TEXT,
  pool TEXT NOT NULL,               -- buy | sell | watch
  status TEXT NOT NULL DEFAULT 'open',
  strategy TEXT, signal TEXT,
  reason TEXT,
  entry_date TEXT, entry_time TEXT, entry_price REAL,
  exit_date TEXT, exit_time TEXT, exit_price REAL, exit_reason TEXT,
  pnl_pct REAL, hold_days INTEGER,
  created_at TEXT, updated_at TEXT, last_date TEXT,
  note TEXT,
  score REAL
);
CREATE INDEX IF NOT EXISTS idx_ops_code ON ops_items(code);
CREATE INDEX IF NOT EXISTS idx_ops_pool ON ops_items(pool, status);
CREATE TABLE IF NOT EXISTS ops_prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT, name TEXT, type TEXT,          -- buy | sell | watch
  strategy TEXT, signal TEXT, ts TEXT, price REAL, reason TEXT
);
"""


def _ensure_score_col():
    try:
        cols = [r["name"] for r in db.query("PRAGMA table_info(ops_items)")]
        if "score" not in cols:
            db.execute("ALTER TABLE ops_items ADD COLUMN score REAL")
    except Exception as e:
        log.warning("ensure score col: %s", e)


_ok = False


def setup():
    global _ok
    if not _ok:
        db.init_db()
        con = db.get_conn()
        con.executescript(TABLE)
        con.commit()
        _ensure_score_col()
        _ok = True


def _in_window():
    """买卖操作仅限交易日 09:25~14:59（盘前可排单、盘后不自动操作）"""
    from datetime import datetime as _dt
    now = _dt.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 925 <= hm <= 1459


def window_info():
    return {"start": "09:25", "end": "14:59", "open": _in_window(),
            "weekday": datetime.now().weekday() < 5,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M")}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 演示数据(卖出池样例)
def _ensure_demo_sell():
    """向卖出池插入1条模拟演示数据(仅一次), 便于查看卖出池结构/统计"""
    try:
        if db.meta_get("ops_demo_sell_v1"):
            return
        now = _now()
        pnl = round((7.12 / 6.98 - 1) * 100, 2)
        with db.tx() as con:
            con.execute(
                "INSERT INTO ops_items(code,name,sector,pool,status,strategy,signal,reason,"
                "entry_date,entry_time,entry_price,exit_date,exit_time,exit_price,exit_reason,"
                "pnl_pct,hold_days,created_at,updated_at,last_date,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("000592", "平潭发展", "农林牧渔", "sell", "closed", "buyang", "低位首板确认",
                 "低位首板确认：低位首板涨停，题材与主线一致 → 补涨买点（演示样例）",
                 "2026-09-03", "09:41:07", 6.98, "2026-09-04", "13:22:41", 7.12,
                 "加速一致/一致转分歧，止盈离场（演示样例）",
                 pnl, 1, now, now, "2026-09-04", "[演示样例·仅供查看卖出池布局，可忽略不计入实盘]"))
        db.meta_set("ops_demo_sell_v1", "1")
        log.info("demo sell row inserted")
    except Exception as e:
        log.warning("demo sell: %s", e)


# ---------------------------------------------------------------- 微信推送
def _wechat_push(text):
    """企业微信群机器人推送(webhook 支持逗号分隔多地址)。未配置则跳过。"""
    from .config import WECHAT_WEBHOOK
    hooks = [h.strip() for h in WECHAT_WEBHOOK.split(",") if h.strip()]
    if not hooks:
        log.debug("WECHAT_WEBHOOK 未配置, 跳过微信推送")
        return {"sent": 0, "reason": "未配置 WECHAT_WEBHOOK"}
    import json as _j
    import urllib.request as _ur
    body = {"msgtype": "text", "text": {"content": str(text)[:1900]}}
    data = _j.dumps(body, ensure_ascii=False).encode("utf-8")
    sent = 0
    err = None
    for url in hooks:
        try:
            req = _ur.Request(url, data=data, headers={"Content-Type": "application/json"})
            _ur.urlopen(req, timeout=6)
            sent += 1
        except Exception as e:  # noqa
            err = str(e)[:120]
            log.warning("wechat push fail: %s", e)
    return {"sent": sent, "reason": err}


def wechat_push_test():
    return _wechat_push("【92K 打板·测试】微信推送已接通 ✅\n时间 " + _now())


def _wechat_status():
    from .config import WECHAT_WEBHOOK
    hooks = [h for h in WECHAT_WEBHOOK.split(",") if h.strip()]
    return {"enabled": bool(hooks), "hooks": len(hooks)}


def _price_for(code, ctx):
    """实时价: 快照报价优先(实盘), 否则当日合成K线收盘"""
    try:
        if DATA_SOURCE == "real":
            from .real import market as real_mkt
            q = real_mkt.snapshot().get("quotes", {}).get(code)
            if q and q.get("price"):
                return q["price"]
    except Exception:
        pass
    f = (ctx or {}).get("feats", {}).get(code) or {}
    today = f.get("today") or {}
    return today.get("close")


def _signal_view():
    """当前视图与上下文(轻量复制, 不持有缓存锁)"""
    ctx = market_cache.get_ctx() or {}
    view = market_cache._cache.get("view") or {}
    return view, ctx


# ---------------------------------------------------------------- 事件
def _prompt(code, name, ptype, strategy, signal, price, reason):
    try:
        with db.tx() as con:
            con.execute("INSERT INTO ops_prompts(code,name,type,strategy,signal,ts,price,reason) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (code, name, ptype, strategy, signal, _now(),
                         round(float(price or 0), 2), str(reason or "")[:400]))
        log.info("ops prompt %s %s %s @%s: %s", ptype, code, signal, price, reason)
    except Exception as e:  # noqa
        log.warning("prompt log: %s", e)


def _open_buy_codes():
    return {r["code"] for r in db.query(
        "SELECT code FROM ops_items WHERE pool='buy' AND status='open'")}


def _watch_codes():
    return {r["code"] for r in db.query(
        "SELECT code FROM ops_items WHERE pool='watch' AND status='open'")}


# ---------------------------------------------------------------- 扫描
def sweep(view=None, ctx=None):
    """自动盯盘: 买点→买入池; 持仓卖点→卖出池; 模式候选→观察池。幂等、低写入。"""
    setup()
    _ensure_demo_sell()
    if not view or not ctx:
        view, ctx = _signal_view()
    if not view or not ctx or not ctx.get("feats"):
        return {"state": "idle", "reason": "视图未就绪"}
    date = view.get("date") or _today()
    opened = 0
    closed = 0
    watched = 0
    can_trade = _in_window()

    # ---- 1) 买点提示 → 买入池(仅在 09:25-14:59 交易窗口) ----
    open_buys = _open_buy_codes()
    sigs = (view.get("signals") or {}).get("items") or []
    buy_sigs = [s for s in sigs if s.get("dir") == "buy"]
    if can_trade:
        for s in buy_sigs[:12]:
            code = s.get("code")
            if not code or code in open_buys:
                continue
            price = _price_for(code, ctx) or s.get("price")
            if not price or price <= 0:
                continue
            name = s.get("name", code)
            reason = f"{s.get('signal')}：{s.get('reason')}"
            now = _now()
            try:
                with db.tx() as con:
                    cur = con.execute(
                        "SELECT id FROM ops_items WHERE code=? AND pool='buy' AND status='open'",
                        (code,)).fetchone()
                    if cur:
                        continue
                    con.execute(
                        "INSERT INTO ops_items(code,name,sector,pool,status,strategy,signal,reason,"
                        "entry_date,entry_time,entry_price,created_at,updated_at,last_date) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (code, name, s.get("sector"), "buy", "open", s.get("strategy"),
                         s.get("signal"), reason, date, _now(), price, now, now, date))
                opened += 1
                _prompt(code, name, "buy", s.get("strategy"), s.get("signal"), price, reason)
                try:
                    _wechat_push(
                        f"【92K 打板·买点提示】\n{name}（{code}）{s.get('sector') or ''}\n"
                        f"信号：{s.get('signal')}\n价格：{price}\n时间：{date} {_now()[11:]}\n理由：{reason}")
                except Exception as e:  # noqa
                    log.warning("wechat buy push: %s", e)
            except Exception as e:  # noqa
                log.warning("buy add %s: %s", code, e)

    # ---- 2) 持仓卖点 → 卖出池(结算, 仅在交易窗口) ----
    if can_trade:
        rows = db.query("SELECT * FROM ops_items WHERE pool='buy' AND status='open'")
        for r in rows:
            code = r["code"]
            f = ctx.get("feats", {}).get(code)
            if not f:
                continue
            today = f.get("today") or {}
            price = _price_for(code, ctx) or today.get("close")
            if not price or price <= 0:
                continue
            entry = r["entry_price"] or price
            exit_reason = None
            # 卖出理由优先: 止损线 / 规则引擎卖出信号
            if price <= entry * (1 - 0.05):
                exit_reason = f"止损(-5%)：现价 {price:.2f} ≤ 买入 {entry:.2f}×0.95"
            else:
                from .core import signals as sig_mod
                sigs2 = sig_mod.for_stock(code, f, ctx)
                sell = next((s for s in sigs2 if s.get("dir") == "sell"), None)
                if sell:
                    exit_reason = f"{sell.get('signal')}：{sell.get('reason')}"
            if not exit_reason:
                continue
            # 持有天数(自然日)
            try:
                ed = datetime.strptime(r["entry_date"] + " " + (r["entry_time"] or "00:00:00"),
                                       "%Y-%m-%d %H:%M:%S")
                hold = max(1, (datetime.now() - ed).days)
            except Exception:
                hold = 1
            pnl = round((price / entry - 1) * 100, 2)
            now = _now()
            try:
                with db.tx() as con:
                    con.execute(
                        "UPDATE ops_items SET pool='sell', status='closed', "
                        "exit_date=?, exit_time=?, exit_price=?, exit_reason=?, pnl_pct=?, "
                        "hold_days=?, updated_at=? WHERE id=?",
                        (date, now, round(price, 2), exit_reason, pnl, hold, now, r["id"]))
                closed += 1
                _prompt(code, r.get("name", code), "sell", r.get("strategy"),
                        "卖出提示", price, exit_reason)
            except Exception as e:  # noqa
                log.warning("sell close %s: %s", code, e)

    # ---- 3) 观察池: 符合模式待观察(池内候选/龙头候选, 记录日期与理由) ----
    watch_need = {}
    pools = view.get("pools") or {}
    for key in ("buyang", "qiehuan"):
        pv = pools.get(key)
        if not pv:
            continue
        for it in (pv.get("items") or [])[:12]:
            reason = (it.get("reasons") or ["—"])[:2]
            trigger = it.get("entry_state")
            tag = {"first_board": "今日首板", "one_to_two": "今日一进二"}.get(trigger, "等待买点")
            txt = f"[{tag}] {'；'.join(str(x) for x in reason)}"
            watch_need[it["code"]] = (it.get("name", it["code"]), it.get("sector"), txt,
                                      it.get("score"))
    for l in (view.get("leaders") or {}).get("pool") or []:
        code = l["code"]
        if code in watch_need:
            continue
        conds_ok = sum(1 for c in (l.get("conds") or []) if c.get("ok"))
        warn = "⚠高位断板观察(断板即撤)" if l.get("broken_today") else "龙头候选(辨识度)"
        txt = f"[{warn}] {conds_ok}/6 条条件成立"
        watch_need[code] = (l.get("name", code), l.get("sector"), txt, l.get("score"))

    existing = db.query("SELECT code, reason, last_date FROM ops_items "
                        "WHERE pool='watch' AND status='open'")
    exist_map = {r["code"]: r for r in existing}
    seen = set()
    for code, (name, sector, txt, score) in watch_need.items():
        if code in seen:
            continue
        seen.add(code)
        cur = exist_map.get(code)
        now = _now()
        if cur and cur["reason"] == txt and cur["last_date"] == date:
            continue  # 无变化不写库
        try:
            with db.tx() as con:
                if cur:
                    con.execute("UPDATE ops_items SET name=?, sector=?, reason=?, last_date=?, "
                                "score=?, updated_at=? WHERE id=?",
                                (name, sector, txt, date, score, now, cur["id"]))
                else:
                    con.execute(
                        "INSERT INTO ops_items(code,name,sector,pool,status,reason,last_date,"
                        "created_at,updated_at,note,score) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (code, name, sector, "watch", "open", txt, date, now, now,
                         f"加入观察 {date}", score))
            watched += 1
        except Exception as e:  # noqa
            log.warning("watch add %s: %s", code, e)
    # 观察过期: 不在候选且 >5 个自然日未更新 → 归档
    stale = [r["id"] for r in db.query(
        "SELECT id, last_date FROM ops_items WHERE pool='watch' AND status='open'")]
    for r in db.query("SELECT id, last_date FROM ops_items WHERE pool='watch' AND status='open'"):
        try:
            old = datetime.strptime(r["last_date"], "%Y-%m-%d").date()
            if (datetime.now().date() - old).days > 5:
                db.execute("UPDATE ops_items SET status='archived', updated_at=? WHERE id=?",
                           (_now(), r["id"]))
        except Exception:
            pass
    return {"state": "done", "date": date, "opened": opened, "closed": closed,
            "watched": watched, "watch_total": len(watch_need),
            "window": window_info()}


# ---------------------------------------------------------------- 查询/操作
def _row_view(r):
    return {k: r[k] for k in ("id", "code", "name", "sector", "pool", "status", "strategy",
                              "signal", "reason", "entry_date", "entry_time", "entry_price",
                              "exit_date", "exit_time", "exit_price", "exit_reason", "pnl_pct",
                              "hold_days", "created_at", "updated_at", "last_date", "score")}


def _score_fallback(row):
    """旧数据无 score 字段时, 从理由文本解析评分兜底"""
    if row.get("score") is not None:
        return row["score"]
    import re
    txt = str(row.get("reason") or "")
    m = re.search(r"龙头评分\s*([\d.]+)", txt) or re.search(r"\[?[\u4e00-\u9fa5]*\]?\s*([\d.]+)\s*分", txt)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def _clean_reason(txt):
    """移除理由中已单列展示的评分片段(兼容旧数据)"""
    import re
    s = str(txt or "")
    s = re.sub(r"龙头评分\s*[\d.]+\s*", "", s)
    s = re.sub(r"(\[[^\]]*\])\s*[\d.]+\s*分：", r"\1 ", s)
    s = re.sub(r"[\d.]+\s*分：", "", s)
    s = re.sub(r"（(\d+/6[^）]*)）", r"(\1)", s)   # 兼容旧全角括号条件计数
    s = re.sub(r"\]\s*\(", "] ", s)
    s = re.sub(r"\)\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ；;")
    return s


def overview():
    setup()
    date = _today()
    _, ctx = _signal_view()
    # 统计
    buy_rows = [r for r in db.query(
        "SELECT * FROM ops_items WHERE pool='buy' AND status='open' ORDER BY entry_time DESC LIMIT 60")]
    sell_rows = [r for r in db.query(
        "SELECT * FROM ops_items WHERE pool='sell' ORDER BY exit_time DESC LIMIT 100")]
    watch_rows = [r for r in db.query(
        "SELECT * FROM ops_items WHERE pool='watch' AND status='open' "
        "ORDER BY updated_at DESC LIMIT 80")]
    for r in watch_rows:
        if r.get("score") is None:
            scr = _score_fallback(r)
            if scr is not None:
                try:
                    db.execute("UPDATE ops_items SET score=? WHERE id=?", (scr, r["id"]))
                except Exception:
                    pass
                r["score"] = scr
        # 评分已单列, 同步清理理由中残留的评分文案(立即生效并回写DB)
        cleaned = _clean_reason(r.get("reason"))
        if cleaned != r.get("reason"):
            try:
                db.execute("UPDATE ops_items SET reason=? WHERE id=?", (cleaned, r["id"]))
            except Exception:
                pass
            r["reason"] = cleaned
    prompts = [r for r in db.query(
        "SELECT * FROM ops_prompts ORDER BY id DESC LIMIT 60")]
    # 现价与实时浮盈
    for r in buy_rows:
        price = _price_for(r["code"], ctx)
        r["last_price"] = round(price, 2) if price else None
        r["live_pct"] = round((price / r["entry_price"] - 1) * 100, 2) \
            if price and r["entry_price"] else None
    wins = [r for r in sell_rows if (r["pnl_pct"] or 0) > 0]
    loss = [r for r in sell_rows if (r["pnl_pct"] or 0) <= 0]
    return {
        "date": date,
        "mode": "real" if DATA_SOURCE == "real" else "mock",
        "window": window_info(),
        "wechat": _wechat_status(),
        "stats": {
            "buy_open": len(buy_rows), "watch": len(watch_rows), "sold": len(sell_rows),
            "today_prompt_buy": sum(1 for p in prompts if p["type"] == "buy"),
            "win_rate_pct": round(len(wins) / len(sell_rows) * 100, 1) if sell_rows else None,
            "avg_win_pct": round(sum(r["pnl_pct"] for r in wins) / len(wins), 2) if wins else None,
            "avg_loss_pct": round(sum(r["pnl_pct"] for r in loss) / len(loss), 2) if loss else None,
            "avg_hold_days": round(sum((r["hold_days"] or 0) for r in sell_rows) / len(sell_rows), 1) if sell_rows else None,
        },
        "buy": [dict(_row_view(r), last_price=r.get("last_price"), live_pct=r.get("live_pct"))
                for r in buy_rows],
        "sell": [_row_view(r) for r in sell_rows],
        "watch": [_row_view(r) for r in watch_rows],
        "prompts": [{"code": p["code"], "name": p["name"], "type": p["type"],
                     "strategy": p["strategy"], "signal": p["signal"], "ts": p["ts"],
                     "price": p["price"], "reason": p["reason"]} for p in prompts],
        "disclaimer": ("自动盯盘提示按 92 模式规则生成（买点=信号触发, 卖点=断板/止损/不及预期等），"
                       "价格为提示时点行情价；仅供参考，不构成投资建议，最终按你实际成交为准。"),
    }


def ignore_item(pool, code):
    setup()
    where = "pool=? AND code=? AND status='open'" if pool == "buy" else "pool=? AND code=?"
    if pool == "buy":
        n = db.execute("UPDATE ops_items SET status='ignored', updated_at=? "
                       "WHERE pool='buy' AND code=? AND status='open'", (_now(), code)).rowcount
    elif pool == "watch":
        n = db.execute("UPDATE ops_items SET status='archived', updated_at=? "
                       "WHERE pool='watch' AND code=? AND status='open'", (_now(), code)).rowcount
    else:
        n = 0
    return {"ok": True, "removed": n}


def manual_sell(code):
    """手动了结一笔买入池持仓(按当前价)。仅 09:25-14:59 交易时段可执行。"""
    setup()
    if not _in_window():
        wi = window_info()
        return {"ok": False, "error": f"仅交易时段({wi['start']}-{wi['end']})可买卖操作；当前 {wi['now']}"}
    view, ctx = _signal_view()
    rows = db.query("SELECT * FROM ops_items WHERE pool='buy' AND status='open' AND code=?",
                    (code,))
    if not rows:
        return {"ok": False, "error": "买入池中无该持仓"}
    r = rows[0]
    price = _price_for(code, ctx) or r["entry_price"]
    pnl = round((price / r["entry_price"] - 1) * 100, 2) if r["entry_price"] else 0
    now = _now()
    db.execute(
        "UPDATE ops_items SET pool='sell', status='closed', exit_date=?, exit_time=?, "
        "exit_price=?, exit_reason='手动了结', pnl_pct=?, updated_at=? WHERE id=?",
        (r["entry_date"], now, round(price, 2), pnl, now, r["id"]))
    _prompt(code, r.get("name", code), "sell", r.get("strategy"), "手动卖出", price, "手动了结")
    return {"ok": True, "code": code, "price": round(price, 2), "pnl_pct": pnl}


def manual_watch(code):
    """手动加入观察池"""
    setup()
    view, ctx = _signal_view()
    meta = (ctx or {}).get("stocks", {}).get(code) or {}
    price = _price_for(code, ctx)
    now = _now()
    date = _today()
    if db.query_one("SELECT id FROM ops_items WHERE pool='watch' AND code=? AND status='open'", (code,)):
        return {"ok": False, "error": "已在观察池"}
    db.execute(
        "INSERT INTO ops_items(code,name,sector,pool,status,reason,last_date,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (code, meta.get("name") or code, meta.get("sector") or "",
         "watch", "open", "手动加入观察", date, now, now))
    _prompt(code, meta.get("name") or code, "watch", None, "手动观察", price, "手动加入观察池")
    return {"ok": True}


def delete_sell(item_id):
    """管理删除卖出池记录"""
    setup()
    row = db.query_one("SELECT id, pool, name FROM ops_items WHERE id=?", (item_id,))
    if not row:
        return {"ok": False, "error": "记录不存在"}
    if row["pool"] != "sell":
        return {"ok": False, "error": "仅卖出池记录可删除(其它池请用移除/忽略)"}
    db.execute("DELETE FROM ops_items WHERE id=?", (item_id,))
    log.info("ops delete sell id=%s name=%s", item_id, row["name"])
    return {"ok": True, "deleted": 1, "name": row["name"]}
