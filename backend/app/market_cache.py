"""分析结果缓存：行情不变则复用最近一次全量分析，避免重复计算。
- get_view: 缓存新鲜直接返回; 过期时“立刻返回最近一次视图 + 后台线程重建(单飞)”,
  不再让网页请求同步等待 analyze(可>60s, 曾造成 nginx 504);
- wait=True(盘后复盘/手动flush/冷启动内部)才同步等待重建完成;
- 同一时刻只允许一次重建, 避免多请求并发重算风暴。
"""
import threading
import time

_cache = {}
_lock = threading.Lock()
_build_lock = threading.Lock()
_bg_lock = threading.Lock()
_bg_running = False
_bg_last = 0.0
_BG_MIN_GAP = 5.0


def invalidate():
    with _lock:
        _cache.clear()


def _build_view():
    """执行一次全量分析(real/mock), 返回新视图或 None"""
    from .config import DATA_SOURCE
    if DATA_SOURCE == "real":
        from .real import analyze_real
        return analyze_real.analyze_real()
    from . import analyze as _a
    hist = _a.fetch_hist()
    return _a.analyze(hist)


def _kick_bg():
    """后台重建(单飞 + 最短间隔), 不阻塞调用方"""
    global _bg_running, _bg_last
    with _bg_lock:
        now = time.time()
        if _bg_running or now - _bg_last < _BG_MIN_GAP:
            return
        _bg_running = True
    t = threading.Thread(target=_bg_worker, daemon=True, name="view-bg-build")
    t.start()


def _bg_worker():
    global _bg_running, _bg_last
    try:
        with _build_lock:
            view = _build_view()
            if view is not None:
                with _lock:
                    _cache["ts"] = time.time()
                    _cache["view"] = view
    except Exception as e:  # noqa
        import logging
        logging.getLogger("kb.cache").warning("background view build: %s", e)
    finally:
        with _bg_lock:
            _bg_running = False
            _bg_last = time.time()


def get_view(max_age=20.0, wait=False):
    """返回分析视图。
    - 新鲜(距上次构建<max_age) → 立即返回;
    - 已过期:
        wait=False(网页默认) → 立即返回最近一次视图(可能稍旧)并在后台重建;
        wait=True          → 同步等待重建完成(阻塞, 仅显式调用方使用);
    - 从未构建过(冷启动)且 wait=False → 触发后台构建并返回 None(调用方按503/加载中处理)。"""
    now = time.time()
    with _lock:
        if _cache and now - _cache.get("ts", 0) < max_age:
            return _cache["view"]
    with _build_lock:
        with _lock:
            if _cache and now - _cache.get("ts", 0) < max_age:
                return _cache["view"]
        cached = _cache.get("view")
        if cached is not None and not wait:
            # 过期但有过期缓存 → 后台重建, 立即返回旧视图(不阻塞网页)
            _kick_bg()
            return cached
        if cached is None and not wait:
            # 冷启动 → 后台构建, 快速返回 None(网页显示“数据初始化中”, 不卡请求)
            _kick_bg()
            return None
        # wait=True 或冷启动同步首次构建(单飞)
        view = _build_view()
        if view is None:
            return _cache.get("view")
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
    """清缓存并同步重建(盘后/启动等需要立即新鲜结果的场景; 阻塞)"""
    with _lock:
        _cache.clear()
    return get_view(max_age=0, wait=True)
