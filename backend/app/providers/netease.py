"""网易财经批量实时行情(第三价格源, 无签名/无令牌, 稳定)。
接口: http://api.money.126.net/data/feed/{codes}?callback=_ntes_quote_callback
  代码格式: 0+code = 沪市, 1+code = 深市 (北交所不支持, 自动跳过)
返回 JSONP, 字段: price/percent/yestclose/open/high/low/volume/turnover/name
用途: 腾讯、新浪 hq 都不可用时, 仍能按内置全市场名单取到报价。
"""
import json
import logging
import re
import urllib.request

log = logging.getLogger("kb.netease")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Referer": "http://quotes.money.126.net/",
    "Accept": "*/*",
}
BASE = "http://api.money.126.net/data/feed/"
CHUNK = 300


def _pfx(code):
    c = str(code)
    if c.startswith(("4", "8", "92")):
        return None          # 北交所网易不支持
    return ("0" if c.startswith("6") else "1") + c


def _fetch(symbols):
    url = BASE + ",".join(symbols) + "?callback=_ntes_quote_callback"
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "ignore")
    m = re.search(r"\((.*)\)\s*;?\s*$", raw, re.S)
    body = m.group(1) if m else raw
    return json.loads(body)


def fetch_hq_quotes(symbols):
    """返回 {code6: {price,pre_close,open,high,low,volume,turnover,pct,name}}; 失败抛错"""
    syms = []
    for s in symbols or []:
        c = str(s)
        if len(c) == 6 and c.isdigit():
            p = _pfx(c)
            if p:
                syms.append(p)
    if not syms:
        return {}
    out = {}
    for i in range(0, len(syms), CHUNK):
        part_syms = syms[i:i + CHUNK]
        try:
            data = _fetch(part_syms)
        except Exception as e:  # noqa
            log.warning("netease chunk %d fail: %s", i // CHUNK + 1, str(e)[:80])
            continue
        if not isinstance(data, dict):
            continue
        # 返回键为 '0xxxxx'/'1xxxxx', 映射回 6 位代码
        for k, v in data.items():
            c = str(k)[1:]
            if len(c) != 6 or not c.isdigit():
                continue
            try:
                out[c] = {
                    "name": v.get("name"),
                    "price": v.get("price"),
                    "pre_close": v.get("yestclose"),
                    "open": v.get("open"),
                    "high": v.get("high"),
                    "low": v.get("low"),
                    "volume": v.get("volume"),
                    "turnover": v.get("turnover"),
                    "pct": v.get("percent"),
                    "amount": v.get("turnover") and None,
                }
            except Exception:
                continue
    return out
