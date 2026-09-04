"""腾讯财经公开接口（备用行情源，低延迟批量实时 + 前复权日K）。
字段为 qt.gtimg.cn 标准字段(以 '~' 分隔)；与 sina 互补做双数据源冗余。
"""
import json
import logging
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("kb.tencent")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0"}
CHUNK = 900


def _http(url, timeout=10, tries=2):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa
            last = e
            time.sleep(0.3)
    raise last or ConnectionError(url)


def to_symbol(code):
    """与新浪同前缀: sh/sz/bj"""
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "9")):
        return "sh" + c
    if c.startswith(("4", "8", "92")):
        return "bj" + c
    return "sz" + c


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_hq_quotes(symbols, timeout=8):
    """批量实时行情(块并发)。返回 dict code->quote"""
    if not symbols:
        return {}
    out = {}

    def one(chunk):
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        raw = _http(url, timeout)
        txt = raw.decode("gbk", "ignore")
        res = {}
        for line in txt.splitlines():
            line = line.strip()
            if "=" not in line or not line.startswith("v_"):
                continue
            head, _, body = line.partition("=")
            sym = head.replace("v_", "").strip()
            code = sym[2:]
            f = body.strip().strip(';').strip('"').split("~")
            if len(f) < 50 or not f[1]:
                continue
            ts = str(f[30]) if len(f) > 30 else ""
            date = ts[:8] if len(ts) >= 8 and ts[:4].isdigit() else time.strftime("%Y-%m-%d")
            hms = f"{ts[8:10]}:{ts[10:12]}:{ts[12:14]}" if len(ts) >= 14 else ""
            res[code] = {
                "code": code, "symbol": sym, "name": f[1],
                "price": _f(f[3]), "pre_close": _f(f[4]), "open": _f(f[5]),
                "high": _f(f[33]), "low": _f(f[34]),
                "volume": _f(f[36]), "amount": (_f(f[37]) or 0) * 1e4,
                "turnover": _f(f[38]), "pe": _f(f[39]),
                "float_cap": _f(f[44]), "pb": _f(f[46]),
                "limit_up_px": _f(f[47]), "limit_down_px": _f(f[48]),
                "date": date, "time": hms,
            }
        return res

    chunks = [symbols[i:i + CHUNK] for i in range(0, len(symbols), CHUNK)]
    with ThreadPoolExecutor(max(4, min(20, len(chunks) or 1))) as ex:
        futs = [ex.submit(one, c) for c in chunks]
        for fu in as_completed(futs):
            try:
                out.update(fu.result())
            except Exception as e:  # noqa
                log.warning("tencent chunk fail: %s", e)
    if not out and chunks:
        raise ConnectionError("tencent returned no quotes")
    return out


def fetch_kline(code, n=520, symbol=None):
    """前复权日K(与 sina.fetch_kline 同构)。盘中含当日未收盘K → 由调用方过滤。
    返回升序 [{day,open,high,low,close,volume}]"""
    sym = symbol or to_symbol(code)
    url = "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=" + \
          urllib.parse.quote(f"{sym},day,,,{int(n)},qfq")
    j = json.loads(_http(url, timeout=15).decode("utf-8", "ignore"))
    d = (j.get("data") or {}).get(sym, {})
    rows = d.get("qfqday") or d.get("day") or []
    out = []
    for r in rows:
        if not r or len(r) < 6:
            continue
        try:
            out.append({"day": str(r[0]), "open": float(r[1]), "close": float(r[2]),
                        "high": float(r[3]), "low": float(r[4]), "volume": float(r[5])})
        except (TypeError, ValueError):
            continue
    return out
