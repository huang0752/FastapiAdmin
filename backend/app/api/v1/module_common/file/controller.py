from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    Form,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse

from app.common.response import ResponseSchema, SuccessResponse, UploadFileResponse
from app.core.base_schema import AuthSchema, PrivateUploadResponseSchema, UploadResponseSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute
from app.utils.upload_util import UploadUtil

from .service import FileService

FileRouter = APIRouter(route_class=OperationLogRoute, prefix="/file", tags=["公共模块", "文件管理"])

UploadType = Literal[
    "file",
    "avatar",
    "param",
    "resource",
    "tenant_logo",
    "tenant_favicon",
    "tenant_login_bg",
    "tenant_brand",
]

@FileRouter.post(
    "/upload",
    summary="上传文件",
    response_model=ResponseSchema[UploadResponseSchema],
    dependencies=[Depends(AuthPermission(["module_common:file:upload"]))],
)
async def upload_controller(
    file: UploadFile,
    request: Request,
    upload_type: Annotated[
        UploadType | None,
        Query(description="上传类型: file=通用文件, avatar=头像, param=参数配置, resource=监控资源, tenant_*=租户品牌"),
    ] = "file",
    target_path: Annotated[str | None, Form(description="目标目录路径（仅 resource 类型支持）")] = None,
) -> JSONResponse:
    result = await FileService.upload_service(
        base_url=str(request.base_url),
        file=file,
        upload_type=upload_type or "file",
        target_path=target_path,
    )
    return SuccessResponse(data=result, msg="上传文件成功")

@FileRouter.post(
    "/download",
    summary="下载文件",
    dependencies=[Depends(AuthPermission(["module_common:file:download"]))],
)
async def download_controller(
    background_tasks: BackgroundTasks,
    file_path: Annotated[str, Body(description="文件路径")],
    delete: Annotated[bool, Body(description="是否删除文件")] = False,
) -> FileResponse:
    result = await FileService.download_service(file_path=file_path)
    if delete:
        background_tasks.add_task(UploadUtil.delete_file, Path(result.file_path))
    return UploadFileResponse(file_path=result.file_path, filename=result.file_name)


@FileRouter.post(
    "/private/upload",
    summary="上传租户私有文件",
    response_model=ResponseSchema[PrivateUploadResponseSchema],
)
async def private_upload_controller(
    file: UploadFile,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_common:file:upload"]))],
    namespace: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]+$", description="业务命名空间")] = "file",
) -> JSONResponse:
    result = await FileService.save_private_service(
        file=file,
        tenant_id=auth.tenant_id or 0,
        namespace=namespace,
    )
    return SuccessResponse(data=result, msg="上传私有文件成功")


@FileRouter.post(
    "/private/download",
    summary="下载租户私有文件",
)
async def private_download_controller(
    storage_key: Annotated[str, Body(embed=True, description="私有文件存储键")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_common:file:download"]))],
) -> FileResponse:
    result = FileService.resolve_private_service(
        storage_key=storage_key,
        tenant_id=auth.tenant_id or 0,
    )
    return UploadFileResponse(file_path=result.file_path, filename=result.file_name)


@FileRouter.delete(
    "/private/delete",
    summary="删除租户私有文件",
    response_model=ResponseSchema[dict],
)
async def private_delete_controller(
    storage_key: Annotated[str, Body(embed=True, description="私有文件存储键")],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_common:file:delete"]))],
) -> JSONResponse:
    released_bytes = FileService.delete_private_service(
        storage_key=storage_key,
        tenant_id=auth.tenant_id or 0,
    )
    return SuccessResponse(data={"released_bytes": released_bytes}, msg="删除私有文件成功")
