"""公开访问地址推导（用于微信推送里的“点击直达”链接）。
优先级:
1) 环境变量 WECHAT_PAGE_URL(若显式设置) ;
2) 默认打板操作台地址 https://wangchaoqun.top/92kebi/#/ops (config 内默认值);
3) 仅当以上为空时才按最近一次访问来源动态推导(scheme://host + X-Forwarded-Prefix + #/ops)。
"""
import threading

from .config import WECHAT_PAGE_URL

_lock = threading.Lock()
_origin = ""   # 例: https://wangchaoqun.top
_prefix = ""   # 例: /92kebi/  (nginx 反代子路径时通过 X-Forwarded-Prefix 传入)
_ts = 0.0


def _is_loopback(host):
    h = (host or "").split(":")[0].strip().lower()
    return h in ("127.0.0.1", "localhost", "::1", "0.0.0.0") or h.startswith("127.")


def capture(scheme, host, fwd_proto=None, fwd_prefix=None):
    """由中间件在每个 HTTP 请求上调用, 记住用户实际访问的 协议/主机/子路径。
    本机回环来源(健康检查/本地 curl)不覆盖已记录的公网来源。"""
    global _origin, _prefix, _ts
    host = (host or "").strip()
    if not host:
        return
    if _is_loopback(host):
        with _lock:
            cur_host = (_origin or "").split("://", 1)[-1].split(":")[0]
        if _origin and not _is_loopback(cur_host):
            return  # 已有公网来源, 忽略本机探测
    try:
        proto = (fwd_proto or scheme or "http").split(",")[0].strip() or "http"
        origin = f"{proto}://{host}"
        prefix = (fwd_prefix or "").strip()
        if prefix and not prefix.startswith("/"):
            prefix = "/" + prefix
        if prefix and not prefix.endswith("/"):
            prefix += "/"
    except Exception:
        return
    import time
    with _lock:
        _origin = origin
        _prefix = prefix
        _ts = time.time()


def current():
    with _lock:
        return {"origin": _origin, "prefix": _prefix}


def ops_page_url():
    """打板操作台可点击地址: 环境变量 > 动态推导; 都无 → ''(此时推送不带链接, 退化为纯文本)"""
    if WECHAT_PAGE_URL:
        return WECHAT_PAGE_URL
    with _lock:
        origin, prefix = _origin, _prefix
    if not origin:
        return ""
    return f"{origin}{prefix}#/ops"
