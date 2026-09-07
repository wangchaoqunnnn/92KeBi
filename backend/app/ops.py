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
    """买卖操作仅限交易日 09:25~14:59（北京时间; 盘前可排单、盘后不自动操作）"""
    from . import cn_time
    if not cn_time.is_weekday():
        return False
    return cn_time.in_range(925, 1459)


def window_info():
    from . import cn_time
    return {"start": "09:25", "end": "14:59", "open": _in_window(),
            "weekday": cn_time.is_weekday(),
            "now": cn_time.now_str("%Y-%m-%d %H:%M")}


def _now():
    from . import cn_time
    return cn_time.now_str()


def _today():
    from . import cn_time
    return cn_time.today_str()


# ---------------------------------------------------------------- 微信推送
def _hooks():
    """Webhook 来源: 环境变量 WECHAT_WEBHOOK + 运行期文件 data/wechat_webhook.txt(推荐,不入git) + DB meta"""
    from .config import WECHAT_WEBHOOK, DATA_DIR
    import os
    urls = []
    if WECHAT_WEBHOOK:
        urls += [h.strip() for h in WECHAT_WEBHOOK.split(",") if h.strip()]
    try:
        f = os.path.join(DATA_DIR, "wechat_webhook.txt")
        if os.path.exists(f):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    except Exception:
        pass
    try:
        v = db.meta_get("wechat_webhook")
        if v:
            urls += [h.strip() for h in v.split(",") if h.strip()]
    except Exception:
        pass
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _clip_bytes(s, limit):
    """按 UTF-8 字节数截断(企业微信按字节计数, 中文字符=3字节)"""
    b = str(s).encode("utf-8")
    if len(b) <= limit:
        return str(s)
    return b[:limit].decode("utf-8", "ignore").rstrip()


def _wechat_push(text, page_url=None):
    """企业微信群机器人推送(支持多个webhook)。未配置则跳过。
    以企业微信响应体 errcode==0 为唯一成功判据(HTTP 200 不等于送达, 失败会带 errcode)。
    附加“点击打开打板操作台”链接时, 地址不写死: page_url 参数 > 环境变量 WECHAT_PAGE_URL
    > 按最近一次访问来源动态推导(pub_url)。都取不到则发纯文本。"""
    hooks = _hooks()
    if not hooks:
        log.debug("微信 Webhook 未配置, 跳过推送")
        return {"sent": 0, "reason": "未配置微信 Webhook(WECHAT_WEBHOOK 或 data/wechat_webhook.txt)"}
    import json as _j
    import urllib.request as _ur
    url = ""
    if page_url:
        url = page_url.strip()
    if not url:
        try:
            from . import pub_url
            url = pub_url.ops_page_url()
        except Exception:
            url = ""
    txt = str(text).strip()
    if url:
        # markdown 消息: 正文 + 可点击链接(markdown 才支持群内点击打开网页)
        content = f"{_clip_bytes(txt, 3600)}\n\n[📋 点击打开 92K 打板操作台]({url})"
        body = {"msgtype": "markdown", "markdown": {"content": content}}
    else:
        body = {"msgtype": "text", "text": {"content": _clip_bytes(txt, 1900)}}
    data = _j.dumps(body, ensure_ascii=False).encode("utf-8")
    sent = 0
    errs = []
    for url in hooks:
        try:
            req = _ur.Request(url, data=data, headers={"Content-Type": "application/json"})
            resp = _ur.urlopen(req, timeout=6)
            raw = resp.read().decode("utf-8", "ignore").strip()
            try:
                j = _j.loads(raw) or {}
            except Exception:
                j = {}
            code = j.get("errcode")
            if code == 0:
                sent += 1
            else:
                msg = j.get("errmsg") or raw[:100] or "unknown"
                errs.append(f"errcode={code} {msg}"[:200])
                log.warning("wechat push rejected(%s): %s", url[-24:], msg[:120])
        except Exception as e:  # noqa
            errs.append(str(e)[:150])
            log.warning("wechat push fail: %s", e)
    return {"sent": sent, "reason": "；".join(errs) if errs else None}


def wechat_push_test():
    return _wechat_push("【92K 打板·测试】微信推送已接通 ✅\n时间 " + _now())


def _fmt_num(v, nd=2):
    try:
        if v is None:
            return "—"
        return f"{round(float(v), nd):.{nd}f}"
    except Exception:
        return "—"


