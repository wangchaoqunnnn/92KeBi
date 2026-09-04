"""打板操作台 REST(ops)。"""
from fastapi import APIRouter, HTTPException, Query

from .. import ops

router = APIRouter(prefix="/api/ops")


@router.get("/overview")
def ops_overview():
    return ops.overview()


@router.post("/flush")
def ops_flush():
    """立即执行一次自动盯盘扫描(买点/卖点/观察池)"""
    from .. import market_cache
    view = market_cache.get_view(max_age=0)
    ctx = market_cache.get_ctx()
    res = ops.sweep(view=view, ctx=ctx)
    return {"ok": True, **res}


@router.post("/ignore")
def ops_ignore(pool: str = Query(..., pattern="^(buy|watch)$"), code: str = Query(...)):
    return ops.ignore_item(pool, code)


@router.post("/manual-sell")
def ops_manual_sell(code: str = Query(...)):
    return ops.manual_sell(code)


@router.post("/manual-watch")
def ops_manual_watch(code: str = Query(...)):
    return ops.manual_watch(code)


@router.post("/delete")
def ops_delete(item_id: int = Query(...)):
    """管理删除(仅卖出池记录)"""
    return ops.delete_sell(item_id)


@router.post("/push-test")
def ops_push_test():
    """微信推送连通性测试(需配置 WECHAT_WEBHOOK 环境变量)"""
    return {"ok": True, **ops.wechat_push_test()}
