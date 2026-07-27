from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import IndustrySamplePackOut, IndustryTermOut
from .service import IndustrySampleService

IndustryRouter = APIRouter(route_class=OperationLogRoute, prefix="/industry", tags=["任务调度", "行业样例"])


@IndustryRouter.get("/sample-packs", summary="查询行业样例包", response_model=ResponseSchema[list[IndustrySamplePackOut]])
async def industry_sample_packs_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission())],
) -> JSONResponse:
    _ = auth
    return SuccessResponse(data=IndustrySampleService.sample_packs(), msg="查询行业样例包成功")


@IndustryRouter.get("/terms", summary="查询行业词库", response_model=ResponseSchema[list[IndustryTermOut]])
async def industry_terms_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission())],
    module: Annotated[str | None, Query(description="业务模块")] = None,
) -> JSONResponse:
    _ = auth
    return SuccessResponse(data=IndustrySampleService.terms(module=module), msg="查询行业词库成功")