def _fmt_pct(v):
    try:
        if v is None:
            return "—"
        return f"{float(v):+.2f}%"
    except Exception:
        return "—"


def _notify(msg):
    """向配置的微信群推送(失败不抛异常, 记日志并返回详细原因)"""
    try:
        res = _wechat_push(msg)
        head = (msg.splitlines()[0] if msg else "")[:60]
        if res.get("sent", 0) > 0:
            log.info("wechat push ok: %s", head)
        else:
            log.warning("wechat push NOT delivered: %s -> %s", head, res.get("reason"))
        return res
    except Exception as e:  # noqa
        log.warning("wechat notify: %s", e)
        return {"sent": 0, "reason": str(e)[:120]}


def _notify_buy_change(code, name, sector, price, signal, reason, when, tag="新开仓"):
    return _notify(
        f"【92K 打板·买入池·持仓】{tag}\n"
        f"{name}（{code}）{sector or ''}\n"
        f"买入价：{_fmt_num(price)} @ {when}\n"
        f"信号：{signal or '—'}\n理由：{reason or '—'}")


def _notify_sell_change(r, tag="自动结算", extra=None):
    """r: ops_items sell 行(dict); tag: 自动结算/手动结算/删除等"""
    lines = [
        f"{r.get('name') or r.get('code')}（{r.get('code')}）{r.get('sector') or ''}",
        f"买入 {r.get('entry_date') or '—'} {r.get('entry_time') or ''} @ {_fmt_num(r.get('entry_price'))}"
        f" → 卖出 {r.get('exit_date') or '—'} {r.get('exit_time') or ''} @ {_fmt_num(r.get('exit_price'))}"
        f"  {_fmt_pct(r.get('pnl_pct'))}",
        f"持有：{r.get('hold_days') if r.get('hold_days') is not None else '—'}天",
    ]
    er = r.get("exit_reason")
    if er:
        lines.append(f"卖出理由：{er}")
    if extra:
        lines.append(extra)
    return _notify(f"【92K 打板·卖出池·结算】{tag}\n" + "\n".join(lines))


def _wechat_status():
    return {"enabled": bool(_hooks()), "hooks": len(_hooks())}


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


def _sold_codes():
    """已卖出的股票集合(卖出池存在即视为“卖过”), 用于: 禁止重复卖出/同一票再买回"""
    return {r["code"] for r in db.query("SELECT code FROM ops_items WHERE pool='sell'")}


def _dedupe_sell_rows():
    """数据卫生: 同一只股票在卖出池只能保留一条(保留最早 id), 历史重复自动清理。
    返回删除条数。静默维护, 不推送。"""
    try:
        dups = db.query("SELECT code, MIN(id) keep, COUNT(*) n FROM ops_items "
                        "WHERE pool='sell' GROUP BY code HAVING COUNT(*)>1")
        removed = 0
        for d in dups:
            ids = [r["id"] for r in db.query(
                "SELECT id FROM ops_items WHERE pool='sell' AND code=? AND id<>?",
                (d["code"], d["keep"]))]
            for i in ids:
                db.execute("DELETE FROM ops_items WHERE id=?", (i,))
            removed += len(ids)
        if removed:
            log.info("卖出池去重: 清理重复卖出 %d 条(每票仅保留最早一条)", removed)
        return removed
    except Exception as e:  # noqa
        log.warning("dedupe sell rows: %s", e)
        return 0


def _clear_other_open_buys(code, keep_id, note="同码已结算, 同步清除"):
    """卖出后同步清理买入池里同代码的其它持仓(遗留脏行/重复买入), 防止同一票反复卖出"""
    try:
        with db.tx() as con:
            cur = con.execute("UPDATE ops_items SET status='ignored', note=?, updated_at=? "
                              "WHERE pool='buy' AND status='open' AND code=? AND id<>?",
                              (note, _now(), code, keep_id))
            n = cur.rowcount
        if n:
            log.info("同步清除买入池同码持仓 %s x%d", code, n)
    except Exception as e:  # noqa
        log.warning("clear other buys %s: %s", code, e)


