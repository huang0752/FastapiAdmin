from types import SimpleNamespace
from typing import ClassVar

import httpx
from fastapi import Request
from redis.asyncio.client import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.user.model import UserModel
from app.config.setting import settings
from app.core.base_schema import JWTOutSchema
from app.core.exceptions import CustomException

from .control_sso_schema import ControlIdentityClaims
from .federated_identity_service import FederatedIdentityService, FederatedUserProfile
from .model import FederatedIdentityModel
from .service import LoginService, resolve_request_site
from .session_registry import (
    UserSessionRegistry,
    require_active_federated_entitlement,
)


class ControlSSOClientService:
    """使用一次性启动码完成中控身份兑换和本地影子账号开户。"""

    transport: ClassVar[httpx.AsyncBaseTransport | None] = None

    @classmethod
    async def exchange_and_login(
        cls,
        request: Request,
        db: AsyncSession,
        redis: Redis,
        code: str,
    ) -> JWTOutSchema:
        site = await resolve_request_site(db, request)
        claims = await cls._exchange_code(code, site.code)
        if claims.site_code != site.code:
            raise CustomException(msg="启动码站点与当前访问站点不一致", status_code=403)

        tenant = (
            await db.execute(
                select(TenantModel).where(
                    TenantModel.site_id == site.id,
                    TenantModel.code == claims.target_tenant_code,
                    TenantModel.status == 0,
                    TenantModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if not tenant:
            raise CustomException(msg="目标租户不存在或已停用", status_code=400)

        existing_user_id = (
            await db.execute(
                select(FederatedIdentityModel.local_user_id).where(
                    FederatedIdentityModel.site_id == site.id,
                    FederatedIdentityModel.issuer == claims.issuer,
                    FederatedIdentityModel.central_user_uuid == claims.central_user_uuid,
                )
            )
        ).scalar_one_or_none()
        site_id = int(site.id)
        tenant_id = int(tenant.id)
        tenant_code = str(tenant.code)
        # 只读定位完成后立即归还连接；等待 Redis fence 时不占用连接池。
        # 该端点使用 db_session_getter，后续写事务在 fence 内显式开启。
        await db.rollback()
        if existing_user_id is not None:
            # 在任何身份/成员写入前先获取与 inactive revoke 共享的 fence，
            # 保持 Redis fence -> DB write 的统一锁序。
            async with UserSessionRegistry.user_fences(
                redis,
                [(site_id, tenant_id, int(existing_user_id))],
            ) as ownerships:
                return await cls._provision_and_login(
                    request=request,
                    db=db,
                    redis=redis,
                    claims=claims,
                    site_id=site_id,
                    tenant_id=tenant_id,
                    tenant_code=tenant_code,
                    fence_ownerships=ownerships,
                )
        return await cls._provision_and_login(
            request=request,
            db=db,
            redis=redis,
            claims=claims,
            site_id=site_id,
            tenant_id=tenant_id,
            tenant_code=tenant_code,
            fence_ownerships=None,
        )

    @classmethod
    async def _provision_and_login(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        redis: Redis,
        claims: ControlIdentityClaims,
        site_id: int,
        tenant_id: int,
        tenant_code: str,
        fence_ownerships,
    ) -> JWTOutSchema:
        async with db.begin():
            if fence_ownerships is not None:
                await UserSessionRegistry.ensure_ownerships(fence_ownerships)
            user = await cls._provision(db, site_id, tenant_id, claims)
            user.is_superuser = (
                claims.central_is_superuser
                and claims.central_tenant_code == "system"
                and claims.target_tenant_code == "system"
                and tenant_code == "system"
            )
            if claims.central_tenant_role in {"owner", "admin"}:
                await TenantService.sync_federated_tenant_manager_role(
                    db,
                    tenant_id,
                    user.id,
                    claims.central_tenant_role,
                )
            if fence_ownerships is not None:
                await UserSessionRegistry.ensure_ownerships(fence_ownerships)
            await db.flush()
            # 既有联邦用户在 user fence 内重查；首次开户在强制开关开启时
            # 也必须已有 active entitlement，否则不会签出 token。
            await require_active_federated_entitlement(
                db,
                user=user,
                site_id=site_id,
                tenant_id=tenant_id,
            )
            if fence_ownerships is not None:
                await UserSessionRegistry.ensure_ownerships(fence_ownerships)
            token_user = SimpleNamespace(
                id=user.id,
                username=user.username,
                name=user.name,
                is_superuser=user.is_superuser,
                last_login=user.last_login,
            )
        if fence_ownerships is not None:
            await UserSessionRegistry.ensure_ownerships(fence_ownerships)
        return await LoginService.create_token(
            request=request,
            redis=redis,
            user=token_user,
            login_type="control_sso",
            tenant_id=tenant_id,
            site_id=site_id,
            _fence_ownerships=fence_ownerships,
        )

    @classmethod
    async def _exchange_code(cls, code: str, site_code: str) -> ControlIdentityClaims:
        issuer = settings.CONTROL_SSO_ISSUER.rstrip("/")
        try:
            credentials = settings.control_client_for_site(site_code)
        except ValueError as exc:
            raise CustomException(msg="当前站点未配置中控客户端", status_code=503) from exc
        timeout_seconds = settings.CONTROL_SSO_TIMEOUT_SECONDS
        timeout = httpx.Timeout(
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=cls.transport) as client:
                response = await client.post(
                    f"{issuer}/control/sso/exchange",
                    json={"code": code},
                    auth=httpx.BasicAuth(
                        credentials.client_id,
                        credentials.client_secret.get_secret_value(),
                    ),
                )
                response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            claims = ControlIdentityClaims.model_validate(payload)
        except (httpx.HTTPError, ValueError) as exc:
            raise CustomException(msg="中控启动码兑换失败", status_code=401) from exc

        claims.issuer = claims.issuer.rstrip("/")
        if claims.issuer != issuer:
            raise CustomException(msg="中控签发方不匹配", status_code=401)
        return claims

    @classmethod
    async def _provision(
        cls,
        db: AsyncSession,
        site_id: int,
        tenant_id: int,
        claims: ControlIdentityClaims,
    ) -> UserModel:
        return await FederatedIdentityService.upsert_user_and_membership(
            db=db,
            site_id=site_id,
            issuer=claims.issuer,
            tenant_id=tenant_id,
            profile=FederatedUserProfile(
                central_user_uuid=claims.central_user_uuid,
                name=claims.name,
                mobile=claims.mobile,
                email=claims.email,
                avatar=claims.avatar,
                status=claims.status,
            ),
        )
