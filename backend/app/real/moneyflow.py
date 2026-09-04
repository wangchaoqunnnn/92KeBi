"""个股资金面(新浪资金流, 按交易日口径) + 当日量能对比工具。
- 主力/超大单净流入: 新浪 MoneyFlow.ssl_qsfx_zjlrqs(单位:元, 每个交易日一条, 盘后更新)
- 当日量能: 实时累计成交额 vs 近5日均额(全日折算), 输出绝对数值与倍率(非百分比)
- 主动净买(暗盘口径): 盘口 外盘-内盘 差 × 价格(实时, 来源腾讯盘口字段)
"""
import logging
import time
import urllib.request
import urllib.parse

log = logging.getLogger("kb.moneyflow")

_cache = {}
_TTL = 120.0


def _sym(code):
    c = str(code).zfill(6)
    return ("sh" if c.startswith(("60", "68", "9")) else "sz") + c


def fetch_flow_rows(code, rows_n=6):
    """新浪资金流最近 rows_n 个交易日 → list[{date,trade,net_yi,super_yi,amount_yi}]"""
    now = time.time()
    key = f"{code}:{rows_n}"
    if key in _cache and now - _cache[key][0] < _TTL:
        return _cache[key][1]
    rows = []
    try:
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "MoneyFlow.ssl_qsfx_zjlrqs?" + urllib.parse.urlencode({"daima": _sym(code)}))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        import json as _j
        raw = _j.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
        arr = raw if isinstance(raw, list) else []
        chunk = list(arr[:rows_n])          # 接口返回最新在前
        chunk.reverse()                     # → 升序(旧→新)
        for r in chunk:
            try:
                net = float(r.get("netamount") or 0)
                ratio = float(r.get("ratioamount") or 0)
                amt = abs(net / ratio) if ratio else None
                rows.append({
                    "date": str(r.get("opendate") or "")[:10],
                    "trade": float(r.get("trade") or 0),
                    "net_yi": round(net / 1e8, 3),
                    "super_yi": round(float(r.get("r0_net") or 0) / 1e8, 3),
                    "amount_yi": round(amt / 1e8, 2) if amt else None,
                })
            except Exception:
                continue
    except Exception as e:
        log.warning("moneyflow %s: %s", code, e)
    rows = rows[-rows_n:]
    _cache[key] = (now, rows)
    return rows

def _elapsed_fraction():
    """A股已交易时间占比(09:30-11:30 + 13:00-15:00, 共240分钟)"""
    from datetime import datetime
    now = datetime.now()
    mins = now.hour * 60 + now.minute
    if mins < 9 * 60 + 30:
        return 0.0
    if 11 * 60 + 30 <= mins <= 13 * 60:
        return 120 / 240
    if mins > 15 * 60:
        return 1.0
    if mins >= 9 * 60 + 30:
        if mins <= 11 * 60 + 29:
            return (mins - (9 * 60 + 30)) / 240
        if mins >= 13 * 60 + 1:
            return (120 + mins - 13 * 60) / 240
    return 0.0


def stock_flow(code, quote=None, price=None):
    """返回个股卡片数据: 当日量能/主动净买(暗盘口径)/主力净流入"""
    out = {"code": code, "asof": time.strftime("%Y-%m-%d %H:%M")}
    amount = None
    outer = inner = None
    if quote:
        amount = quote.get("amount") or 0
        outer = quote.get("outer")
        inner = quote.get("inner")
        price = price or quote.get("price")
    rows = fetch_flow_rows(code, 6)
    # ---- 当日量能 ----
    amount_yi = round(amount / 1e8, 2) if amount else None
    frac = _elapsed_fraction() or 1.0
    whole_yi = round(amount_yi / max(frac, 0.02), 1) if amount_yi else None
    valid_amt = [r["amount_yi"] for r in rows if r.get("amount_yi")]
    avg5_yi = round(sum(valid_amt[-5:]) / len(valid_amt[-5:]), 1) if valid_amt else None
    vol_state = None
    if whole_yi and avg5_yi:
        times = round(whole_yi / avg5_yi, 2)
        vol_state = "放量" if times >= 1.2 else ("缩量" if times <= 0.8 else "持平")
        out["volume"] = {"today_yi": amount_yi, "whole_day_yi": whole_yi,
                         "avg5_yi": avg5_yi, "times": times,
                         "state": vol_state, "elapsed": round(frac, 3),
                         "note": "全日折算 vs 近5交易日日均成交额(绝对亿元, 非百分比)"}
    # ---- 主动净买(暗盘口径: 外盘-内盘) ----
    if outer is not None and inner is not None and price:
        diff = (outer - inner) * 100 * price  # 手→股×价
        out["active_net"] = {"diff_yi": round(diff / 1e8, 3), "outer": outer,
                             "inner": inner, "source": "盘口外盘-内盘(实时)",
                             "state": "净买" if diff > 0 else "净卖"}
    # ---- 主力/超大单净流入(最近交易日) ----
    if rows:
        r = rows[-1]
        out["main_net"] = {"date": r["date"], "net_yi": r["net_yi"],
                           "super_yi": r["super_yi"],
                           "source": "新浪资金流(交易日后更新)"}
    return out
