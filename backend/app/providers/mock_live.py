"""模拟实时行情(仅 mock 模式)：在最近一根日线高低区间内做随机游走，营造“盘中”效果。"""
import random
import threading
import time

_lock = threading.Lock()
_quotes = {}
_ts = 0.0


def init(base_rows):
    """base_rows: [{code,name,close,high,low,pct,amount}] 以最近收盘为锚"""
    with _lock:
        _quotes.clear()
        for r in base_rows:
            mid = (r["high"] + r["low"]) / 2
            _quotes[r["code"]] = {
                "name": r["name"], "pre_close": r["pre_close"] or r["close"],
                "base": r["close"], "mid": mid, "price": r["close"],
                "high": r["high"], "low": r["low"], "amount": r.get("amount", 0),
                "base_pct": r.get("pct", 0),
            }
        _ts = time.time()


def tick(seed=None, n_move=8):
    rng = random.Random(seed)
    with _lock:
        codes = list(_quotes.keys())
        for code in rng.sample(codes, min(n_move, len(codes))):
            q = _quotes[code]
            span = max(q["high"] - q["low"], q["base"] * 0.005)
            q["price"] = max(q["low"] * 0.995, min(q["high"] * 1.005,
                                                   q["price"] + rng.uniform(-0.18, 0.18) * span))
        _ts = time.time()


def snapshot(limit=None, min_amount=0.0):
    with _lock:
        rows = []
        for code, q in _quotes.items():
            pre = q["pre_close"] or q["base"] or 1
            pct = (q["price"] / pre - 1) * 100
            rows.append({
                "code": code, "name": q["name"], "price": round(q["price"], 2),
                "pre_close": round(pre, 2), "pct": round(pct, 2),
                "high": q["high"], "low": q["low"],
                "amount": q["amount"], "ts": round(_ts, 1),
                "base_pct": q.get("base_pct", 0),
            })
    rows.sort(key=lambda r: -r["pct"])
    return rows


def state():
    return {"tick_ts": round(_ts, 1), "stocks": len(_quotes)}
