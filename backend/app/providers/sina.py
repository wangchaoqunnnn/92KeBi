"""新浪财经公开接口数据源（真实实盘模式）。
仅依赖新浪系公开 HTTP 接口，无需 token：
- 全市场A股实时行情/成分:  Market_Center.getHQNodeData (hs_a 等节点)
- 行业板块树与成分:        Market_Center.getHQNodes / getHQNodeData(node=new_*)
- 批量实时快照(小批量):    hq.sinajs.cn
- 个股/指数日K(前复权档):  quotes.sina.cn CN_MarketDataService.getKLineData
说明：东财 push2 系列接口在本环境被拒连，因此不依赖东财；涨停/跌停/连板均按
行情+涨跌幅限制规则自行判定（主板10%、创业/科创20%、ST 5%，上市首日/北交所另处理）。
"""
import json
import logging
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("kb.sina")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
MARKET_CENTER = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"


def _http(url, timeout=12, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa
            last = e
            time.sleep(0.4 * (i + 1))
    raise last or ConnectionError(url)


def _json(url, timeout=12, tries=3):
    raw = _http(url, timeout, tries)
    return json.loads(raw.decode("utf-8", "ignore"))


# ---------------------------------------------------------------- 行情列表
def fetch_node_page(node, page=1, num=100, sort="symbol", asc=1):
    q = urllib.parse.urlencode({"page": page, "num": num, "sort": sort,
                                "asc": asc, "node": node, "symbol": ""})
    return _json(MARKET_CENTER + "Market_Center.getHQNodeData?" + q)


def _norm_row(r):
    """把节点行规整成 dict(数值已转 float/None 安全)"""
    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    out = {}
    for k in ("code", "symbol", "name", "ticktime"):
        out[k] = str(r.get(k) or "").strip()
    for k in ("trade", "settlement", "open", "high", "low", "changepercent",
              "pricechange", "turnoverratio", "volume", "amount", "mktcap",
              "nmc", "per", "pb"):
        out[k] = f(r.get(k))
    return out


def fetch_members(node="hs_a", threads=8):
    """抓取某节点全部成分(带行情字段)。返回 list[dict]"""
    rows = []
    page = 1
    while True:
        try:
            batch = fetch_node_page(node, page=page, num=100)
        except Exception as e:
            log.warning("member page fail node=%s page=%s: %s", node, page, e)
            break
        if not batch:
            break
        rows.extend(_norm_row(x) for x in batch)
        if len(batch) < 100:
            break
        page += 1
    return rows


def fetch_members_fast(node="hs_a", threads=8, pages=58):
    """多线程抓取成分(固定页数并发, 每页100)。A股沪深京合计<5800，58页留足余量。"""
    out = [None] * pages
    with ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(fetch_node_page, node, p, 100): p for p in range(1, pages + 1)}
        for fu in as_completed(futs):
            p = futs[fu]
            try:
                out[p - 1] = [_norm_row(x) for x in fu.result()]
            except Exception as e:
                log.warning("member page fail %s: %s", p, e)
                out[p - 1] = []
    rows = [r for page_rows in out for r in page_rows if r.get("code")]
    log.info("fetch_members_fast %s total=%d", node, len(rows))
    return rows


# ---------------------------------------------------------------- 板块树
def fetch_node_tree():
    """返回叶子节点 (id, name)。行业=new_*, 概念=gn_*"""
    tree = _json(MARKET_CENTER + "Market_Center.getHQNodes", timeout=20)
    leaves = []

    def walk(n, path):
        if not isinstance(n, list):
            return
        if len(n) >= 3 and isinstance(n[0], str):
            name, child, nid = n[0], n[1], n[2]
            if nid:
                leaves.append({"id": nid, "name": name, "path": path})
            walk(child, path + [name] if nid == "" else path)
        else:
            for x in n:
                walk(x, path)

    walk(tree, [])
    inds = [x for x in leaves if str(x["id"]).startswith("new_")]
    cons = [x for x in leaves if str(x["id"]).startswith("gn_")]
    return {"industries": inds, "concepts": cons}


