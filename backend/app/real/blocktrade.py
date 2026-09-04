"""历史大宗交易(东财数据中心 RPT_BLOCKTRADE_STA, 每股每日聚合统计)。
单位: VOLUME=万股, DEAL_AMT=万元, 折溢价率 PREMIUM_RATIO(负=折价)。
进程内缓存30分钟; 失败返回空(前端提示)。
"""
import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger("kb.blocktrade")
_cache = {}
_TTL = 1800.0
URL = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
       "?columns=ALL&pageSize={size}&pageNumber=1&source=WEB&client=WEB"
       "&reportName=RPT_BLOCKTRADE_STA&sortColumns=TRADE_DATE&sortTypes=-1&filter={flt}")


def fetch_block_trades(code, limit=0):
    """近1年(滚动365日)内全部大宗交易(按日聚合)或最近 limit 条(limit>0时)。
    limit=0 → 1年全量(分页抓取, 上限800条)"""
    now = time.time()
    mode = f"year" if not limit else f"top{limit}"
    key = f"{code}:{mode}"
    if key in _cache and now - _cache[key][0] < _TTL:
        return _cache[key][1]
    out = {"code": code, "rows": [], "error": None}
    try:
        from datetime import date, timedelta
        if limit:
            flt = f'(SECURITY_CODE="{code}")'
            size, pages = limit, 1
        else:
            cutoff = (date.today() - timedelta(days=365)).isoformat()
            flt = f'(SECURITY_CODE="{code}")(TRADE_DATE>=\'{cutoff}\')'
            size, pages = 100, 8
        rows = []
        for pn in range(1, pages + 1):
            pflt = urllib.parse.quote(flt)
            url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
                   "?columns=ALL&pageSize={s}&pageNumber={p}&source=WEB&client=WEB"
                   "&reportName=RPT_BLOCKTRADE_STA&sortColumns=TRADE_DATE&sortTypes=-1&filter={f}"
                   ).format(s=size, p=pn, f=pflt)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0 Safari/537.36",
                "Referer": "https://data.eastmoney.com/"})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
            j = json.loads(raw)
            data = ((j.get("result") or {}).get("data")) or []
            if not data:
                break
            for r in data:
                try:
                    rows.append({
                        "date": str(r.get("TRADE_DATE") or "")[:10],
                        "name": r.get("SECURITY_NAME_ABBR"),
                        "deal_num": r.get("DEAL_NUM"),
                        "volume_wan": r.get("VOLUME"),          # 万股
                        "amount_wan": r.get("DEAL_AMT"),        # 万元
                        "avg_price": r.get("AVERAGE_PRICE"),
                        "close": r.get("CLOSE_PRICE"),
                        "premium_pct": round((r.get("PREMIUM_RATIO") or 0) * 100, 2) if r.get("PREMIUM_RATIO") is not None else None,
                        "chg_after1": r.get("D1_CLOSE_ADJCHRATE"),
                        "chg_after5": r.get("D5_CLOSE_ADJCHRATE"),
                        "chg_after10": r.get("D10_CLOSE_ADJCHRATE"),
                    })
                except Exception:
                    continue
            if len(data) < size:
                break
        out["rows"] = rows
        out["range_label"] = "近1年" if not limit else f"最近{limit}条"
        out["asof"] = time.strftime("%Y-%m-%d %H:%M")
        # ---- 统计 ----
        rs = rows
        if rs:
            def g(k):
                return [x[k] for x in rs if x.get(k) is not None]
            amts = g("amount_wan")
            vols = g("volume_wan")
            prem = g("premium_pct")
            out["stats"] = {
                "n": len(rs),
                "total_amt_yi": round(sum(amts) / 1e4, 2) if amts else None,   # 万元→亿元
                "total_vol_wan": round(sum(vols), 1) if vols else None,
                "avg_premium_pct": round(sum(prem) / len(prem), 2) if prem else None,
                "discount_n": sum(1 for p in prem if p < 0),
                "discount_ratio": round(sum(1 for p in prem if p < 0) / len(prem) * 100, 1) if prem else None,
                "max_amount_yi": round(max(amts) / 1e4, 2) if amts else None,
                "date_from": rs[-1]["date"] if rs else None,
                "date_to": rs[0]["date"] if rs else None,
            }
    except Exception as e:
        log.warning("blocktrade %s: %s", code, e)
        out["error"] = str(e)[:160]
    _cache[key] = (now, out)
    return out