# ---------------------------------------------------------------- 扫描
def sweep(view=None, ctx=None):
    """自动盯盘: 买点→买入池; 持仓卖点→卖出池; 模式候选→观察池。幂等、低写入。"""
    setup()
    if not view or not ctx:
        view, ctx = _signal_view()
    if not view or not ctx or not ctx.get("feats"):
        return {"state": "idle", "reason": "视图未就绪"}
    date = view.get("date") or _today()
    opened = 0
    closed = 0
    watched = 0
    can_trade = _in_window()
    _dedupe_sell_rows()                      # 卫生: 卖出池每票仅一条(清历史重复)
    sold = _sold_codes()                     # 已卖出集合: 防重复卖出/防卖后再买

    # ---- 1) 买点提示 → 买入池(仅在 09:25-14:59 交易窗口; 已卖出过的票不再买入) ----
    open_buys = _open_buy_codes()
    sigs = (view.get("signals") or {}).get("items") or []
    buy_sigs = [s for s in sigs if s.get("dir") == "buy"]
    if can_trade:
        for s in buy_sigs[:12]:
            code = s.get("code")
            if not code or code in open_buys or code in sold:
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
                    _notify_buy_change(code, name, s.get("sector"), price,
                                       s.get("signal"), reason, f"{date} {_now()[11:]}")
                except Exception as e:  # noqa
                    log.warning("wechat buy push: %s", e)
            except Exception as e:  # noqa
                log.warning("buy add %s: %s", code, e)

    # ---- 2) 持仓卖点 → 卖出池(结算, 仅在交易窗口) ----
    # 规则: 只卖“买入池持仓”(先买后卖); T+1 当日买入不可卖; 每只票卖出池仅一条(重复卖出跳过)
    if can_trade:
        rows = db.query("SELECT * FROM ops_items WHERE pool='buy' AND status='open'")
        for r in rows:
            code = r["code"]
            if code in sold:            # 该票已卖出过 → 不再卖(防重复)
                continue
            if str(r.get("entry_date") or "") >= str(date):   # T+1: 当日买入不可当日卖
                log.debug("T+1 拦截: %s 买入%s == 交易日%s, 次日方可卖", code,
                          r.get("entry_date"), date)
                continue
            f = ctx.get("feats", {}).get(code)
            if not f:
                continue
            today = f.get("today") or {}
            if today.get("limit_up"):
                # 今日涨停: 强势, 任何卖点(含止损纪律)不执行, 不提示卖出
                continue
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
            # 持有天数(自然日, 北京时间)
            try:
                ed = datetime.strptime(r["entry_date"] + " " + (r["entry_time"] or "00:00:00"),
                                       "%Y-%m-%d %H:%M:%S")
                from . import cn_time
                hold = max(1, (cn_time.now() - ed).days)
            except Exception:
                hold = 1
            pnl = round((price / entry - 1) * 100, 2)
            now = _now()
            try:
                with db.tx() as con:
                    # 数据库级 T+1 兜底: 当日买入(entry_date >= 交易日)的行即使被代码漏判也不会被卖出
                    cur = con.execute(
                        "UPDATE ops_items SET pool='sell', status='closed', "
                        "exit_date=?, exit_time=?, exit_price=?, exit_reason=?, pnl_pct=?, "
                        "hold_days=?, updated_at=? WHERE id=? AND entry_date < ?",
                        (date, now, round(price, 2), exit_reason, pnl, hold, now, r["id"], date))
                    if cur.rowcount == 0:
                        log.warning("T+1 拦截(SQL): %s 当日买入不可当日卖(entry_date=%s, date=%s)",
                                    code, r.get("entry_date"), date)
                        continue
                closed += 1
                sold.add(code)             # 标记已卖出, 同轮其余同码持仓不再卖
                _prompt(code, r.get("name", code), "sell", r.get("strategy"),
                        "卖出提示", price, exit_reason)
                # 持仓结算 → 卖出池数据变化, 立即推送
                row = dict(r, exit_date=date, exit_time=now, exit_price=round(price, 2),
                           exit_reason=exit_reason, pnl_pct=pnl, hold_days=hold)
                try:
                    _notify_sell_change(row, tag="自动结算（卖点）")
                except Exception as e:  # noqa
                    log.warning("wechat sell push: %s", e)
                # 卖出后同步清除买入池里该票的其它持仓(遗留/重复), 保证只结算一次
                _clear_other_open_buys(code, r["id"])
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
            from . import cn_time
            old = datetime.strptime(r["last_date"], "%Y-%m-%d").date()
            if (cn_time.today() - old).days > 5:
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
    if pool == "buy":
        rows = db.query("SELECT * FROM ops_items WHERE pool='buy' AND code=? AND status='open'",
                        (code,))
        with db.tx() as con:
            cur = con.execute("UPDATE ops_items SET status='ignored', updated_at=? "
                              "WHERE pool='buy' AND code=? AND status='open'", (_now(), code))
            n = cur.rowcount
        # 买入池数据变化 → 立即推送
        push = {"sent": 0, "reason": None}
        if n and rows:
            row = dict(rows[0], status="ignored")
            try:
                push = _notify_buy_change(code, row.get("name") or code, row.get("sector"),
                                          row.get("entry_price"), row.get("signal"),
                                          "人工移除：不再跟踪该持仓（已忽略）",
                                          f"{row.get('entry_date')} {row.get('entry_time') or ''}",
                                          tag="移除（忽略）")
            except Exception as e:  # noqa
                log.warning("wechat ignore push: %s", e)
                push = {"sent": 0, "reason": str(e)[:120]}
        return {"ok": True, "removed": n, "push": push}
    if pool == "watch":
        with db.tx() as con:
            cur = con.execute("UPDATE ops_items SET status='archived', updated_at=? "
                              "WHERE pool='watch' AND code=? AND status='open'", (_now(), code))
            n = cur.rowcount
        return {"ok": True, "removed": n}
    return {"ok": True, "removed": 0}


