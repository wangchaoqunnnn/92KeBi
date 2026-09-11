"""东方财富 行业板块成分（备用数据源）。
用途: 新浪 Market_Center 被限流(HTTP 456)时, 用东财 push2 接口获取“行业板块 → 成分股”，
生成与新浪同结构的 {行业名: [6位代码]}, 供 market.ensure_industry_cache 回退使用。
接口: push2.eastmoney.com/api/qt/clist/get
  - 行业板块列表: fs=m:90+t:2
  - 板块成分股  : fs=b:BKxxxx (分页 pz=100)
失败(网络/被墙/格式变化)时返回 {}，由上层继续回退, 不影响服务。
"""
import json
import logging
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("kb.eastmoney")

BASE = "https://push2.eastmoney.com/api/qt/clist/get"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _get(params, timeout=12, tries=2):
    q = urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(BASE + "?" + q, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception as e:  # noqa
            last = e
            time.sleep(0.3 * (i + 1))
    raise last or ConnectionError(BASE)


def _diff(data):
    d = (data or {}).get("data") or {}
    diff = d.get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    return diff


def fetch_boards():
    """行业板块列表 → {板块名: BK代码}"""
    out = {}
    for pn in range(1, 4):
        try:
            rows = _diff(_get({"pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                               "fid": "f3", "fs": "m:90+t:2", "fields": "f12,f14"}))
        except Exception as e:  # noqa
            log.warning("eastmoney boards p%d: %s", pn, e)
            break
        if not rows:
            break
        for it in rows:
            name, code = it.get("f14"), it.get("f12")
            if name and code:
                out[name] = code
        if len(rows) < 100:
            break
    return out


def fetch_members(bk):
    """单个板块成分 → [6位代码]"""
    codes = []
    for pn in range(1, 6):   # 最多 500 只
        try:
            rows = _diff(_get({"pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                               "fid": "f3", "fs": f"b:{bk}", "fields": "f12,f14"}))
        except Exception as e:  # noqa
            log.warning("eastmoney members %s p%d: %s", bk, pn, e)
            break
        if not rows:
            break
        for it in rows:
            c = str(it.get("f12") or "").zfill(6)
            if len(c) == 6 and c.isdigit():
                codes.append(c)
        if len(rows) < 100:
            break
    return list(dict.fromkeys(codes))


def fetch_industry_map(threads=6):
    """返回 {行业名: [6位代码]}; 失败/无数据返回 {}"""
    boards = fetch_boards()
    if len(boards) < 10:
        log.warning("eastmoney 板块列表不足(%d), 视为不可用", len(boards))
        return {}
    out = {}
    with ThreadPoolExecutor(max(2, min(threads, 10))) as ex:
        futs = {ex.submit(fetch_members, bk): name for name, bk in boards.items()}
        for fu in as_completed(futs):
            name = futs[fu]
            try:
                codes = fu.result()
            except Exception as e:  # noqa
                log.warning("eastmoney %s: %s", name, e)
                codes = []
            if codes:
                out[name] = codes
    log.info("eastmoney industry map: %d industries, %d stocks",
             len(out), sum(len(v) for v in out.values()))
    return out


# ---------------------------------------------------------------- 全市场快照(新浪被限流时的备用行情源)
# fs 覆盖: 深主板/创业板/沪主板/科创板/北交所
_QS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_FIELDS = "f12,f13,f14,f2,f3,f15,f16,f17,f18,f5,f6,f8,f20,f21"


def _num(v, nd=2):
    try:
        if v in ("-", None, ""):
            return None
        return round(float(v), nd)
    except Exception:
        return None


def _symbol(code, mkt):
    if str(code).startswith(("4", "8", "92")):
        return "bj" + str(code)
    return ("sh" if str(mkt) == "1" else "sz") + str(code)


def _fetch_page(pn, pz=200):
    rows = _diff(_get({"pn": pn, "pz": pz, "po": 0, "np": 1, "fltt": 2, "invt": 2,
                       "fid": "f12", "fs": _QS, "fields": _FIELDS}))
    return rows


def fetch_all_quotes(max_pages=40, threads=8):
    """东财全市场A股快照 → [{code,name,symbol,price,pct,open,high,low,pre_close,
    volume,amount,turnover,nmc,mktcap}]; 失败返回 []"""
    try:
        first = _fetch_page(1)
    except Exception as e:  # noqa
        log.warning("eastmoney all quotes p1: %s", e)
        return []
    if not first:
        return []
    rows = list(first)
    total = 0
    try:
        d = (_get({"pn": 1, "pz": 1, "po": 0, "np": 1, "fltt": 2, "invt": 2,
                   "fid": "f12", "fs": _QS, "fields": "f12"}) or {}).get("data") or {}
        total = int(d.get("total") or 0)
    except Exception:
        pass
    pages = min(max_pages, max(1, (total + 199) // 200)) if total else max_pages
    if pages > 1:
        with ThreadPoolExecutor(max(2, min(threads, 10))) as ex:
            futs = [ex.submit(_fetch_page, pn) for pn in range(2, pages + 1)]
            for fu in as_completed(futs):
                try:
                    rows += fu.result() or []
                except Exception:
                    pass
    out = []
    seen = set()
    for r in rows:
        code = str(r.get("f12") or "").zfill(6)
        if len(code) != 6 or not code.isdigit() or code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code, "name": r.get("f14") or code,
            "symbol": _symbol(code, r.get("f13")),
            "price": _num(r.get("f2")), "pct": _num(r.get("f3")),
            "high": _num(r.get("f15")), "low": _num(r.get("f16")),
            "open": _num(r.get("f17")), "pre_close": _num(r.get("f18")),
            "volume": r.get("f5"), "amount": r.get("f6"),
            "turnover": _num(r.get("f8")), "mktcap": r.get("f20"), "nmc": r.get("f21"),
        })
    log.info("eastmoney all quotes: %d stocks (total=%s)", len(out), total)
    return out
