"""定时任务（需求 T-04）：
- mock 模式: 模拟盘中 tick(MOCK_TICK_SECONDS) + 分析缓存刷新 + 每日盘后复盘
- real 模式: 全市场行情快照轮询(REAL_POLL_SECONDS, 线程池执行防阻塞) + 视图刷新
             + 收盘后(工作日 15:05+, 北京时间)增量回填样本日K + 行业映射缓存维护
用 asyncio 后台任务实现；部署到云服务器由 systemd 守护, 进程内另有看门狗:
任一循环意外退出(未捕获异常/被取消)都会在 30 秒内自动重建, 保证盘中不中断。
所有交易窗口/日期判断使用北京时间(cn_time), 与服务器系统时区无关。
"""
import asyncio
import logging

from .. import cn_time, market_cache
from ..config import DATA_SOURCE, MOCK_TICK_SECONDS, REAL_POLL_SECONDS

log = logging.getLogger("kb.scheduler")

_state = {"started_at": None, "ticks": 0, "last_tick": None, "last_analysis": None,
          "last_review": None, "tasks": {}, "running": True, "mode": DATA_SOURCE,
          "poll_count": 0, "last_poll": None, "poll_error": None,
          "respawns": {}}

_LOOPS = ("poll", "analysis", "daily")   # 需要看门狗守护的循环


def status():
    return dict(_state)


