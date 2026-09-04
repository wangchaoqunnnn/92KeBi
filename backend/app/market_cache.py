"""分析结果缓存：行情不变则复用最近一次全量分析，避免重复计算；tick 更新时刷新。"""
import threading
import time

_cache = {}
_lock = threading.Lock()


def invalidate():
    with _lock:
        _cache.clear()


def get_view(max_age=30.0):
    """返回(缓存的)最新分析视图；超过 max_age 秒则重建。
    mock 模式来自内置模拟市场；real 模式来自新浪实时实盘(样本池+全市场)。"""
    from .config import DATA_SOURCE
    now = time.time()
    with _lock:
        if _cache and now - _cache.get("ts", 0) < max_age:
            return _cache["view"]
    if DATA_SOURCE == "real":
        from .real import analyze_real
        view = analyze_real.analyze_real()
        if view is None:
            # 样本池尚未就绪：等待/返回基础结构由调用方兜底
            return _cache.get("view")
    else:
        from . import analyze as _a
        hist = _a.fetch_hist()
        view = _a.analyze(hist)
    with _lock:
        _cache["ts"] = time.time()
        _cache["view"] = view
    return view


def remember(ctx):
    """analyze() 内部调用：保留轻量上下文供个股详情复用"""
    with _lock:
        _cache["ctx"] = ctx


def get_ctx():
    with _lock:
        return _cache.get("ctx")


def force_refresh():
    with _lock:
        _cache.clear()
    return get_view(max_age=0)
