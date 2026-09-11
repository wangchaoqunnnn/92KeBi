"""打板操作台 REST(ops)。"""
from fastapi import APIRouter, HTTPException, Query

from .. import ops

router = APIRouter(prefix="/api/ops")

_ov_cache = {"ts": 0.0, "val": None}


def _clear_ov_cache():
    _ov_cache["ts"] = 0.0
    _ov_cache["val"] = None


@router.get("/overview")
def ops_overview():
    import time as _t
    now = _t.time()
    if _ov_cache["val"] is not None and now - _ov_cache["ts"] < 1.0:
        return _ov_cache["val"]
    val = ops.overview()
    _ov_cache.update({"ts": now, "val": val})
    return val


@router.post("/flush")
def ops_flush():
    """立即执行一次自动盯盘扫描(买点/卖点/观察池)"""
    from .. import market_cache
    view = market_cache.get_view(max_age=0, wait=True)
    ctx = market_cache.get_ctx()
    res = ops.sweep(view=view, ctx=ctx)
    _clear_ov_cache()
    return {"ok": True, **res}


@router.post("/ignore")
def ops_ignore(pool: str = Query(..., pattern="^(buy|watch)$"), code: str = Query(...)):
    res = ops.ignore_item(pool, code)
    _clear_ov_cache()
    return res


@router.post("/manual-sell")
def ops_manual_sell(code: str = Query(...)):
    res = ops.manual_sell(code)
    _clear_ov_cache()
    return res


@router.post("/manual-watch")
def ops_manual_watch(q: str = Query("", max_length=30)):
    """手动加入观察池: q 支持 6 位代码或股票名称(由后端解析+算法评分)"""
    res = ops.manual_watch(q)
    _clear_ov_cache()
    return res


@router.post("/delete")
def ops_delete(item_id: int = Query(...)):
    """管理删除(仅卖出池记录)"""
    res = ops.delete_sell(item_id)
    _clear_ov_cache()
    return res


@router.post("/remove-buy")
def ops_remove_buy(item_id: int = Query(...)):
    """移除买入池持仓(直接删除该数据行)"""
    res = ops.remove_buy(item_id)
    _clear_ov_cache()
    return res


@router.post("/push-test")
def ops_push_test():
    """微信推送连通性测试(需配置 WECHAT_WEBHOOK 环境变量)"""
    return {"ok": True, **ops.wechat_push_test()}


@router.post("/demo-buy")
def ops_demo_buy():
    """新增一条模拟持仓(买入池)用于微信推送联调(测完可移除)"""
    res = ops.add_demo_buy()
    _clear_ov_cache()
    return res


@router.post("/demo-sell")
def ops_demo_sell():
    """新增一条模拟结算(卖出池)用于微信推送联调(测完可删除)"""
    res = ops.add_demo_sell()
    _clear_ov_cache()
    return res


@router.post("/admin/t1-fix")
def ops_t1_fix(rollback: int = Query(0, ge=0, le=1)):
    """T+1 修复: 查出“当日买入当日卖出”的真实记录; rollback=1 时回滚为买入池持仓"""
    res = ops.t1_fix_sells(rollback=bool(rollback))
    _clear_ov_cache()
    return res


@router.get("/admin/audit-sell")
def ops_audit_sell():
    """审计: 列出“卖出池中无更早买入流水”的可疑卖出(排查未买入即卖出)"""
    return ops.audit_sell_origins()


@router.get("/risk-check")
def ops_risk_check():
    """止损自检: 每只持仓的现价/浮亏/是否触发止损/为何暂未卖出"""
    return ops.risk_check()