class Scheduler:
    def __init__(self):
        self._tasks = {}
        self._polling = False
        self._crawled_today = None

    # ------------------------------------------------------------ 任务构造/看门狗
    def _spawn(self, name):
        builder = {"poll": self._tick_loop, "analysis": self._analysis_loop,
                   "daily": self._daily_loop, "watchdog": self._watchdog_loop}[name]
        t = asyncio.create_task(builder(), name=f"kb-{name}")
        self._tasks[name] = t
        _state["tasks"] = {n: (t.get_name() if not t.done() else f"{t.get_name()}(dead)")
                           for n, t in self._tasks.items()}
        return t

    async def _watchdog_loop(self):
        """监控三主循环: 任一异常退出 → 记日志并在30秒内重建"""
        while _state["running"]:
            await asyncio.sleep(30)
            for name in _LOOPS:
                t = self._tasks.get(name)
                if t is None or t.done():
                    exc = None
                    if t is not None:
                        try:
                            exc = t.exception()
                        except Exception:  # noqa
                            exc = "cancelled/unretrievable"
                    log.error("scheduler loop '%s' died(%s) → auto-respawn", name, exc)
                    _state["respawns"][name] = _state["respawns"].get(name, 0) + 1
                    self._spawn(name)

    # ------------------------------------------------------------ mock 模式
    async def _tick_loop(self):
        """mock: 模拟实时游走; real: 新浪全市场快照轮询"""
        from ..providers import mock_live
        if DATA_SOURCE == "real":
            await self._real_poll_loop()
            return
        while _state["running"]:
            try:
                mock_live.tick()
                _state["ticks"] += 1
                _state["last_tick"] = cn_time.now_str("%H:%M:%S")
            except Exception as e:  # noqa
                log.warning("tick error: %s", e)
            await asyncio.sleep(MOCK_TICK_SECONDS)

    # ------------------------------------------------------------ real 轮询
    async def _real_poll_loop(self):
        from ..real import market as real_mkt
        while _state["running"]:
            if not self._polling:
                # 交易时段(北京时间 工作日 9:00-16:30)高频轮询; 其余时段低频
                now = cn_time.now()
                in_session = now.weekday() < 5 and 9 <= now.hour <= 16
                interval = REAL_POLL_SECONDS if in_session else 600
                self._polling = True
                try:
                    await asyncio.to_thread(real_mkt.refresh_quotes)
                    _state["poll_count"] += 1
                    _state["last_poll"] = cn_time.now_str("%H:%M:%S")
                    _state["poll_error"] = None
                    # 不强制失效缓存：分析视图由 _analysis_loop 按周期重建(带单飞锁)
                except Exception as e:  # noqa
                    log.warning("real poll error: %s", e)
                    _state["poll_error"] = str(e)
                finally:
                    self._polling = False
                await asyncio.sleep(interval)
            else:
                await asyncio.sleep(2)

    # ------------------------------------------------------------ 分析 + 打板扫描
    async def _analysis_loop(self):
        """按周期重建分析视图(带单飞锁)，并驱动 ops 打板台账扫描(买卖入池+微信推送)。
        与浏览器无关：无论是否有页面访问, 只要进程存活就持续运行。"""
        cadence = 6 if DATA_SOURCE == "mock" else 10
        _ops_running = False
        while _state["running"]:
            try:
                await asyncio.to_thread(market_cache.get_view, 8.0)
                _state["last_analysis"] = cn_time.now_str("%H:%M:%S")
                # 打板台账: 买点→买入池, 卖点→卖出池, 候选→观察池
                if not _ops_running:
                    _ops_running = True
                    try:
                        from .. import ops
                        view = market_cache._cache.get("view")
                        ctx = market_cache.get_ctx()
                        if view and ctx:
                            await asyncio.to_thread(ops.sweep, view, ctx)
                    except Exception as e:  # noqa
                        log.warning("ops sweep error: %s", e)
                    finally:
                        _ops_running = False
            except Exception as e:  # noqa
                log.warning("analysis error: %s", e)
            await asyncio.sleep(cadence)

    # ------------------------------------------------------------ 每日/盘后
    async def _daily_loop(self):
        """按北京时间切换日期: 每日一次盘后复盘 + real 模式收盘回填样本日K。"""
        last_day = None
        while _state["running"]:
            today = cn_time.today_str()
            if today != last_day:
                last_day = today
                try:
                    if DATA_SOURCE == "real":
                        await self._real_daily()
                    view = market_cache.force_refresh()
                    st = view["stats"] if view else {}
                    _state["last_review"] = {
                        "date": view["date"] if view else today,
                        "phase": view["phase"]["phase_cn"] if view else None,
                        "zt": st.get("zt_count"), "dt": st.get("dt_count"),
                        "dragon": (view["leaders"]["dragon"]["name"]
                                   if view and view["leaders"]["dragon"] else None),
                        "time": cn_time.now_str("%H:%M:%S"),
                    }
                    log.info("复盘 %s", _state["last_review"])
                except Exception as e:  # noqa
                    log.warning("daily review error: %s", e)
            await asyncio.sleep(1800)

    async def _real_daily(self):
        """收盘(北京时间 15:00+)回填当日日K; 周末跳过。"""
        from ..real import sample as real_sample
        from ..real import market as real_mkt
        now = cn_time.now()
        if now.weekday() >= 5 or now.hour < 15 \
                or self._crawled_today == now.date().isoformat():
            return
        self._crawled_today = now.date().isoformat()
        log.info("盘后: 重选样本并回填真实日K")
        await asyncio.to_thread(real_mkt.refresh_quotes)
        await asyncio.to_thread(real_sample.sync_sample_stocks)
        await asyncio.to_thread(real_sample.crawl_sample)
        # 样本K线就绪后重跑一轮板块历史补齐(样本外的板块成员此时才可判断覆盖)
        real_mkt._ENRICH_DAY = ""
        real_mkt._maybe_enrich_async()

    # ------------------------------------------------------------ 启停
    async def start(self):
        _state["running"] = True
        _state["started_at"] = cn_time.now_str()
        self._tasks = {}
        for name in (*_LOOPS, "watchdog"):
            self._spawn(name)
        log.info("scheduler started (data_source=%s, tasks=%s)", DATA_SOURCE,
                 {n: t.get_name() for n, t in self._tasks.items()})

    async def stop(self):
        _state["running"] = False
        for t in list(self._tasks.values()):
            t.cancel()
        try:
            await asyncio.wait(list(self._tasks.values()), timeout=3)
        except Exception:  # noqa
            pass
