"""HTTP endpoints for Control tenant-provision orchestration."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.logger import logger
from app.core.router_class import OperationLogRoute
from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher

from .schema import ControlTenantProvisionOutSchema, ControlTenantProvisionQueryParam, ControlTenantProvisionUpdateSchema
from .service import ControlTenantProvisionCreateService, ControlTenantProvisionService
from .task_schema import ControlTenantWithProvisionsCreateSchema, ControlTenantWithProvisionsOutSchema

TenantProvisionCreateRouter = APIRouter(route_class=OperationLogRoute, prefix="/tenants", tags=["中控管理", "租户自动开户"])
TenantProvisionRouter = APIRouter(route_class=OperationLogRoute, prefix="/tenant-provisions", tags=["中控管理", "租户自动开户"])


async def _publish_provision_tasks(task_ids: list[int]) -> None:
    dispatcher = BusinessTaskDispatcher()
    for task_id in task_ids:
        try:
            await dispatcher.publish_existing(task_id)
        except Exception as exc:
            # Pending outbox remains recoverable by the existing runtime scanner.
            logger.error("租户自动开户任务发布失败 task_id={} error={}", task_id, type(exc).__name__)


@TenantProvisionCreateRouter.post("/provision", response_model=ResponseSchema[ControlTenantWithProvisionsOutSchema])
async def create_tenant_with_provisions(
    request: Request,
    data: ControlTenantWithProvisionsCreateSchema,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_provision:create"]))],
) -> JSONResponse:
    # The response contains a one-time password, and OperationLogRoute would
    # otherwise replace the outbox-publish background task with its log task.
    request.state.skip_operation_log = True
    outcome = await ControlTenantProvisionCreateService(auth).create(data)
    background_tasks.add_task(_publish_provision_tasks, outcome.task_ids)
    return SuccessResponse(data=outcome.result, msg="租户已创建，产品正在开通")


@TenantProvisionRouter.get("", response_model=ResponseSchema[PageResultSchema[ControlTenantProvisionOutSchema]])
async def list_tenant_provisions(
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ControlTenantProvisionQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_provision:query"]))],
) -> JSONResponse:
    result = await ControlTenantProvisionService(auth).page(page.page_no, page.page_size, search)
    return SuccessResponse(data=result, msg="查询租户开通状态成功")


@TenantProvisionRouter.put("/{provision_id}", response_model=ResponseSchema[ControlTenantProvisionOutSchema])
async def update_tenant_provision(
    provision_id: Annotated[int, Path(gt=0)],
    data: ControlTenantProvisionUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_provision:update"]))],
) -> JSONResponse:
    result = await ControlTenantProvisionService(auth).update(provision_id, data)
    return SuccessResponse(data=result, msg="修改租户开通配置成功")


async def _dispatch_manual_action(
    provision_id: int,
    action: Literal["retry", "reconcile"],
    request: Request,
    background_tasks: BackgroundTasks,
    auth: AuthSchema,
) -> JSONResponse:
    request.state.skip_operation_log = True
    task = await ControlTenantProvisionService(auth).dispatch_action(provision_id, action)
    background_tasks.add_task(_publish_provision_tasks, [task.id])
    return SuccessResponse(data={"task_id": task.id}, msg="人工操作已提交")


@TenantProvisionRouter.post("/{provision_id}/retry", response_model=ResponseSchema[dict])
async def retry_tenant_provision(
    provision_id: Annotated[int, Path(gt=0)],
    request: Request,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_provision:retry"]))],
) -> JSONResponse:
    return await _dispatch_manual_action(provision_id, "retry", request, background_tasks, auth)


@TenantProvisionRouter.post("/{provision_id}/reconcile", response_model=ResponseSchema[dict])
async def reconcile_tenant_provision(
    provision_id: Annotated[int, Path(gt=0)],
    request: Request,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_provision:reconcile"]))],
) -> JSONResponse:
    return await _dispatch_manual_action(provision_id, "reconcile", request, background_tasks, auth)
