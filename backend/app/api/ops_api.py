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
def ops_manual_watch(q: str = Query("", max_length=30)):
    """手动加入观察池: q 支持 6 位代码或股票名称(由后端解析+算法评分)"""
    return ops.manual_watch(q)


@router.post("/delete")
def ops_delete(item_id: int = Query(...)):
    """管理删除(仅卖出池记录)"""
    return ops.delete_sell(item_id)


@router.post("/remove-buy")
def ops_remove_buy(item_id: int = Query(...)):
    """移除买入池持仓(直接删除该数据行)"""
    return ops.remove_buy(item_id)


@router.post("/push-test")
def ops_push_test():
    """微信推送连通性测试(需配置 WECHAT_WEBHOOK 环境变量)"""
    return {"ok": True, **ops.wechat_push_test()}


@router.post("/demo-buy")
def ops_demo_buy():
    """新增一条模拟持仓(买入池)用于微信推送联调(测完可移除)"""
    return ops.add_demo_buy()


@router.post("/demo-sell")
def ops_demo_sell():
    """新增一条模拟结算(卖出池)用于微信推送联调(测完可删除)"""
    return ops.add_demo_sell()
