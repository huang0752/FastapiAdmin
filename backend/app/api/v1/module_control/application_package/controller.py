"""HTTP API for Control application-package mappings."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import (
    ControlApplicationPackageCreateSchema,
    ControlApplicationPackageOutSchema,
    ControlApplicationPackageQueryParam,
    ControlApplicationPackageUpdateSchema,
)
from .service import ControlApplicationPackageService

ApplicationPackageRouter = APIRouter(route_class=OperationLogRoute, prefix="/application-packages", tags=["中控管理", "应用套餐"])


@ApplicationPackageRouter.get("", response_model=ResponseSchema[PageResultSchema[ControlApplicationPackageOutSchema]])
async def list_application_packages(
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ControlApplicationPackageQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application_package:query"]))],
) -> JSONResponse:
    result = await ControlApplicationPackageService(auth).page(page.page_no, page.page_size, search)
    return SuccessResponse(data=result, msg="查询应用套餐成功")


@ApplicationPackageRouter.post("", response_model=ResponseSchema[ControlApplicationPackageOutSchema])
async def create_application_package(
    data: ControlApplicationPackageCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application_package:create"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlApplicationPackageService(auth).create(data), msg="创建应用套餐成功")


@ApplicationPackageRouter.put("/{package_id}", response_model=ResponseSchema[ControlApplicationPackageOutSchema])
async def update_application_package(
    package_id: Annotated[int, Path(gt=0)],
    data: ControlApplicationPackageUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application_package:update"]))],
) -> JSONResponse:
    return SuccessResponse(data=await ControlApplicationPackageService(auth).update(package_id, data), msg="修改应用套餐成功")


@ApplicationPackageRouter.delete("/{package_id}", response_model=ResponseSchema[None])
async def delete_application_package(
    package_id: Annotated[int, Path(gt=0)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_control:application_package:delete"]))],
) -> JSONResponse:
    await ControlApplicationPackageService(auth).delete(package_id)
    return SuccessResponse(msg="删除应用套餐成功")
