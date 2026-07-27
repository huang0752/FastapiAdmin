from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission, db_getter
from app.core.exceptions import CustomException
from app.core.router_class import OperationLogRoute

from .schema import SiteCreateSchema, SiteOutSchema, SitePublicConfigSchema, SiteQueryParam, SiteUpdateSchema
from .service import SiteService

SiteRouter = APIRouter(route_class=OperationLogRoute, prefix="/site", tags=["平台管理", "站点管理"])


@SiteRouter.get("/public/config", response_model=ResponseSchema[SitePublicConfigSchema], summary="按 Host 获取公开站点配置")
async def get_public_site_config(request: Request, db: Annotated[AsyncSession, Depends(db_getter)]) -> JSONResponse:
    # 不信任可由客户端伪造的 X-Forwarded-Host；反向代理应保留/重写标准 Host。
    site = await SiteService.resolve_by_host(db, request.headers.get("host"))
    if site is None:
        raise CustomException(msg="当前域名未配置站点", status_code=status.HTTP_404_NOT_FOUND)
    return SuccessResponse(data=SitePublicConfigSchema.model_validate(site), msg="获取站点配置成功")


@SiteRouter.get("/detail/{site_id}", response_model=ResponseSchema[SiteOutSchema])
async def get_site_detail(site_id: Annotated[int, Path(gt=0)], auth: Annotated[AuthSchema, Depends(AuthPermission(["module_platform:site:query"]))]) -> JSONResponse:
    return SuccessResponse(data=await SiteService(auth).detail(site_id), msg="获取站点详情成功")


@SiteRouter.get("/list", response_model=ResponseSchema[PageResultSchema[SiteOutSchema]])
async def get_site_list(page: Annotated[PaginationQueryParam, Depends()], search: Annotated[SiteQueryParam, Depends()], auth: Annotated[AuthSchema, Depends(AuthPermission(["module_platform:site:query"]))]) -> JSONResponse:
    return SuccessResponse(data=await SiteService(auth).page(page.page_no or 1, page.page_size or 10, search), msg="查询站点成功")


@SiteRouter.post("/create", response_model=ResponseSchema[SiteOutSchema])
async def create_site(data: SiteCreateSchema, auth: Annotated[AuthSchema, Depends(AuthPermission(["module_platform:site:create"]))]) -> JSONResponse:
    return SuccessResponse(data=await SiteService(auth).create(data), msg="创建站点成功")


@SiteRouter.put("/update/{site_id}", response_model=ResponseSchema[SiteOutSchema])
async def update_site(site_id: Annotated[int, Path(gt=0)], data: SiteUpdateSchema, auth: Annotated[AuthSchema, Depends(AuthPermission(["module_platform:site:update"]))]) -> JSONResponse:
    return SuccessResponse(data=await SiteService(auth).update(site_id, data), msg="更新站点成功")


@SiteRouter.delete("/delete", response_model=ResponseSchema[None])
async def delete_site(ids: Annotated[list[int], Body(min_length=1)], auth: Annotated[AuthSchema, Depends(AuthPermission(["module_platform:site:delete"]))]) -> JSONResponse:
    await SiteService(auth).delete(ids)
    return SuccessResponse(msg="删除站点成功")
