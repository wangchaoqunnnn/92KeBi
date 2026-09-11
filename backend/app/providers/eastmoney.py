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