def manual_sell(code):
    """手动了结一笔买入池持仓(按当前价)。仅 09:25-14:59 交易时段可执行。
    规则同自动卖出: 先买后卖(仅持仓) · T+1 当日买入不可卖 · 每只票卖出池仅一条。"""
    setup()
    if not _in_window():
        wi = window_info()
        return {"ok": False, "error": f"仅交易时段({wi['start']}-{wi['end']})可买卖操作；当前 {wi['now']}"}
    view, ctx = _signal_view()
    if code in _sold_codes():
        return {"ok": False, "error": f"{code} 已在卖出池(每只股票只能卖出一次)；如需再操作请先删除该卖出记录再重新买入"}
    rows = db.query("SELECT * FROM ops_items WHERE pool='buy' AND status='open' AND code=?",
                    (code,))
    if not rows:
        return {"ok": False, "error": "买入池中无该持仓(先买入才能卖出)"}
    r = rows[0]
    trade_date = (view or {}).get("date") or _today()
    if str(r.get("entry_date") or "") >= str(trade_date):
        return {"ok": False,
                "error": f"{r.get('name', code)} 为当日买入({r.get('entry_date')})，A股 T+1：隔日({trade_date}之后)方可卖出"}
    price = _price_for(code, ctx) or r["entry_price"]
    pnl = round((price / r["entry_price"] - 1) * 100, 2) if r["entry_price"] else 0
    now = _now()
    with db.tx() as con:
        cur = con.execute(
            "UPDATE ops_items SET pool='sell', status='closed', exit_date=?, exit_time=?, "
            "exit_price=?, exit_reason='手动了结', pnl_pct=?, updated_at=? WHERE id=? "
            "AND entry_date < ?",
            (r["entry_date"], now, round(price, 2), pnl, now, r["id"], trade_date))
        if cur.rowcount == 0:
            return {"ok": False,
                    "error": f"{r.get('name', code)} 为当日买入，A股 T+1：隔日方可卖出（已被数据库拦截）"}
    # 卖出后同步清除买入池里同码的其它持仓
    _clear_other_open_buys(code, r["id"])
    _prompt(code, r.get("name", code), "sell", r.get("strategy"), "手动卖出", price, "手动了结")
    # 持仓了结 → 卖出池数据变化, 立即推送
    row = dict(r, exit_date=r["entry_date"], exit_time=now, exit_price=round(price, 2),
               exit_reason="手动了结", pnl_pct=pnl, updated_at=now)
    try:
        push = _notify_sell_change(row, tag="手动结算")
    except Exception as e:  # noqa
        log.warning("wechat manual sell push: %s", e)
        push = {"sent": 0, "reason": str(e)[:120]}
    return {"ok": True, "code": code, "price": round(price, 2), "pnl_pct": pnl, "push": push}


