import hashlib
import secrets
from datetime import datetime
from typing import ClassVar

import httpx
from fastapi import Request
from redis.asyncio.client import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.user.model import UserModel
from app.config.setting import settings
from app.core.base_schema import JWTOutSchema
from app.core.exceptions import CustomException
from app.utils.hash_bcrpy_util import PwdUtil

from .control_sso_schema import ControlIdentityClaims
from .service import LoginService, resolve_request_site


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
        claims = await cls._exchange_code(code)
        site = await resolve_request_site(db, request)
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

        user = await cls._provision(db, site.id, tenant.id, claims)
        return await LoginService.create_token(
            request=request,
            redis=redis,
            user=user,
            login_type="control_sso",
            tenant_id=tenant.id,
            site_id=site.id,
        )

    @classmethod
    async def _exchange_code(cls, code: str) -> ControlIdentityClaims:
        issuer = settings.CONTROL_SSO_ISSUER.rstrip("/")
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
                        settings.CONTROL_SSO_CLIENT_ID,
                        settings.CONTROL_SSO_CLIENT_SECRET,
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
        identity = await cls._find_identity(db, site_id, claims.issuer, claims.central_user_uuid)
        if identity:
            user = await cls._load_user(db, identity.local_user_id)
            await cls._sync_user_and_membership(db, user, tenant_id, claims)
            identity.last_login_time = datetime.now()
            await db.commit()
            return user

        try:
            async with db.begin_nested():
                user = UserModel(
                    username=cls._synthetic_username(claims.issuer, claims.central_user_uuid),
                    password=PwdUtil.hash_password(secrets.token_urlsafe(48)),
                    auth_source="federated",
                    password_login_enabled=False,
                    name=claims.name,
                    mobile=claims.mobile,
                    email=claims.email,
                    avatar=claims.avatar,
                    status=claims.status,
                    tenant_id=tenant_id,
                )
                db.add(user)
                await db.flush()
                db.add(
                    FederatedIdentityModel(
                        site_id=site_id,
                        issuer=claims.issuer,
                        central_user_uuid=claims.central_user_uuid,
                        local_user_id=user.id,
                        last_login_time=datetime.now(),
                    )
                )
                db.add(TenantUserModel(user_id=user.id, tenant_id=tenant_id, role="member", is_default=1))
                await db.flush()
        except IntegrityError:
            identity = await cls._find_identity(db, site_id, claims.issuer, claims.central_user_uuid)
            if not identity:
                raise
            user = await cls._load_user(db, identity.local_user_id)
            await cls._sync_user_and_membership(db, user, tenant_id, claims)
            identity.last_login_time = datetime.now()

        await db.commit()
        return user

    @staticmethod
    async def _find_identity(
        db: AsyncSession,
        site_id: int,
        issuer: str,
        central_user_uuid: str,
    ) -> FederatedIdentityModel | None:
        return (
            await db.execute(
                select(FederatedIdentityModel).where(
                    FederatedIdentityModel.site_id == site_id,
                    FederatedIdentityModel.issuer == issuer,
                    FederatedIdentityModel.central_user_uuid == central_user_uuid,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _load_user(db: AsyncSession, user_id: int) -> UserModel:
        user = (await db.execute(select(UserModel).where(UserModel.id == user_id))).scalar_one_or_none()
        if not user:
            raise CustomException(msg="联邦身份对应的本地用户不存在", status_code=409)
        return user

    @staticmethod
    async def _sync_user_and_membership(
        db: AsyncSession,
        user: UserModel,
        tenant_id: int,
        claims: ControlIdentityClaims,
    ) -> None:
        user.name = claims.name
        user.mobile = claims.mobile
        user.email = claims.email
        user.avatar = claims.avatar
        user.status = claims.status
        user.auth_source = "federated"
        user.password_login_enabled = False
        membership = (
            await db.execute(
                select(TenantUserModel).where(
                    TenantUserModel.user_id == user.id,
                    TenantUserModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if not membership:
            db.add(TenantUserModel(user_id=user.id, tenant_id=tenant_id, role="member", is_default=0))
        await db.flush()

    @staticmethod
    def _synthetic_username(issuer: str, central_user_uuid: str) -> str:
        digest = hashlib.sha256(f"{issuer}\0{central_user_uuid}".encode()).hexdigest()
        return f"control_{digest[:48]}"
