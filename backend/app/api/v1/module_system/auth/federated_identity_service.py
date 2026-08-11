import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.tenant.model import TenantUserModel
from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.user.model import UserModel
from app.core.exceptions import CustomException
from app.utils.hash_bcrpy_util import PwdUtil


@dataclass(frozen=True, slots=True)
class FederatedUserProfile:
    central_user_uuid: str
    name: str
    mobile: str | None
    email: str | None
    avatar: str | None
    status: int


class FederatedIdentityService:
    """在调用方事务中创建或复用联邦身份与租户成员关系。"""

    @classmethod
    async def upsert_user_and_membership(
        cls,
        *,
        db: AsyncSession,
        site_id: int,
        issuer: str,
        tenant_id: int,
        profile: FederatedUserProfile,
        membership_role: str = "member",
        is_default: int = 1,
    ) -> UserModel:
        normalized_issuer = issuer.rstrip("/")
        await cls._ensure_sqlite_outer_transaction(db)
        identity = await cls._find_identity(db, site_id, normalized_issuer, profile.central_user_uuid)
        if identity:
            user = await cls._load_user(db, identity.local_user_id)
            await cls._sync_user_and_membership(
                db=db,
                user=user,
                tenant_id=tenant_id,
                issuer=normalized_issuer,
                profile=profile,
                membership_role=membership_role,
                is_default=is_default,
            )
            identity.last_login_time = datetime.now()
            await db.flush()
            return user

        try:
            async with db.begin_nested():
                user = UserModel(
                    username=cls._synthetic_username(normalized_issuer, profile.central_user_uuid),
                    password=PwdUtil.hash_password(secrets.token_urlsafe(48)),
                    auth_source="federated",
                    password_login_enabled=False,
                    name=profile.name,
                    mobile=profile.mobile,
                    email=profile.email,
                    avatar=profile.avatar,
                    status=profile.status,
                    tenant_id=tenant_id,
                )
                db.add(user)
                await db.flush()
                db.add(
                    FederatedIdentityModel(
                        site_id=site_id,
                        issuer=normalized_issuer,
                        central_user_uuid=profile.central_user_uuid,
                        local_user_id=user.id,
                        last_login_time=datetime.now(),
                    )
                )
                db.add(
                    TenantUserModel(
                        user_id=user.id,
                        tenant_id=tenant_id,
                        role=membership_role,
                        is_default=is_default,
                    )
                )
                await db.flush()
        except IntegrityError:
            identity = await cls._find_identity(db, site_id, normalized_issuer, profile.central_user_uuid)
            if not identity:
                raise
            user = await cls._load_user(db, identity.local_user_id)
            await cls._sync_user_and_membership(
                db=db,
                user=user,
                tenant_id=tenant_id,
                issuer=normalized_issuer,
                profile=profile,
                membership_role=membership_role,
                is_default=is_default,
            )
            identity.last_login_time = datetime.now()

        await db.flush()
        return user

    @staticmethod
    async def _ensure_sqlite_outer_transaction(db: AsyncSession) -> None:
        if db.get_bind().dialect.name == "sqlite":
            # SQLite defers BEGIN until the first DML statement. Without this no-op,
            # releasing the creation savepoint would persist it past an outer rollback.
            await db.execute(update(UserModel).where(UserModel.id == -1).values(id=UserModel.id))

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

    @classmethod
    async def _sync_user_and_membership(
        cls,
        *,
        db: AsyncSession,
        user: UserModel,
        tenant_id: int,
        issuer: str,
        profile: FederatedUserProfile,
        membership_role: str,
        is_default: int,
    ) -> None:
        if user.username.startswith("control_") and len(user.username) > 32:
            user.username = cls._synthetic_username(issuer, profile.central_user_uuid)
        user.name = profile.name
        user.mobile = profile.mobile
        user.email = profile.email
        user.avatar = profile.avatar
        user.status = profile.status
        user.auth_source = "federated"
        user.password_login_enabled = False
        await db.flush()
        membership = await cls._find_membership(db, user.id, tenant_id)
        if membership:
            return
        has_membership = (await db.execute(select(TenantUserModel.id).where(TenantUserModel.user_id == user.id).limit(1))).scalar_one_or_none()
        try:
            async with db.begin_nested():
                db.add(
                    TenantUserModel(
                        user_id=user.id,
                        tenant_id=tenant_id,
                        role=membership_role,
                        is_default=0 if has_membership is not None else is_default,
                    )
                )
                await db.flush()
        except IntegrityError:
            if not await cls._find_membership(db, user.id, tenant_id):
                raise

    @staticmethod
    async def _find_membership(
        db: AsyncSession,
        user_id: int,
        tenant_id: int,
    ) -> TenantUserModel | None:
        return (
            await db.execute(
                select(TenantUserModel).where(
                    TenantUserModel.user_id == user_id,
                    TenantUserModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _synthetic_username(issuer: str, central_user_uuid: str) -> str:
        digest = hashlib.sha256(f"{issuer}\0{central_user_uuid}".encode()).hexdigest()
        return f"control_{digest[:24]}"
