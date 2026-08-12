from fastapi import APIRouter

router = APIRouter(prefix="/screen", tags=["冷链车辆大屏"])


@router.get("/overview", summary="冷链车辆大屏单聚合入口")
async def overview_contract() -> dict:
    """业务聚合将在后续切片接入；当前不返回伪造指标。"""
    return {"state": "empty", "data": {}, "missing_fields": ["business_aggregate"], "errors": []}
