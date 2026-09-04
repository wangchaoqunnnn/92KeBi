"""中国时区(A股)时钟工具 — 云服务器无论设什么时区, 交易窗口/日期判断都按北京时间。
优先用 zoneinfo(Asia/Shanghai); 容器无 tzdata 时回退为固定 UTC+8, 保证结果一致。
所有返回均为“北京时间”的 naive datetime / 字符串, 与系统本地时区无关。
"""
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa
    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # 无 zoneinfo/tzdata 或加载失败 → 固定 +8
    _CN_TZ = None

_UTC8 = timedelta(hours=8)


def now() -> datetime:
    """当前北京时间(naive, 去掉了时区后缀, 直接可比较/格式化)"""
    if _CN_TZ is not None:
        return datetime.now(_CN_TZ).replace(tzinfo=None)
    return (datetime.utcnow() + _UTC8).replace(tzinfo=None)


def utcnow_ts() -> float:
    """当前 UTC 时间戳(epoch seconds, 与时区无关, 供日志/缓存使用)"""
    import time
    return time.time()


def init_process_timezone():
    """把进程默认时区钉死为 Asia/Shanghai(Unix: TZ + tzset)。
    这样 datetime.now()/date.today()/time.strftime() 等全局调用也都走北京时间,
    与本模块的 now()/today() 结果一致。Windows 无 tzset, 自动跳过。"""
    import os
    os.environ.setdefault("TZ", "Asia/Shanghai")
    try:
        import time
        time.tzset()
    except Exception:  # Windows / 无 tzdata
        pass


def today() -> date:
    """今天(北京时间)的 date 对象"""
    return now().date()


def today_str(fmt: str = "%Y-%m-%d") -> str:
    return now().strftime(fmt)


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now().strftime(fmt)


def is_weekday() -> bool:
    """是否周一~周五(北京时间; 节假日未剔除, 与交易日历保持一致需另行判断)"""
    return now().weekday() < 5


def hm() -> int:
    """当前 HHMM (北京时间), 便于窗口比较, 如 0931"""
    n = now()
    return n.hour * 100 + n.minute


def in_range(h1: int, h2: int) -> bool:
    """当前是否处于北京时间 h1 <= HHMM <= h2 (h1/h2 形如 925/1459)"""
    return h1 <= hm() <= h2
