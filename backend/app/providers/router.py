"""多行情源路由：健康度 + 延迟择优 + 故障自动切换（需求：多备份数据源、低延迟）。

- 快照/批量实时: 默认按滚动平均延迟取优(腾讯 qt / 新浪 hq), 失败自动切源并短暂隔离
- 日K: 新浪优先、腾讯备份(前复权, 过滤当日盘中未收盘K)
- 延迟与健康状态可在 /api/admin/status、/api/market/live 观察
"""
import logging
import statistics
import time
from collections import deque

from . import sina, tencent

log = logging.getLogger("kb.router")

_health = {
    "tencent": {"ok": True, "seq": 0, "fails": 0, "lats": deque(maxlen=6), "last_ms": None},
    "sina_hq": {"ok": True, "seq": 0, "fails": 0, "lats": deque(maxlen=6), "last_ms": None},
    "sina_market": {"ok": True, "seq": 0, "fails": 0, "lats": deque(maxlen=6), "last_ms": None},
    "sina_kline": {"ok": True, "fails": 0, "lats": deque(maxlen=5)},
    "tencent_kline": {"ok": True, "fails": 0, "lats": deque(maxlen=5)},
}

PREF = None  # 强制源(env REAL_PREF) 可选 'tencent' | 'sina_hq'


def _record(name, ms, ok=True):
    h = _health.setdefault(name, {})
    h.setdefault("seq", 0)
    h.setdefault("fails", 0)
    h.setdefault("ok", True)
    h.setdefault("lats", deque(maxlen=6))
    h["seq"] += 1
    h["last_ms"] = round(ms, 1)
    if ok:
        h["fails"] = 0
        h["ok"] = True
        h["lats"].append(ms)
    else:
        h["fails"] += 1
        if h["fails"] >= 2:
            h["ok"] = False


def _pick(names):
    """在健康源里选平均延迟最小者; 全不健康则重置后挑顺序第一个"""
    healthy = [n for n in names if _health.get(n, {}).get("ok")]
    if not healthy:
        for n in names:
            _health[n]["ok"] = True
        healthy = list(names)
    def key(n):
        lats = list(_health.get(n, {}).get("lats", []))
        return (statistics.mean(lats) if lats else 999.0, _health.get(n, {}).get("fails", 0))
    return min(healthy, key=key)


def fast_quote_sources():
    if PREF == "tencent":
        return ["tencent", "sina_hq"]
    if PREF == "sina_hq":
        return ["sina_hq", "tencent"]
    return None  # 自动


def fetch_fast_quotes(symbols):
    """并行批量实时行情(整 tick 单源): 返回 (source, quotes_dict) 或 (None,None)"""
    if not symbols:
        return None, None
    names = fast_quote_sources() or ["tencent", "sina_hq"]
    first = _pick(names)
    order = [first] + [n for n in names if n != first]
    last_err = None
    for name in order:
        t0 = time.time()
        try:
            if name == "tencent":
                q = tencent.fetch_hq_quotes(symbols)
            else:
                q = sina.fetch_hq_quotes(symbols)
            ms = (time.time() - t0) * 1000
            _record(name, ms, ok=True)
            if q:
                log.debug("fast quotes source=%s %d symbols %.0fms", name, len(q), ms)
                return name, q
            raise ConnectionError("empty")
        except Exception as e:  # noqa
            ms = (time.time() - t0) * 1000
            _record(name, ms, ok=False)
            last_err = e
            log.warning("fast quotes source %s failed: %s", name, e)
    return None, None


def fetch_kline_any(code, n=520, symbol=None):
    """日K: 新浪主/腾讯备(腾讯过滤盘中当日未收盘K)。失败均抛错由调用方兜底。"""
    import datetime as _dt
    names = ["sina_kline", "tencent_kline"]
    if PREF == "tencent":
        names = ["tencent_kline", "sina_kline"]
    last_err = None
    today = _dt.date.today().strftime("%Y-%m-%d")
    for name in names:
        t0 = time.time()
        try:
            if name == "sina_kline":
                rows = sina.fetch_kline(code, n=n, symbol=symbol)
            else:
                rows = tencent.fetch_kline(code, n=n, symbol=symbol)
                # 腾讯含当日盘中未收盘K → 剔除(与新浪口径一致)
                if rows and rows[-1]["day"] == today:
                    rows = rows[:-1]
            _record(name, (time.time() - t0) * 1000, ok=True)
            if rows:
                return rows
            raise ConnectionError("empty kline")
        except Exception as e:  # noqa
            _record(name, (time.time() - t0) * 1000, ok=False)
            last_err = e
    raise last_err or ConnectionError("no kline source available")


def health_status():
    out = {}
    for name, h in _health.items():
        out[name] = {"ok": h.get("ok", True), "fails": h.get("fails", 0),
                     "last_ms": h.get("last_ms"),
                     "avg_ms": round(statistics.mean(h["lats"]), 1) if h.get("lats") else None,
                     "calls": h.get("seq", 0)}
    return out
