from fastapi import APIRouter

router = APIRouter(prefix="/screen", tags=["食品追溯大屏"])


@router.get("/overview", summary="追溯大屏单聚合入口")
async def overview_contract() -> dict:
    """业务聚合将在后续切片接入；当前不返回伪造指标。"""
    return {"state": "empty", "data": {}, "missing_fields": ["business_aggregate"], "errors": []}
