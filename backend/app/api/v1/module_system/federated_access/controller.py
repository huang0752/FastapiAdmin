import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.config.setting import settings
from app.core.dependencies import db_session_getter, redis_getter
from app.core.exceptions import CustomException
from app.core.router_class import OperationLogRoute

from .schema import ControlUserAccessSyncIn, ControlUserAccessSyncOut
from .service import ControlUserAccessSyncService

FederatedAccessRouter = APIRouter(
    route_class=OperationLogRoute,
    prefix="/auth/control/access",
    tags=["系统管理", "中控访问资格"],
)


@FederatedAccessRouter.post(
    "/sync",
    summary="同步中控用户访问资格",
    response_model=ResponseSchema[ControlUserAccessSyncOut],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["code"],
                        "additionalProperties": False,
                        "properties": {
                            "code": {
                                "type": "string",
                                "minLength": 20,
                                "maxLength": 512,
                            }
                        },
                    }
                }
            },
        }
    },
)
async def control_user_access_sync_controller(
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session_getter)],
    redis: Annotated[Redis, Depends(redis_getter)],
) -> JSONResponse:
    # code 是一次性凭据，必须在读取/校验 body 前先禁用操作日志。
    request.state.skip_operation_log = True
    if not settings.CONTROL_USER_ACCESS_SYNC_ENABLED:
        raise HTTPException(
            status_code=404,
            detail="中控用户访问资格同步未启用",
        )
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CustomException(msg="同步请求格式不正确", status_code=422) from None
    try:
        data = ControlUserAccessSyncIn.model_validate(payload)
    except ValidationError:
        raise CustomException(msg="同步请求参数不正确", status_code=422) from None
    result = await ControlUserAccessSyncService.sync(
        request=request,
        db=db,
        redis=redis,
        code=data.code,
    )
    return SuccessResponse(data=result, msg="用户访问资格同步完成")
