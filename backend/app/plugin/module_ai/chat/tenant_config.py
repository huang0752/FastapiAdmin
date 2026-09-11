"""当前租户共享 AI 配置；管理权限与业务使用权限分离。"""
from typing import Annotated

from fastapi import APIRouter, Depends
from openai import AsyncOpenAI
from redis.asyncio import Redis

from app.common.response import SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, redis_getter, resolve_effective_permissions
from app.core.exceptions import CustomException
from app.core.router_class import OperationLogRoute

from .schema import AiFeatureBindingUpdateSchema, AiModelConfigSchema, AiModelConfigUpdateSchema

MANAGE_PERMISSION = "module_ai:config:manage"
router = APIRouter(prefix="/ai-config", route_class=OperationLogRoute, tags=["租户 AI 配置"])
Auth = Annotated[AuthSchema, Depends(AuthPermission())]
RedisDep = Annotated[Redis, Depends(redis_getter)]


async def can_manage(auth: AuthSchema) -> bool:
    if auth.tenant_id is None or not auth.user:
        return False
    if auth.user.is_superuser:
        return True
    # 仅系统维护的当前租户治理角色；普通自建同名角色不能提升权限。
    if any(role.tenant_id == auth.tenant_id and role.status == 0 and role.is_system and role.code in {"owner", "admin"} for role in auth.user.roles):
        return True
    return MANAGE_PERMISSION in await resolve_effective_permissions(auth)


async def require_manager(auth: Auth) -> AuthSchema:
    if not await can_manage(auth):
        raise CustomException(msg="需要当前租户 AI 配置管理权限", code=10403, status_code=403)
    return auth

Manager = Annotated[AuthSchema, Depends(require_manager)]


@router.get("/capabilities")
async def capabilities(auth: Auth):
    return SuccessResponse(data={"can_manage": await can_manage(auth), "tenant_id": auth.tenant_id})


@router.get("/model")
async def list_models(auth: Manager, redis: RedisDep):
    from .service import AiModelConfigService

    return SuccessResponse(data=await AiModelConfigService(auth, redis, scope="tenant").list())


@router.post("/model")
async def create_model(data: AiModelConfigSchema, auth: Manager, redis: RedisDep):
    from .service import AiModelConfigService

    return SuccessResponse(data=await AiModelConfigService(auth, redis, scope="tenant").create(data))


@router.put("/model/{config_id}")
async def update_model(config_id: str, data: AiModelConfigUpdateSchema, auth: Manager, redis: RedisDep):
    from .service import AiModelConfigService

    return SuccessResponse(data=await AiModelConfigService(auth, redis, scope="tenant").update(config_id, data))


@router.delete("/model/{config_id}")
async def delete_model(config_id: str, auth: Manager, redis: RedisDep):
    from .service import AiModelConfigService

    await AiModelConfigService(auth, redis, scope="tenant").delete(config_id)
    return SuccessResponse()


@router.post("/model/{config_id}/activate")
async def activate_model(config_id: str, auth: Manager, redis: RedisDep):
    from .service import AiModelConfigService

    await AiModelConfigService(auth, redis, scope="tenant").set_active(config_id)
    return SuccessResponse()


@router.post("/model/{config_id}/probe")
async def probe_model(config_id: str, auth: Manager, redis: RedisDep):
    from .service import AiRuntimeService

    config = await AiRuntimeService(auth, redis, scope="tenant")._runtime_config(config_id, source="connection_test")
    try:
        async with AsyncOpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=min(config["timeout_seconds"], 15), max_retries=0) as client:
            response = await client.chat.completions.create(model=config["model_id"], messages=[{"role": "user", "content": "Reply OK."}], max_tokens=16)
            if not response.choices:
                raise ValueError("empty choices")
    except Exception:
        # 供应商错误可能带请求信息，不回传错误正文或密钥。
        raise CustomException(msg="连接检测失败，请检查服务地址、模型和密钥", code=10400, status_code=400) from None
    return SuccessResponse(data={"connected": True}, msg="模型连接成功")


@router.get("/feature")
async def list_features(auth: Manager, redis: RedisDep):
    from .service import AiFeatureBindingService

    return SuccessResponse(data=await AiFeatureBindingService(auth, redis, scope="tenant").list())


@router.put("/feature/{feature_code}")
async def update_feature(feature_code: str, data: AiFeatureBindingUpdateSchema, auth: Manager, redis: RedisDep):
    from .service import AiFeatureBindingService

    return SuccessResponse(data=await AiFeatureBindingService(auth, redis, scope="tenant").upsert(feature_code, data))
