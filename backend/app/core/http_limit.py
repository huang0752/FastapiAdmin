from math import ceil
from time import monotonic
from typing import NoReturn

from fastapi import Request, Response
from fastapi_limiter.depends import RateLimiter
from sqlalchemy import select
from starlette.websockets import WebSocket

from app.core.exceptions import CustomException


class TenantPackageRateLimiter:
    """按当前租户套餐动态选择窗口请求上限。"""

    _limit_cache: dict[int, tuple[float, int]] = {}

    def __init__(self, *, default_times: int = 200, seconds: int = 10, cache_seconds: int = 30) -> None:
        self.default_times = default_times
        self.seconds = seconds
        self.cache_seconds = cache_seconds

    @classmethod
    def clear_cache(cls) -> None:
        cls._limit_cache.clear()

    async def _load_tenant_limit(self, tenant_id: int) -> int:
        from app.api.v1.module_platform.package.model import PackageModel
        from app.api.v1.module_platform.tenant.model import TenantModel
        from app.core.database import async_db_session

        async with async_db_session() as db:
            limit = (
                await db.execute(
                    select(PackageModel.rate_limit)
                    .join(TenantModel, TenantModel.package_id == PackageModel.id)
                    .where(
                        TenantModel.id == tenant_id,
                        TenantModel.is_deleted.is_(False),
                        PackageModel.is_deleted.is_(False),
                        PackageModel.status == 0,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        return int(limit) if limit is not None and limit > 0 else self.default_times

    async def resolve_times(self, tenant_id: int | None) -> int:
        """公共接口和平台租户走安全默认值，普通租户读取套餐。"""
        if tenant_id is None or tenant_id == 1:
            return self.default_times
        cached = self._limit_cache.get(tenant_id)
        now = monotonic()
        if cached and now - cached[0] < self.cache_seconds:
            return cached[1]
        limit = await self._load_tenant_limit(tenant_id)
        self._limit_cache[tenant_id] = (now, limit)
        return limit

    async def __call__(self, request: Request, response: Response) -> None:
        tenant_id = getattr(request.state, "tenant_id", None)
        times = await self.resolve_times(tenant_id)
        await RateLimiter(times=times, seconds=self.seconds)(request, response)


def http_limit_callback(request: Request, response: Response, expire: int) -> NoReturn:
    """
    HTTP 触发限流时的默认回调：抛出 429。

    参数:
    - request (Request): 当前请求。
    - response (Response): 当前响应（未直接使用，保留与限流器签名一致）。
    - expire (int): 剩余冷却毫秒数。

    返回:
    - 无（始终抛出 CustomException）。
    """
    expires = ceil(expire / 30)
    raise CustomException(
        status_code=429,
        msg="请求过于频繁，请稍后重试！",
        data={"Retry-After": str(expires)},
    )


async def ws_limit_callback(ws: WebSocket, expire: int) -> None:
    """
    WebSocket 触发限流时的默认回调：关闭连接。

    参数:
    - ws (WebSocket): 当前 WebSocket。
    - expire (int): 剩余冷却毫秒数。

    返回:
    - None
    """
    expires = ceil(expire / 30)
    await ws.close(code=1008, reason=f"请求过于频繁，请稍后重试！{expires} 秒后重试")
