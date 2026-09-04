"""定时任务（需求 T-04）：
- mock 模式: 模拟盘中 tick(MOCK_TICK_SECONDS) + 分析缓存刷新 + 每日盘后复盘
- real 模式: 全市场行情快照轮询(REAL_POLL_SECONDS, 线程池执行防阻塞) + 视图刷新
             + 收盘后(工作日 15:05+)增量回填样本日K + 行业映射缓存维护
用 asyncio 后台任务实现；部署到云服务器可替换为 APScheduler/Cron。
"""
import asyncio
import logging
import time
from datetime import datetime

from .. import market_cache
from ..config import DATA_SOURCE, MOCK_TICK_SECONDS, REAL_POLL_SECONDS

log = logging.getLogger("kb.scheduler")

_state = {"started_at": None, "ticks": 0, "last_tick": None, "last_analysis": None,
          "last_review": None, "tasks": {}, "running": True, "mode": DATA_SOURCE,
          "poll_count": 0, "last_poll": None, "poll_error": None}


def status():
    return dict(_state)


class Scheduler:
    def __init__(self):
        self._tasks = []
        self._polling = False
        self._crawled_today = None

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
                _state["last_tick"] = time.strftime("%H:%M:%S")
            except Exception as e:  # noqa
                log.warning("tick error: %s", e)
            await asyncio.sleep(MOCK_TICK_SECONDS)

    async def _real_poll_loop(self):
        from ..real import market as real_mkt
        while _state["running"]:
            if not self._polling:
                # 交易时段(工作日 9:00-16:30)高频轮询; 其余时段低频(仅同步日期切换/收盘数据)
                now = datetime.now()
                in_session = now.weekday() < 5 and 9 <= now.hour <= 16
                interval = REAL_POLL_SECONDS if in_session else 600
                self._polling = True
                try:
                    await asyncio.to_thread(real_mkt.refresh_quotes)
                    _state["poll_count"] += 1
                    _state["last_poll"] = time.strftime("%H:%M:%S")
                    _state["poll_error"] = None
                    # 不强制失效缓存：分析视图由 _analysis_loop 按周期重建(带单飞锁),
                    # 避免每个浏览器轮询都触发重算风暴
                except Exception as e:  # noqa
                    log.warning("real poll error: %s", e)
                    _state["poll_error"] = str(e)
                finally:
                    self._polling = False
                await asyncio.sleep(interval)
            else:
                await asyncio.sleep(2)

    async def _analysis_loop(self):
        """按周期重建分析视图(带单飞锁)，并驱动 ops 打板台账扫描。均在 worker 线程执行不阻塞事件循环。"""
        cadence = 6 if DATA_SOURCE == "mock" else 10
        _ops_running = False
        while _state["running"]:
            try:
                await asyncio.to_thread(market_cache.get_view, 8.0)
                _state["last_analysis"] = time.strftime("%H:%M:%S")
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

    async def _daily_loop(self):
        """盘后任务/每日复盘。real: 收盘后回填当日真实日K与存档。"""
        last_day = None
        while _state["running"]:
            today = datetime.now().strftime("%Y-%m-%d")
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
                        "time": time.strftime("%H:%M:%S"),
                    }
                    log.info("复盘 %s", _state["last_review"])
                except Exception as e:  # noqa
                    log.warning("daily review error: %s", e)
            await asyncio.sleep(1800)

    async def _real_daily(self):
        from ..real import sample as real_sample
        from ..real import market as real_mkt
        now = datetime.now()
        if now.weekday() >= 5 or now.hour < 15 or self._crawled_today == now.date().isoformat():
            return
        self._crawled_today = now.date().isoformat()
        log.info("盘后: 重选样本并回填真实日K")
        await asyncio.to_thread(real_mkt.refresh_quotes)
        await asyncio.to_thread(real_sample.sync_sample_stocks)
        await asyncio.to_thread(real_sample.crawl_sample)
        # 样本K线就绪后重跑一轮板块历史补齐(样本外的板块成员此时才可判断覆盖)
        real_mkt._ENRICH_DAY = ""
        real_mkt._maybe_enrich_async()

    async def start(self):
        _state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._tasks = [asyncio.create_task(self._tick_loop()),
                       asyncio.create_task(self._analysis_loop()),
                       asyncio.create_task(self._daily_loop())]
        _state["tasks"] = {str(id(t)): t.get_name() for t in self._tasks}
        log.info("scheduler started (data_source=%s)", DATA_SOURCE)

    async def stop(self):
        _state["running"] = False
        for t in self._tasks:
            t.cancel()