def fetch_node_members(node_id, threads=6):
    """抓某板块(行业/概念)全部成分代码：以100/页分页"""
    codes = []
    page = 1
    while True:
        try:
            batch = fetch_node_page(node_id, page=page, num=100, sort="symbol", asc=1)
        except Exception as e:
            log.warning("industry page fail %s p%s: %s", node_id, page, e)
            break
        if not batch:
            break
        codes.extend(str(x.get("code", "")).zfill(6) for x in batch)
        if len(batch) < 100:
            break
        page += 1
        if page > 30:
            break
    return list(dict.fromkeys(codes))


def fetch_industry_map(industries, threads=8):
    """返回 {名称: [codes]}（新浪行业，49个）"""
    out = {}
    with ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(fetch_node_members, x["id"]): x for x in industries}
        for fu in as_completed(futs):
            meta = futs[fu]
            try:
                codes = fu.result()
            except Exception as e:
                log.warning("industry %s fail: %s", meta, e)
                codes = []
            out[meta["name"]] = codes
    return out


# ---------------------------------------------------------------- 批量实时行情(hq)
def fetch_hq_quotes(symbols, timeout=10, tries=2):
    """新浪 hq 小批量实时行情。symbols: ['sh600519', ...] 或含 sz/bj 前缀。
    返回 dict code->dict(name,open,pre_close,price,high,low,volume,amount,date,time)"""
    if not symbols:
        return {}
    out = {}
    # 分块 350/请求
    for i in range(0, len(symbols), 350):
        chunk = symbols[i:i + 350]
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        raw = _http(url, timeout, tries)
        txt = raw.decode("gbk", "ignore")
        for line in txt.splitlines():
            line = line.strip()
            if not line.startswith("var hq_str_"):
                continue
            head, _, body = line.partition("=")
            sym = head.replace("var hq_str_", "").strip('" ')
            code = sym[2:]
            f = body.strip().strip('"').split(",")
            if len(f) < 32 or not f[0]:
                continue

            def fl(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None
            out[code] = {
                "code": code, "symbol": sym, "name": f[0],
                "open": fl(f[1]), "pre_close": fl(f[2]), "price": fl(f[3]),
                "high": fl(f[4]), "low": fl(f[5]),
                "volume": fl(f[8]), "amount": fl(f[9]),
                "date": f[30] if len(f) > 30 else "", "time": f[31] if len(f) > 31 else "",
            }
    return out


def to_symbol(code):
    """6位代码 -> sina symbol 前缀 (sh/sz/bj)"""
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "9")):
        return "sh" + c
    if c.startswith(("4", "8", "92")):
        return "bj" + c
    return "sz" + c


# ---------------------------------------------------------------- 日K
def fetch_kline(code, n=520, symbol=None):
    """日K(前复权 scale=240)。返回升序 list[{day,open,high,low,close,volume}]
    注意：该接口在盘中不返回未收盘的当日K。"""
    sym = symbol or to_symbol(code)
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?" + \
          urllib.parse.urlencode({"symbol": sym, "scale": 240, "ma": "no", "datalen": n})
    rows = _json(url, timeout=15)
    out = []
    for r in rows or []:
        try:
            out.append({"day": str(r["day"]),
                        "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "volume": float(r.get("volume") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_index_kline(symbol="sh000001", n=520):
    return fetch_kline(symbol, n, symbol=symbol)


def today_str():
    return time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 涨跌停判定
def limit_rate(code, name):
    """涨跌幅限制: 主板10% / 创业(300,301)科创(688,689)20% / ST 5%(主板口径)
    北交所/新上市(前缀 N/C) 单独处理(不参与涨停判定)"""
    c = str(code).zfill(6)
    n = (name or "").upper()
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    if "ST" in n and c.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        return 0.05
    return 0.10


def is_new_listing(code, name):
    n = (name or "").strip().upper()
    return n.startswith(("N", "C", "退")) or str(code).startswith(("4", "8", "92"))


def is_limit_up(price, pre_close, rate):
    if not price or not pre_close or pre_close <= 0 or price <= 0:
        return False
    lim = round(pre_close * (1 + rate), 2)
    return price + 1e-6 >= lim


def is_limit_down(price, pre_close, rate):
    if not price or not pre_close or pre_close <= 0 or price <= 0:
        return False
    lim = round(pre_close * (1 - rate), 2)
    return price - 1e-6 <= lim
