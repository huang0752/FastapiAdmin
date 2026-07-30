"""后台任务的租户/actor 执行上下文与协作检查点。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.role.model import RoleModel
from app.api.v1.module_system.user.model import UserModel
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.dependencies import resolve_effective_permissions

from ..business.task.model import BusinessTaskModel
from .exceptions import BusinessTaskCancelled, InvalidBackgroundActorError
from .state import ensure_progress


def utc_now() -> datetime:
    return datetime.now(UTC)


async def build_background_auth(
    db: AsyncSession,
    *,
    tenant_id: int,
    actor_user_id: int | None,
    required_permissions: tuple[str, ...] = (),
) -> AuthSchema:
    """从任务可信字段重建受租户约束的后台认证上下文。"""
    if actor_user_id is None:
        raise InvalidBackgroundActorError("业务任务缺少明确 actor，拒绝隐式系统管理员执行")
    user = (
        await db.execute(
            select(UserModel)
            .options(selectinload(UserModel.roles).selectinload(RoleModel.menus))
            .where(UserModel.id == actor_user_id, UserModel.status == 0, UserModel.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if user is None:
        raise InvalidBackgroundActorError("任务 actor 不存在或已停用")
    tenant = (
        await db.execute(
            select(TenantModel).where(
                TenantModel.id == tenant_id,
                TenantModel.status.in_((0, 1)),
                TenantModel.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise InvalidBackgroundActorError("任务租户不存在或已停用")
    if not user.is_superuser:
        membership = (
            await db.execute(
                select(TenantUserModel).where(
                    TenantUserModel.user_id == actor_user_id,
                    TenantUserModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise InvalidBackgroundActorError("任务 actor 已不属于任务租户")
    auth = AuthSchema.for_background_task(db=db, user=user, tenant_id=tenant_id)
    if required_permissions:
        effective_permissions = await resolve_effective_permissions(
            auth,
            bypass_role_grants=user.is_superuser,
            require_active_package=True,
        )
        missing = set(required_permissions) - effective_permissions
        if missing:
            raise InvalidBackgroundActorError("任务 actor 已失去处理器所需权限")
    return auth


@dataclass(slots=True)
class BusinessTaskContext:
    task_id: int
    tenant_id: int
    actor_user_id: int
    trace_id: str
    execution_token: str
    db: AsyncSession
    auth: AuthSchema
    session_factory: async_sessionmaker[AsyncSession]

    async def heartbeat(self) -> bool:
        now = utc_now()
        async with self.session_factory() as db:
            result = await db.execute(
                update(BusinessTaskModel)
                .where(
                    BusinessTaskModel.id == self.task_id,
                    BusinessTaskModel.tenant_id == self.tenant_id,
                    BusinessTaskModel.status == "running",
                    BusinessTaskModel.execution_token == self.execution_token,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=settings.CELERY_LEASE_SECONDS),
                )
            )
            await db.commit()
            return bool(result.rowcount)

    async def update_progress(self, progress: int) -> bool:
        async with self.session_factory() as db:
            current = (
                await db.execute(
                    select(BusinessTaskModel.progress).where(
                        BusinessTaskModel.id == self.task_id,
                        BusinessTaskModel.tenant_id == self.tenant_id,
                        BusinessTaskModel.status == "running",
                        BusinessTaskModel.execution_token == self.execution_token,
                    )
                )
            ).scalar_one_or_none()
            if current is None:
                return False
            ensure_progress(current, progress)
            result = await db.execute(
                update(BusinessTaskModel)
                .where(
                    BusinessTaskModel.id == self.task_id,
                    BusinessTaskModel.tenant_id == self.tenant_id,
                    BusinessTaskModel.status == "running",
                    BusinessTaskModel.execution_token == self.execution_token,
                    BusinessTaskModel.progress <= progress,
                )
                .values(progress=progress)
            )
            await db.commit()
            return bool(result.rowcount)

    async def check_cancelled(self) -> None:
        async with self.session_factory() as db:
            cancel_requested_at = (
                await db.execute(
                    select(BusinessTaskModel.cancel_requested_at).where(
                        BusinessTaskModel.id == self.task_id,
                        BusinessTaskModel.tenant_id == self.tenant_id,
                        BusinessTaskModel.execution_token == self.execution_token,
                    )
                )
            ).scalar_one_or_none()
        if cancel_requested_at is not None:
            raise BusinessTaskCancelled("业务任务已请求取消")