def _find_stock(key, ctx):
    """按代码或名称在全市场(real快照/ mock样本)查找股票。
    返回 ("found", code, name, sector) / ("ambiguous", [candidates]) / ("notfound", None)"""
    import re
    key = (key or "").strip()
    if not key:
        return "notfound", None
    quotes = {}
    industry = None
    if DATA_SOURCE == "real":
        from .real import market as real_mkt
        quotes = real_mkt.snapshot().get("quotes") or {}
        industry = real_mkt.industry_of
    else:
        st = (ctx or {}).get("stocks") or {}
        quotes = {c: {"name": m.get("name", c), "sector": m.get("sector", "")}
                  for c, m in st.items()}
        industry = lambda c: ((ctx or {}).get("stocks", {}).get(c) or {}).get("sector", "")  # noqa: E731
    # 纯 6 位代码
    if re.fullmatch(r"\d{6}", key):
        q = quotes.get(key)
        if q is None:
            return "notfound", None
        return "found", (key, q["name"], (industry(key) if industry else q.get("sector")) or "")
    # 名称: 先精确, 再包含; 唯一才直接命中
    items = [(c, qq) for c, qq in quotes.items()]
    exact = [(c, qq) for c, qq in items if qq["name"] == key]
    pool = exact or [(c, qq) for c, qq in items if key in qq["name"]]
    if not pool:
        return "notfound", None
    if len(pool) == 1:
        c, qq = pool[0]
        return "found", (c, qq["name"], (industry(c) if industry else qq.get("sector")) or "")
    cands = [{"code": c, "name": qq["name"],
              "sector": (industry(c) if industry else qq.get("sector")) or ""}
             for c, qq in pool[:10]]
    return "ambiguous", cands


def _manual_score(code, ctx):
    """手动观察的算法评分: 与自动池同一套引擎(feat_for_code + 龙头六维 L-01..L-06)。
    返回 (score0-100, items[])；异常时返回 (None, []) 不阻断入库。"""
    try:
        stats = (ctx or {}).get("today_stats") or {}
        if DATA_SOURCE == "real":
            from .real import analyze_real
            feat, meta = analyze_real.feat_for_code(code)
        else:
            feat = ((ctx or {}).get("feats") or {}).get(code)
            meta = ((ctx or {}).get("stocks") or {}).get(code) or {}
        if not feat:
            return None, []
        from .core import leaders
        total, items = leaders._conds(feat, stats, meta or None)
        return round(total, 1), items
    except Exception as e:  # noqa
        log.warning("manual score %s: %s", code, e)
        return None, []


def manual_watch(q):
    """手动加入观察池(支持股票代码或名称)。记录时间=加入时; 观察理由=手动加入;
    评分由算法(龙头六维 L-01..L-06)自动给出。"""
    setup()
    q = (q or "").strip()
    if not q:
        return {"ok": False, "error": "请输入股票代码或名称"}
    view, ctx = _signal_view()
    state, got = _find_stock(q, ctx)
    if state == "notfound":
        return {"ok": False, "error": "未找到该股票，请核对代码或名称"}
    if state == "ambiguous":
        return {"ok": False,
                "error": f"名称“{q}”匹配到 {len(got)} 只股票，请改输入 6 位代码或从下方列表点选",
                "candidates": got}
    code, name, sector = got
    dup = db.query_one("SELECT id FROM ops_items WHERE pool='watch' AND code=? AND status='open'",
                       (code,))
    if dup:
        return {"ok": False, "error": f"{name}（{code}）已在观察池"}
    score, items = _manual_score(code, ctx)
    now = _now()
    date = _today()
    # 记录时间=加入时: entry_date/entry_time 记完整时刻, last_date 记日期
    try:
        db.execute(
            "INSERT INTO ops_items(code,name,sector,pool,status,reason,last_date,entry_date,"
            "entry_time,created_at,updated_at,score,note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, sector, "watch", "open", "手动加入", date, date, now[11:], now, now,
             score, "手动加入观察(算法评分)"))
    except Exception as e:  # noqa
        log.warning("manual watch insert: %s", e)
        return {"ok": False, "error": str(e)[:150]}
    _prompt(code, name, "watch", None, "手动观察", _price_for(code, ctx),
            f"手动加入（算法评分 {score if score is not None else '—'}）")
    log.info("manual watch added %s %s score=%s", code, name, score)
    return {"ok": True, "code": code, "name": name, "sector": sector,
            "score": score, "dims": items or [],
            "reason": "手动加入", "date": date}


