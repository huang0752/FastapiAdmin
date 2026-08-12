from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi_limiter.depends import RateLimiter
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter
from app.core.exceptions import CustomException
from app.core.router_class import OperationLogRoute
from app.utils.ip_local_util import get_client_ip

from .schema import UsageCertificatePlatformPage, UsageCertificatePreviewOut, UsageCertificatePublicOut
from .service import UsageCertificateService

TENANT_CERTIFICATE_PERMISSION = "module_platform:usage-certificate:tenant-query"
PLATFORM_CERTIFICATE_PERMISSION = "module_platform:usage-certificate:platform-query"

TenantUsageCertificateRouter = APIRouter(route_class=OperationLogRoute, prefix="/tenant/usage-certificate", tags=["软件使用证明"])
PlatformUsageCertificateRouter = APIRouter(route_class=OperationLogRoute, prefix="/usage-certificate", tags=["平台管理", "软件使用证明"])
PublicUsageCertificateRouter = APIRouter(prefix="/public/usage-certificate", tags=["公开软件使用证明"])


def _pdf_response(content: bytes, filename: str) -> Response:
    encoded = quote(filename)
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}", "download-filename": encoded})


def _require_platform(auth: AuthSchema) -> None:
    if not auth.user or not auth.user.is_superuser or auth.tenant_id != 1:
        raise CustomException(msg="无平台管理权限", status_code=403)


@TenantUsageCertificateRouter.get("/preview", response_model=ResponseSchema[UsageCertificatePreviewOut])
async def tenant_preview(request: Request, auth: Annotated[AuthSchema, Depends(AuthPermission([TENANT_CERTIFICATE_PERMISSION]))]) -> JSONResponse:
    if not auth.tenant_id:
        raise CustomException(msg="当前会话缺少租户信息", status_code=403)
    tenant = await UsageCertificateService.tenant_by_id(auth.db, auth.tenant_id)
    return SuccessResponse(data=UsageCertificateService.preview(tenant, request_ip=get_client_ip(request)), msg="查询成功")


@TenantUsageCertificateRouter.get("/download")
async def tenant_download(request: Request, auth: Annotated[AuthSchema, Depends(AuthPermission([TENANT_CERTIFICATE_PERMISSION]))]) -> Response:
    if not auth.tenant_id:
        raise CustomException(msg="当前会话缺少租户信息", status_code=403)
    tenant = await UsageCertificateService.tenant_by_id(auth.db, auth.tenant_id)
    result = await UsageCertificateService.pdf(tenant, request_ip=get_client_ip(request))
    return _pdf_response(result.content, result.filename)


@PlatformUsageCertificateRouter.get("/list", response_model=ResponseSchema[UsageCertificatePlatformPage])
async def platform_list(
    auth: Annotated[AuthSchema, Depends(AuthPermission([PLATFORM_CERTIFICATE_PERMISSION]))],
    page_no: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: str | None = None,
    currently_valid: bool | None = None,
) -> JSONResponse:
    _require_platform(auth)
    result = await UsageCertificateService.platform_page(auth.db, page_no=page_no, page_size=page_size, keyword=keyword, currently_valid=currently_valid)
    return SuccessResponse(data=result, msg="查询成功")


@PlatformUsageCertificateRouter.get("/{id}/preview", response_model=ResponseSchema[UsageCertificatePreviewOut])
async def platform_preview(request: Request, id: Annotated[int, Path(ge=1)], auth: Annotated[AuthSchema, Depends(AuthPermission([PLATFORM_CERTIFICATE_PERMISSION]))]) -> JSONResponse:
    _require_platform(auth)
    tenant = await UsageCertificateService.tenant_by_id(auth.db, id)
    return SuccessResponse(data=UsageCertificateService.preview(tenant, request_ip=get_client_ip(request)), msg="查询成功")


@PlatformUsageCertificateRouter.get("/{id}/download")
async def platform_download(request: Request, id: Annotated[int, Path(ge=1)], auth: Annotated[AuthSchema, Depends(AuthPermission([PLATFORM_CERTIFICATE_PERMISSION]))]) -> Response:
    _require_platform(auth)
    tenant = await UsageCertificateService.tenant_by_id(auth.db, id)
    result = await UsageCertificateService.pdf(tenant, request_ip=get_client_ip(request))
    return _pdf_response(result.content, result.filename)


@PublicUsageCertificateRouter.get("/{token}", response_model=ResponseSchema[UsageCertificatePublicOut], dependencies=[Depends(RateLimiter(times=30, seconds=60))])
async def public_verify(token: Annotated[str, Path(max_length=64)], db: Annotated[AsyncSession, Depends(db_getter)]) -> JSONResponse:
    if len(token) < 20:
        raise CustomException(msg="无法核验此证明", status_code=404)
    try:
        tenant = await UsageCertificateService.tenant_by_token(db, token)
    except CustomException as exc:
        raise CustomException(msg="无法核验此证明", status_code=404) from exc
    return SuccessResponse(data=UsageCertificateService.public_out(tenant), msg="核验成功")
