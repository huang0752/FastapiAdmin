"""HTTP endpoints for Control application administration."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    ControlApplicationCreateSchema,
    ControlApplicationOutSchema,
    ControlApplicationQueryParam,
    ControlApplicationSecretSchema,
    ControlApplicationUpdateSchema,
    ControlClientSecretResultSchema,
    ControlExchangeInSchema,
    ControlGrantMemberSchema,
    ControlIdentityClaimsSchema,
    ControlLaunchResultSchema,
    ControlPortalApplicationSchema,
    ControlTenantApplicationCreateSchema,
    ControlTenantApplicationOutSchema,
    ControlTenantApplicationUpdateSchema,
    ControlTenantAvailableApplicationSchema,
    ControlUserApplicationGrantOutSchema,
)
from .service import (
    ControlApplicationService,
    ControlPortalService,
    ControlSSOExchangeService,
    ControlTenantApplicationService,
    ControlUserApplicationGrantService,
)
from .user_entitlement.service import ControlUserEntitlementCommandService, publish_entitlement_tasks

ApplicationRouter = APIRouter(route_class=OperationLogRoute, prefix="/applications", tags=["中控管理", "应用管理"])
TenantApplicationRouter = APIRouter(route_class=OperationLogRoute, prefix="/tenant-applications", tags=["中控管理", "租户应用"])
PortalRouter = APIRouter(route_class=OperationLogRoute, prefix="/portal", tags=["中控应用中心"])
SSORouter = APIRouter(prefix="/sso", tags=["中控 SSO"])
http_basic = HTTPBasic()


def _issuer_from_exchange_url(request_url: str) -> str:
    exchange_suffix = "/control/sso/exchange"
    normalized = request_url.split("?", maxsplit=1)[0]
    if not normalized.endswith(exchange_suffix):
        raise RuntimeError("中控 SSO 兑换路由与 issuer 契约不一致")
    return normalized[: -len(exchange_suffix)].rstrip("/")


@ApplicationRouter.get("", response_model=ResponseSchema[PageResultSchema[ControlApplicationOutSchema]])
async def list_applications(
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ControlApplicationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application:query"]))],
) -> JSONResponse:
    result = await ControlApplicationService(auth).page(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询应用成功")


@ApplicationRouter.get("/{application_id}", response_model=ResponseSchema[ControlApplicationOutSchema])
async def get_application(
    application_id: Annotated[int, Path(gt=0)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application:query"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlApplicationService(auth).detail(application_id), msg="获取应用成功")


@ApplicationRouter.post("", response_model=ResponseSchema[ControlApplicationSecretSchema])
async def create_application(
    data: ControlApplicationCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application:create"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlApplicationService(auth).create(data), msg="创建应用成功，请立即保存客户端密钥")


@ApplicationRouter.put("/{application_id}", response_model=ResponseSchema[ControlApplicationOutSchema])
async def update_application(
    application_id: Annotated[int, Path(gt=0)],
    data: ControlApplicationUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application:update"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlApplicationService(auth).update(application_id, data), msg="修改应用成功")


@ApplicationRouter.delete("/{application_id}", response_model=ResponseSchema[None])
async def delete_application(
    application_id: Annotated[int, Path(gt=0)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application:delete"]))],
) -> JSONResponse:
    await ControlApplicationService(auth).delete(application_id)
    return SuccessResponse(msg="删除应用成功")


@ApplicationRouter.post("/{application_id}/reset-secret", response_model=ResponseSchema[ControlClientSecretResultSchema])
async def reset_application_secret(
    application_id: Annotated[int, Path(gt=0)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application:reset_secret"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlApplicationService(auth).reset_secret(application_id), msg="客户端密钥已重置，请立即保存")


@TenantApplicationRouter.get("", response_model=ResponseSchema[PageResultSchema[ControlTenantApplicationOutSchema]])
async def list_tenant_applications(
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_application:query"]))],
) -> JSONResponse:
    result = await ControlTenantApplicationService(auth).page(page.page_no, page.page_size)
    return SuccessResponse(data=result, msg="查询租户应用成功")


@TenantApplicationRouter.get("/available", response_model=ResponseSchema[list[ControlTenantAvailableApplicationSchema]])
async def list_available_tenant_applications(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:user_grant:query"]))],
) -> JSONResponse:
    result = await ControlTenantApplicationService(auth).available_for_current_tenant()
    return SuccessResponse(data=result, msg="查询当前租户已开通应用成功")


@TenantApplicationRouter.post("", response_model=ResponseSchema[ControlTenantApplicationOutSchema])
async def create_tenant_application(
    data: ControlTenantApplicationCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_application:create"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlTenantApplicationService(auth).create(data), msg="开通租户应用成功")


@TenantApplicationRouter.put("/{tenant_application_id}", response_model=ResponseSchema[ControlTenantApplicationOutSchema])
async def update_tenant_application(
    tenant_application_id: Annotated[int, Path(gt=0)],
    data: ControlTenantApplicationUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_application:update"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlTenantApplicationService(auth).update(tenant_application_id, data), msg="修改租户应用成功")


@TenantApplicationRouter.delete("/{tenant_application_id}", response_model=ResponseSchema[None])
async def delete_tenant_application(
    tenant_application_id: Annotated[int, Path(gt=0)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:tenant_application:delete"]))],
) -> JSONResponse:
    await ControlTenantApplicationService(auth).delete(tenant_application_id)
    return SuccessResponse(msg="撤销租户应用成功")


@TenantApplicationRouter.get("/{tenant_application_id}/grants", response_model=ResponseSchema[list[ControlGrantMemberSchema]])
async def list_tenant_application_grants(
    tenant_application_id: Annotated[int, Path(gt=0)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:user_grant:query"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlUserApplicationGrantService(auth).list_members(tenant_application_id), msg="查询用户应用授权成功")


@TenantApplicationRouter.put("/{tenant_application_id}/grants/{user_id}", response_model=ResponseSchema[ControlUserApplicationGrantOutSchema])
async def grant_tenant_application_user(
    tenant_application_id: Annotated[int, Path(gt=0)],
    user_id: Annotated[int, Path(gt=0)],
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:user_grant:update"]))],
) -> JSONResponse:
    grant = await ControlUserApplicationGrantService(auth).ensure_grant(tenant_application_id, user_id)
    task = await ControlUserEntitlementCommandService(auth).set_desired_state(grant.id, "active", mode="grant")
    background_tasks.add_task(publish_entitlement_tasks, [task.id])
    return SuccessResponse(
        data=ControlUserApplicationGrantOutSchema.model_validate(grant),
        msg="用户应用授权已提交",
    )


@TenantApplicationRouter.delete(
    "/{tenant_application_id}/grants/{user_id}",
    response_model=ResponseSchema[ControlUserApplicationGrantOutSchema],
)
async def revoke_tenant_application_user(
    tenant_application_id: Annotated[int, Path(gt=0)],
    user_id: Annotated[int, Path(gt=0)],
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:user_grant:delete"]))],
) -> JSONResponse:
    grant = await ControlUserApplicationGrantService(auth).ensure_grant(tenant_application_id, user_id)
    task = await ControlUserEntitlementCommandService(auth).set_desired_state(grant.id, "inactive", mode="revoke")
    background_tasks.add_task(publish_entitlement_tasks, [task.id])
    return SuccessResponse(
        data=ControlUserApplicationGrantOutSchema.model_validate(grant),
        msg="撤销用户应用授权已提交",
    )


@TenantApplicationRouter.post(
    "/{tenant_application_id}/grants/{user_id}/retry",
    response_model=ResponseSchema[ControlUserApplicationGrantOutSchema],
)
async def retry_tenant_application_user(
    tenant_application_id: Annotated[int, Path(gt=0)],
    user_id: Annotated[int, Path(gt=0)],
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:user_grant:retry"]))],
) -> JSONResponse:
    grant = await ControlUserApplicationGrantService(auth).ensure_grant(tenant_application_id, user_id)
    task = await ControlUserEntitlementCommandService(auth).retry(grant.id)
    background_tasks.add_task(publish_entitlement_tasks, [task.id])
    return SuccessResponse(
        data=ControlUserApplicationGrantOutSchema.model_validate(grant),
        msg="用户应用授权重试已提交",
    )


@PortalRouter.get("/my-applications", response_model=ResponseSchema[list[ControlPortalApplicationSchema]])
async def list_my_applications(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:portal:query"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlPortalService(auth).my_applications(), msg="查询我的应用成功")


@PortalRouter.post("/applications/{code}/launch", response_model=ResponseSchema[ControlLaunchResultSchema])
async def launch_application(
    code: Annotated[str, Path(min_length=2, max_length=64)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:portal:launch"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlPortalService(auth).launch(code), msg="签发启动码成功")


@SSORouter.post("/exchange", response_model=ResponseSchema[ControlIdentityClaimsSchema])
async def exchange_launch_code(
    request: Request,
    data: ControlExchangeInSchema,
    credentials: Annotated[HTTPBasicCredentials, Depends(http_basic)],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    issuer = _issuer_from_exchange_url(str(request.url))
    result = await ControlSSOExchangeService.exchange(
        db,
        client_id=credentials.username,
        client_secret=credentials.password,
        plain_code=data.code,
        issuer=issuer,
        request_ip=request.client.host if request.client else None,
    )
    return SuccessResponse(data=result, msg="启动码兑换成功")