def delete_sell(item_id):
    """管理删除卖出池记录(删除也属数据变化, 推送微信群)"""
    setup()
    row = db.query_one("SELECT * FROM ops_items WHERE id=?", (item_id,))
    if not row:
        return {"ok": False, "error": "记录不存在"}
    if row["pool"] != "sell":
        return {"ok": False, "error": "仅卖出池记录可删除(其它池请用移除/忽略)"}
    db.execute("DELETE FROM ops_items WHERE id=?", (item_id,))
    log.info("ops delete sell id=%s name=%s", item_id, row["name"])
    try:
        push = _notify_sell_change(row, tag="删除记录",
                                   extra=f"管理删除：该条卖出记录已从卖出池移除（id={item_id}）")
    except Exception as e:  # noqa
        log.warning("wechat delete push: %s", e)
        push = {"sent": 0, "reason": str(e)[:120]}
    return {"ok": True, "deleted": 1, "name": row["name"], "push": push}


def remove_buy(item_id):
    """移除买入池持仓: 直接把该条数据行从表里删除(区别于忽略/归档), 并推送微信群"""
    setup()
    row = db.query_one("SELECT * FROM ops_items WHERE id=?", (item_id,))
    if not row:
        return {"ok": False, "error": "记录不存在"}
    if row["pool"] != "buy":
        return {"ok": False, "error": "仅买入池持仓可移除(卖出池请用删除)"}
    db.execute("DELETE FROM ops_items WHERE id=?", (item_id,))
    log.info("ops remove buy id=%s name=%s", item_id, row["name"])
    try:
        msg = (f"【92K 打板·买入池·持仓】移除（删除数据行）\n"
               f"{row.get('name') or row.get('code')}（{row.get('code')}）{row.get('sector') or ''}\n"
               f"买入：{row.get('entry_date') or '—'} {row.get('entry_time') or ''} "
               f"@ {_fmt_num(row.get('entry_price'))}\n"
               f"信号：{row.get('signal') or row.get('strategy') or '—'}\n"
               f"说明：该持仓已从买入池移除删除（id={item_id}）")
        push = _notify(msg)
    except Exception as e:  # noqa
        log.warning("wechat remove buy push: %s", e)
        push = {"sent": 0, "reason": str(e)[:120]}
    return {"ok": True, "removed": 1, "name": row["name"], "push": push}


# ---------------------------------------------------------------- 模拟数据(仅用于推送联调, 手动触发)
_DEMO_STOCKS = [
    {"code": "601989", "name": "中国重工", "sector": "船舶制造", "buy": 5.19},
    {"code": "600150", "name": "中国船舶", "sector": "船舶制造", "buy": 33.10},
    {"code": "600685", "name": "中船防务", "sector": "船舶制造", "buy": 21.35},
    {"code": "000592", "name": "平潭发展", "sector": "农林牧渔", "buy": 7.12},
    {"code": "002415", "name": "海康威视", "sector": "电子器件", "buy": 32.50},
    {"code": "600519", "name": "贵州茅台", "sector": "酿酒行业", "buy": 1330.0},
]


def add_demo_buy():
    """新增一条模拟持仓(买入池)并推送微信群 —— 手动点击联调用, 测完可点“移除”删除"""
    setup()
    taken = {r["code"] for r in db.query(
        "SELECT code FROM ops_items WHERE pool='buy' AND status='open'")}
    taken |= _sold_codes()   # 已卖出过的票不再演示买入(遵守“只卖一次”)
    pick = next((s for s in _DEMO_STOCKS if s["code"] not in taken), None)
    if not pick:
        return {"ok": False, "error": "示例标的均已占用(持仓/已卖出)，请先移除/删除相关记录再试"}
    now = _now()
    date = _today()
    reason = f"模拟持仓：微信推送联调（{date} {now[11:]}，测试后可点“移除”删除）"
    try:
        db.execute(
            "INSERT INTO ops_items(code,name,sector,pool,status,strategy,signal,reason,"
            "entry_date,entry_time,entry_price,created_at,updated_at,last_date,note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pick["code"], pick["name"], pick["sector"], "buy", "open", "demo_push",
             "模拟买点(推送联调)", reason, date, now[11:], pick["buy"], now, now, date,
             "[模拟数据·用于推送联调，可移除]"))
    except Exception as e:  # noqa
        log.warning("demo buy: %s", e)
        return {"ok": False, "error": str(e)[:150]}
    push = _notify_buy_change(pick["code"], pick["name"], pick["sector"], pick["buy"],
                              "模拟买点(推送联调)", reason, f"{date} {now[11:]}",
                              tag="新开仓(模拟测试)")
    return {"ok": True, "code": pick["code"], "name": pick["name"],
            "entry_price": pick["buy"], "sector": pick["sector"], "push": push}


