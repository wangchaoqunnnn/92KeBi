"""92K 情绪周期决策台 — FastAPI 入口。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cn_time import init_process_timezone

init_process_timezone()  # 无论以何种方式启动(uvicorn/python -m/run.py)都按北京时间运行

from . import db  # noqa: E402
from .config import DATA_SOURCE  # noqa: E402
from .seed.run_seed import seed_market  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("kb.main")

STATIC_DIR = __import__("os").path.join(__import__("os").path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) 数据层初始化
    db.init_db()
    mode = DATA_SOURCE
    if mode == "real":
        await _init_real()
    else:
        from .seed.run_seed import seed_market
        res = seed_market()
        log.info("seed(mock): %s", res)
    # 2) 首次分析 + (mock)模拟实时初始化
    from . import analyze
    from .providers import mock_live
    view = None
    if mode != "real":
        try:
            view = analyze.analyze_today()
        except Exception as e:
            log.error("first view failed: %s", e)
        if view:
            log.info("首日分析: date=%s phase=%s", view["date"], view["phase"]["phase_cn"])
            hist = analyze.fetch_hist()
            if hist:
                mock_live.init([{"code": b["code"], "name": b["name"], "close": b["close"],
                                 "pre_close": b["pre_close"], "high": b["high"], "low": b["low"],
                                 "amount": b.get("amount"), "pct": b["pct"]}
                                for b in hist["day_bars"][-1]])
    # 3) 调度任务
    from .tasks.scheduler import Scheduler
    sched = Scheduler()
    await sched.start()
    app.state.scheduler = sched
    yield
    await sched.stop()
    db.close_all()


async def _init_real():
    """实盘模式初始化: 快照→行业→样本→日K回填(首次较慢, 后续秒级跳过)"""
    import time as _t
    t0 = _t.time()
    from .core import phase
    from .config import REAL_RULE
    phase.set_rule(REAL_RULE)          # 引擎阈值切到全市场口径
    from .real import market as real_mkt
    from .real import sample as real_sample
    ok = False
    for attempt in range(3):
        ok = real_mkt.refresh_quotes()
        if ok:
            break
        log.warning("real snapshot attempt %d failed", attempt + 1)
    if not ok:
        log.error("实时行情源不可达(新浪接口)。请检查网络/外网权限后重启；系统将处于待命状态。")
        return
    real_mkt.get_industry_cache()
    n, msg = real_sample.sync_sample_stocks()
    log.info("real sample synced: n=%s %s (%.1fs)", n, msg, _t.time() - t0)
    if not real_sample.is_up_to_date():
        log.info("开始回填真实日K(样本池 %s 只)…", real_sample.progress().get("total"))
        res = real_sample.crawl_sample()
        log.info("日K回填完成: %s", res)
    else:
        log.info("样本历史已最新, 跳过回填")
    st = real_mkt.snapshot()
    mkt = st.get("mkt_stats") or {}
    log.info("实盘就绪: %s 全市场 涨停%d 跌停%d 涨%d 跌%d", st.get("quote_date"),
             mkt.get("zt"), mkt.get("dt"), mkt.get("up"), mkt.get("down"))


app = FastAPI(title="92K 情绪周期决策台", version="0.1.0", lifespan=lifespan,
              description="龙头/补涨/切换量化决策辅助(模拟数据演示)", docs_url="/docs")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

from .api.routes import router as r1   # noqa: E402
from .api.extra import router as r2    # noqa: E402
from .api.ops_api import router as r3  # noqa: E402
app.include_router(r1)
app.include_router(r2)
app.include_router(r3)


@app.get("/api/health")
def health():
    """云部署健康检查: 进程/时区/调度循环/行情/交易窗口/微信 一键自检"""
    import os
    from . import cn_time
    from .tasks.scheduler import status as sstatus
    st = sstatus()
    d = {"ok": True, "data_source": DATA_SOURCE,
         "cn_time": cn_time.now_str(),
         "tz_env": os.environ.get("TZ", "") or "(system)",
         "scheduler": {"running": st.get("running"),
                       "tasks": st.get("tasks"),
                       "poll_count": st.get("poll_count"),
                       "last_poll": st.get("last_poll"),
                       "last_analysis": st.get("last_analysis"),
                       "respawns": st.get("respawns")}}
    if DATA_SOURCE == "real":
        try:
            from . import ops
            from .real import market as real_mkt
            snap = real_mkt.snapshot()
            mkt = snap.get("mkt_stats") or {}
            d["market"] = {"quote_date": snap.get("quote_date"), "state": snap.get("state"),
                           "zt": mkt.get("zt"), "universe": mkt.get("universe"),
                           "amount_yi": mkt.get("amount_yi"), "src": snap.get("src")}
            d["ops_window"] = ops.window_info()
            d["wechat"] = ops._wechat_status()
        except Exception as e:  # noqa
            d["market_error"] = str(e)[:150]
    return d


# ---------------- 前端静态资源(构建产物) ----------------
if __import__("os").path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=__import__("os").path.join(STATIC_DIR, "assets")),
              name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        p = __import__("os").path.normpath(
            __import__("os").path.join(STATIC_DIR, full_path))
        if full_path and p.startswith(STATIC_DIR) and __import__("os").path.isfile(p):
            return FileResponse(p)
        return FileResponse(__import__("os").path.join(STATIC_DIR, "index.html"))
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {"msg": "92K API running; 前端未构建，请运行前端 npm run build"}
