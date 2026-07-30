from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    BusinessTaskActionOutSchema,
    BusinessTaskDiagnosticOutSchema,
    BusinessTaskHealthOutSchema,
    BusinessTaskOutSchema,
    BusinessTaskQueryParam,
    DemoBatchCleanOutSchema,
    DemoBatchOutSchema,
    DemoBatchTriggerSchema,
)
from .service import BusinessTaskService, DemoBatchService

BusinessTaskRouter = APIRouter(route_class=OperationLogRoute, prefix="/business/task", tags=["任务调度", "业务任务"])
DemoBatchRouter = APIRouter(route_class=OperationLogRoute, prefix="/demo-batch", tags=["任务调度", "试用数据"])


@BusinessTaskRouter.get("/list", summary="查询业务任务", response_model=ResponseSchema[PageResultSchema[BusinessTaskOutSchema]])
async def list_business_task_controller(
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[BusinessTaskQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:business_task:query"]))],
) -> JSONResponse:
    result = await BusinessTaskService(auth).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询业务任务成功")


@BusinessTaskRouter.get("/detail/{id}", summary="获取业务任务详情", response_model=ResponseSchema[BusinessTaskOutSchema])
async def detail_business_task_controller(
    id: Annotated[int, Path(description="业务任务ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:business_task:detail"]))],
) -> JSONResponse:
    result = await BusinessTaskService(auth).detail(id=id)
    return SuccessResponse(data=result, msg="获取业务任务详情成功")


@BusinessTaskRouter.post("/cancel/{id}", summary="安全取消业务任务", response_model=ResponseSchema[BusinessTaskActionOutSchema])
async def cancel_business_task_controller(
    id: Annotated[int, Path(description="业务任务ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:business_task:cancel"]))],
) -> JSONResponse:
    result = await BusinessTaskService(auth).cancel(id=id)
    return SuccessResponse(data=result, msg="业务任务取消请求已受理")


@BusinessTaskRouter.post("/retry/{id}", summary="安全重投业务任务", response_model=ResponseSchema[BusinessTaskActionOutSchema])
async def retry_business_task_controller(
    id: Annotated[int, Path(description="业务任务ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:business_task:retry"]))],
) -> JSONResponse:
    result = await BusinessTaskService(auth).retry(id=id)
    return SuccessResponse(data=result, msg="业务任务已重新投递")


@BusinessTaskRouter.get("/diagnostic/{id}", summary="业务任务运行诊断", response_model=ResponseSchema[BusinessTaskDiagnosticOutSchema])
async def diagnostic_business_task_controller(
    id: Annotated[int, Path(description="业务任务ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:business_task:monitor"]))],
) -> JSONResponse:
    result = await BusinessTaskService(auth).diagnostic(id=id)
    return SuccessResponse(data=result, msg="获取业务任务诊断成功")


@BusinessTaskRouter.get("/monitor/health", summary="Celery业务任务运行时健康", response_model=ResponseSchema[BusinessTaskHealthOutSchema])
async def monitor_business_task_health_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:business_task:monitor"]))],
) -> JSONResponse:
    _ = auth
    from app.plugin.module_task.runtime.health import get_business_task_health

    result = await get_business_task_health()
    return SuccessResponse(data=result, msg="获取业务任务运行时健康成功")


@DemoBatchRouter.post("/trigger", summary="触发试用数据初始化", response_model=ResponseSchema[DemoBatchOutSchema])
async def trigger_demo_batch_controller(
    data: DemoBatchTriggerSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:demo_batch:execute"]))],
) -> JSONResponse:
    result = await DemoBatchService(auth).trigger(data=data)
    return SuccessResponse(data=result, msg="试用数据初始化任务已创建")


@DemoBatchRouter.delete("/clean/{demo_batch_id}", summary="清理试用数据", response_model=ResponseSchema[DemoBatchCleanOutSchema])
async def clean_demo_batch_controller(
    demo_batch_id: Annotated[str, Path(description="试用数据批次ID")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_task:demo_batch:delete"]))],
) -> JSONResponse:
    result = await DemoBatchService(auth).clean(demo_batch_id=demo_batch_id)
    return SuccessResponse(data=result, msg="试用数据清理任务已创建")