def add_demo_sell():
    """新增一条模拟已结算(卖出池)并推送微信群 —— 手动点击联调用, 测完可删除"""
    setup()
    taken = {r["code"] for r in db.query("SELECT code FROM ops_items WHERE pool='sell'")}
    pick = next((s for s in _DEMO_STOCKS if s["code"] not in taken), None)
    if not pick:
        return {"ok": False, "error": "示例标的均已在卖出池，请先删除一条再试"}
    now = _now()
    date = _today()
    entry = round(pick["buy"] * 0.972, 2)   # 模拟昨日买入
    pnl = round((pick["buy"] / entry - 1) * 100, 2)
    note = "[模拟数据·用于推送联调，可删除]"
    reason = "模拟买入：微信推送联调（昨日买入）"
    exit_reason = "模拟卖出：用于测试卖出池结算推送"
    try:
        with db.tx() as con:
            cur = con.execute(
                "INSERT INTO ops_items(code,name,sector,pool,status,strategy,signal,reason,"
                "entry_date,entry_time,entry_price,exit_date,exit_time,exit_price,exit_reason,"
                "pnl_pct,hold_days,created_at,updated_at,last_date,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pick["code"], pick["name"], pick["sector"], "sell", "closed", "demo_push",
                 "模拟结算(推送联调)", reason, date, "09:35:00", entry, date, now[11:],
                 pick["buy"], exit_reason, pnl, 1, now, now, date, note))
            row_id = cur.lastrowid
        row = db.query_one("SELECT * FROM ops_items WHERE id=?", (row_id,))
    except Exception as e:  # noqa
        log.warning("demo sell: %s", e)
        return {"ok": False, "error": str(e)[:150]}
    push = _notify_sell_change(row, tag="模拟结算（测试）",
                               extra="模拟数据：用于测试卖出池结算推送，测完可在页面删除")
    return {"ok": True, "code": pick["code"], "name": pick["name"], "id": row_id,
            "pnl_pct": pnl, "push": push}


def t1_fix_sells(rollback=False):
    """T+1 数据修复: 找出“卖出日期<=买入日期”的真实误卖记录(排除模拟演示数据)。
    rollback=True 时把这些卖出记录回滚为买入池持仓。"""
    rows = db.query(
        "SELECT * FROM ops_items WHERE pool='sell' AND exit_date IS NOT NULL "
        "AND entry_date IS NOT NULL AND exit_date <= entry_date "
        "AND (strategy IS NULL OR strategy != 'demo_push') "
        "AND (note IS NULL OR note NOT LIKE '%模拟%') ORDER BY id")
    out = [{"id": r["id"], "code": r["code"], "name": r.get("name"),
            "entry_date": r.get("entry_date"), "exit_date": r.get("exit_date"),
            "reason": r.get("exit_reason")} for r in rows]
    restored = []
    if rollback and rows:
        now = _now()
        for r in rows:
            try:
                db.execute(
                    "UPDATE ops_items SET pool='buy', status='open', "
                    "exit_date=NULL, exit_time=NULL, exit_price=NULL, exit_reason=NULL, "
                    "pnl_pct=NULL, hold_days=NULL, updated_at=?, note=COALESCE(note,'')||'（T+1误卖已回滚，持仓恢复）' "
                    "WHERE id=?", (now, r["id"]))
                restored.append({"id": r["id"], "code": r["code"], "name": r.get("name")})
            except Exception as e:  # noqa
                log.warning("t1 rollback %s: %s", r["id"], e)
        log.info("T+1 修复: 发现 %d 条当日买卖, 回滚 %d 条", len(rows), len(restored))
    return {"ok": True, "found": out, "rollback": bool(rollback), "restored": restored}
