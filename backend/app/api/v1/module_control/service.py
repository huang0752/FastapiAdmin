"""Business rules for Control application administration."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import anyio
from fastapi import status
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import require_platform_admin
from app.core.exceptions import CustomException
from app.utils.hash_bcrpy_util import PwdUtil

from .model import ControlApplicationModel, ControlSSOLaunchTicketModel, ControlTenantApplicationModel, ControlUserApplicationGrantModel
from .schema import (
    ControlApplicationCreateSchema,
    ControlApplicationOutSchema,
    ControlApplicationQueryParam,
    ControlApplicationSecretSchema,
    ControlApplicationUpdateSchema,
    ControlClientSecretResultSchema,
    ControlGrantMemberSchema,
    ControlIdentityClaimsSchema,
    ControlLaunchResultSchema,
    ControlPortalApplicationSchema,
    ControlTenantApplicationCreateSchema,
    ControlTenantApplicationOutSchema,
    ControlTenantApplicationUpdateSchema,
    ControlTenantAvailableApplicationSchema,
    validate_provisioning_configuration,
)

_CONTROL_CLIENT_DUMMY_SECRET_HASH = "$pbkdf2-sha256$600000$Y29udHJvbC1kdW1teS12MQ==$wir/SHDQlYh4yN+4TUkE+cBmkQ5uGU6BCXK96tQf4u4="
_CONTROL_CLIENT_KDF_LIMITER = anyio.CapacityLimiter(4)
_CONTROL_CLIENT_AUTH_ADMISSION = anyio.CapacityLimiter(4)
_CONTROL_CLIENT_AUTH_ADMISSION_TIMEOUT_SECONDS = 0.05


async def authenticate_control_client(db, *, client_id: str, client_secret: str) -> ControlApplicationModel:
    """Authenticate one registered target application without exposing its secret hash."""
    try:
        with anyio.fail_after(_CONTROL_CLIENT_AUTH_ADMISSION_TIMEOUT_SECONDS):
            await _CONTROL_CLIENT_AUTH_ADMISSION.acquire()
    except TimeoutError:
        raise CustomException(
            msg="Client 认证暂时不可用",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from None

    try:
        application = (await db.execute(select(ControlApplicationModel).where(ControlApplicationModel.client_id == client_id))).scalar_one_or_none()
        password_hash = application.client_secret_hash if application is not None else _CONTROL_CLIENT_DUMMY_SECRET_HASH
        verified = await anyio.to_thread.run_sync(
            PwdUtil.verify_password,
            client_secret,
            password_hash,
            limiter=_CONTROL_CLIENT_KDF_LIMITER,
        )
        if application is None or not verified:
            raise CustomException(msg="Client 认证失败", status_code=status.HTTP_401_UNAUTHORIZED)
        return application
    finally:
        _CONTROL_CLIENT_AUTH_ADMISSION.release()


async def _lock_active_control_state(
    db,
    *,
    site_id: int,
    tenant_id: int,
    application_id: int | None = None,
    application_code: str | None = None,
) -> tuple[TenantModel, SiteModel, ControlApplicationModel] | None:
    """Lock the shared Control state in one stable order before access decisions."""
    tenant = (
        await db.execute(
            select(TenantModel)
            .where(
                TenantModel.id == tenant_id,
                TenantModel.site_id == site_id,
                TenantModel.status == 0,
                TenantModel.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    site = (
        await db.execute(
            select(SiteModel)
            .where(
                SiteModel.id == site_id,
                SiteModel.status == 0,
                SiteModel.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if tenant is None or site is None:
        return None

    application_filters = [
        ControlApplicationModel.site_id == site_id,
        ControlApplicationModel.status == 0,
        ControlApplicationModel.is_deleted.is_(False),
    ]
    if application_id is not None:
        application_filters.append(ControlApplicationModel.id == application_id)
    elif application_code is not None:
        application_filters.append(ControlApplicationModel.code == application_code)
    else:
        raise RuntimeError("应用锁定缺少定位条件")
    application = (await db.execute(select(ControlApplicationModel).where(*application_filters).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if application is None:
        return None
    return tenant, site, application


def _build_launch_redirect(callback_url: str, plain_code: str) -> str:
    """Append a launch code where the configured callback route can read it."""
    callback = urlsplit(callback_url)
    outer_query = parse_qsl(callback.query, keep_blank_values=True)
    fragment = urlsplit(callback.fragment)
    fragment_query = parse_qsl(fragment.query, keep_blank_values=True)
    if any(key == "code" for key, _value in [*outer_query, *fragment_query]):
        raise CustomException(msg="应用回调地址已包含 code 参数", status_code=status.HTTP_409_CONFLICT)

    encoded_code = urlencode({"code": plain_code})
    if fragment.path.startswith("/"):
        next_fragment_query = f"{fragment.query}&{encoded_code}" if fragment.query else encoded_code
        next_fragment = urlunsplit((fragment.scheme, fragment.netloc, fragment.path, next_fragment_query, fragment.fragment))
        return urlunsplit((callback.scheme, callback.netloc, callback.path, callback.query, next_fragment))

    next_outer_query = f"{callback.query}&{encoded_code}" if callback.query else encoded_code
    return urlunsplit((callback.scheme, callback.netloc, callback.path, next_outer_query, callback.fragment))


class _ControlApplicationCRUD(CRUDBase[ControlApplicationModel, ControlApplicationCreateSchema, ControlApplicationUpdateSchema]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=ControlApplicationModel, auth=auth)


async def _require_settled_target_identity(db, *, opening_id=None, application_id=None):
    """Do not move live qualifications to another product/tenant identity."""
    statement = select(ControlUserApplicationGrantModel.id).where(
        ControlUserApplicationGrantModel.is_deleted.is_(False),
        (ControlUserApplicationGrantModel.desired_state == "active") | (ControlUserApplicationGrantModel.sync_status != "succeeded"),
    )
    if opening_id is not None:
        statement = statement.where(ControlUserApplicationGrantModel.tenant_application_id == opening_id)
    if application_id is not None:
        statement = statement.where(ControlUserApplicationGrantModel.tenant_application_id.in_(
            select(ControlTenantApplicationModel.id).where(ControlTenantApplicationModel.application_id == application_id)
        ))
    if await db.scalar(statement.limit(1)) is not None:
        raise CustomException(msg="请先撤销并完成同步，再修改产品或目标租户标识", status_code=409)


class ControlApplicationService:
    def __init__(self, auth: AuthSchema) -> None:
        if auth.db is None:
            raise RuntimeError("中控应用管理缺少数据库会话")
        self.auth = auth
        self.crud = _ControlApplicationCRUD(auth)

    def _site_id(self) -> int:
        if self.auth.site_id is None:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.site_id

    async def _get_scoped(self, application_id: int) -> ControlApplicationModel:
        application = (
            await self.auth.db.execute(
                select(ControlApplicationModel).where(
                    ControlApplicationModel.id == application_id,
                    ControlApplicationModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if application is None:
            raise CustomException(msg="应用不存在", status_code=status.HTTP_404_NOT_FOUND)
        if application.site_id != self._site_id():
            raise CustomException(msg="禁止访问其他站点的应用", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return application

    async def _ensure_code_available(self, code: str, application_id: int | None = None) -> None:
        stmt = select(ControlApplicationModel.id).where(
            ControlApplicationModel.site_id == self._site_id(),
            ControlApplicationModel.code == code,
        )
        if application_id is not None:
            stmt = stmt.where(ControlApplicationModel.id != application_id)
        if (await self.auth.db.execute(stmt.limit(1))).scalar_one_or_none() is not None:
            raise CustomException(msg="同一站点的应用编码已存在", status_code=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _new_credentials() -> tuple[str, str, str]:
        plain_secret = secrets.token_urlsafe(32)
        client_id = f"ctrl_{secrets.token_urlsafe(18)}"
        return client_id, plain_secret, PwdUtil.hash_password(plain_secret)

    @require_platform_admin
    async def create(self, data: ControlApplicationCreateSchema) -> ControlApplicationSecretSchema:
        await self._ensure_code_available(data.code)
        client_id, plain_secret, secret_hash = self._new_credentials()
        values = data.model_dump()
        values.update(
            site_id=self._site_id(),
            client_id=client_id,
            client_secret_hash=secret_hash,
        )
        application = await self.crud.create(values)
        public_data = ControlApplicationOutSchema.model_validate(application).model_dump()
        return ControlApplicationSecretSchema(**public_data, client_secret=plain_secret)

    @require_platform_admin
    async def detail(self, application_id: int) -> ControlApplicationOutSchema:
        return ControlApplicationOutSchema.model_validate(await self._get_scoped(application_id))

    @require_platform_admin
    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ControlApplicationQueryParam,
        order_by: list[dict[str, str]],
    ) -> PageResultSchema:
        search_values = {key: value for key, value in vars(search).items() if key in {"code", "name", "status", "created_time", "updated_time"} and value is not None}
        search_values["site_id"] = self._site_id()
        search_values["is_deleted"] = False
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by,
            search=search_values,
            out_schema=ControlApplicationOutSchema,
        )

    @require_platform_admin
    async def update(self, application_id: int, data: ControlApplicationUpdateSchema) -> ControlApplicationOutSchema:
        current = await self._get_scoped(application_id)
        await self.auth.db.refresh(current, with_for_update=True)
        if data.code is not None and data.code != current.code:
            await _require_settled_target_identity(self.auth.db, application_id=application_id)
        if data.code is not None:
            await self._ensure_code_available(data.code, application_id)
        values = data.model_dump(exclude_unset=True)
        provisioning_enabled = values.get("provisioning_enabled", current.provisioning_enabled)
        provisioning_url = values.get("provisioning_url", current.provisioning_url)
        try:
            validate_provisioning_configuration(
                enabled=provisioning_enabled,
                provisioning_url=provisioning_url,
            )
            from .schema import validate_entitlement_configuration
            validate_entitlement_configuration(
                enabled=values.get("entitlement_sync_enabled", current.entitlement_sync_enabled),
                sync_url=values.get("entitlement_sync_url", current.entitlement_sync_url),
            )
            if "entitlement_sync_url" in values and not values["entitlement_sync_url"] and current.entitlement_sync_url:
                await _require_settled_target_identity(self.auth.db, application_id=application_id)
        except ValueError as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_400_BAD_REQUEST) from exc
        application = await self.crud.update(application_id, data)
        if application.status != 0:
            from .user_entitlement.lifecycle import revoke_control_access
            await revoke_control_access(self.auth, application_ids=[application_id])
        return ControlApplicationOutSchema.model_validate(application)

    @require_platform_admin
    async def reset_secret(self, application_id: int) -> ControlClientSecretResultSchema:
        application = await self._get_scoped(application_id)
        plain_secret = secrets.token_urlsafe(32)
        application.client_secret_hash = PwdUtil.hash_password(plain_secret)
        application.updated_id = self.auth.user.id if self.auth.user else None
        await self.auth.db.flush()
        return ControlClientSecretResultSchema(client_id=application.client_id, client_secret=plain_secret)

    @require_platform_admin
    async def delete(self, application_id: int) -> None:
        await self._get_scoped(application_id)
        active_bindings = (
            await self.auth.db.execute(
                select(func.count())
                .select_from(ControlTenantApplicationModel)
                .where(
                    ControlTenantApplicationModel.application_id == application_id,
                    ControlTenantApplicationModel.site_id == self._site_id(),
                    ControlTenantApplicationModel.status == 0,
                    ControlTenantApplicationModel.is_deleted.is_(False),
                )
            )
        ).scalar_one()
        if active_bindings:
            raise CustomException(msg="应用仍有启用中的租户开通，无法删除", status_code=status.HTTP_409_CONFLICT)
        await self.crud.delete([application_id])
        from .user_entitlement.lifecycle import revoke_control_access
        await revoke_control_access(self.auth, application_ids=[application_id])


class ControlTenantApplicationService:
    """Platform-side lifecycle for opening target applications to tenants."""

    def __init__(self, auth: AuthSchema) -> None:
        if auth.db is None:
            raise RuntimeError("中控租户应用管理缺少数据库会话")
        self.auth = auth

    def _site_id(self) -> int:
        if self.auth.site_id is None:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.site_id

    async def _get_scoped(self, tenant_application_id: int) -> ControlTenantApplicationModel:
        opening = await self.auth.db.get(ControlTenantApplicationModel, tenant_application_id)
        if opening is None:
            raise CustomException(msg="租户应用开通记录不存在", status_code=status.HTTP_404_NOT_FOUND)
        if opening.site_id != self._site_id():
            raise CustomException(msg="禁止访问其他站点的租户应用", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return opening

    async def _validate_same_site(self, tenant_id: int, application_id: int) -> None:
        application = await self.auth.db.get(ControlApplicationModel, application_id)
        if application is None or application.is_deleted:
            raise CustomException(msg="应用不存在", status_code=status.HTTP_404_NOT_FOUND)
        tenant = await self.auth.db.get(TenantModel, tenant_id)
        if tenant is None or tenant.is_deleted:
            raise CustomException(msg="租户不存在", status_code=status.HTTP_404_NOT_FOUND)
        if application.site_id != self._site_id() or tenant.site_id != self._site_id():
            raise CustomException(msg="应用、租户与当前站点不一致", code=10403, status_code=status.HTTP_403_FORBIDDEN)

    async def available_for_current_tenant(self) -> list[ControlTenantAvailableApplicationSchema]:
        if self.auth.tenant_id is None:
            raise CustomException(msg="租户上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        rows = (
            await self.auth.db.execute(
                select(ControlTenantApplicationModel, ControlApplicationModel)
                .join(
                    ControlApplicationModel,
                    ControlApplicationModel.id == ControlTenantApplicationModel.application_id,
                )
                .where(
                    ControlTenantApplicationModel.site_id == self._site_id(),
                    ControlTenantApplicationModel.tenant_id == self.auth.tenant_id,
                    ControlTenantApplicationModel.status == 0,
                    ControlTenantApplicationModel.is_deleted.is_(False),
                    ControlApplicationModel.site_id == self._site_id(),
                    ControlApplicationModel.status == 0,
                    ControlApplicationModel.is_deleted.is_(False),
                )
                .order_by(ControlApplicationModel.sort.desc(), ControlTenantApplicationModel.id.desc())
            )
        ).all()
        return [
            ControlTenantAvailableApplicationSchema(
                tenant_application_id=opening.id,
                application_id=application.id,
                application_code=application.code,
                application_name=application.name,
                target_tenant_code=opening.target_tenant_code,
                status=opening.status,
            )
            for opening, application in rows
        ]

    @require_platform_admin
    async def page(self, page_no: int, page_size: int) -> PageResultSchema[ControlTenantApplicationOutSchema]:
        filters = (
            ControlTenantApplicationModel.site_id == self._site_id(),
            ControlTenantApplicationModel.is_deleted.is_(False),
        )
        total = (await self.auth.db.execute(select(func.count()).select_from(ControlTenantApplicationModel).where(*filters))).scalar_one()
        openings = (
            (await self.auth.db.execute(select(ControlTenantApplicationModel).where(*filters).order_by(ControlTenantApplicationModel.id.desc()).offset((page_no - 1) * page_size).limit(page_size)))
            .scalars()
            .all()
        )
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=page_no * page_size < total,
            items=[ControlTenantApplicationOutSchema.model_validate(item) for item in openings],
        )

    @require_platform_admin
    async def create(self, data: ControlTenantApplicationCreateSchema) -> ControlTenantApplicationOutSchema:
        await self._validate_same_site(data.tenant_id, data.application_id)
        opening = (
            await self.auth.db.execute(
                select(ControlTenantApplicationModel).where(
                    ControlTenantApplicationModel.tenant_id == data.tenant_id,
                    ControlTenantApplicationModel.application_id == data.application_id,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        actor_id = self.auth.user.id if self.auth.user else None
        if opening is None:
            opening = ControlTenantApplicationModel(
                site_id=self._site_id(),
                tenant_id=data.tenant_id,
                application_id=data.application_id,
                target_tenant_code=data.target_tenant_code,
                status=0,
                opened_at=now,
                created_id=actor_id,
                updated_id=actor_id,
            )
            self.auth.db.add(opening)
        else:
            if opening.site_id != self._site_id():
                raise CustomException(msg="租户应用记录站点不一致", code=10403, status_code=status.HTTP_403_FORBIDDEN)
            await self.auth.db.refresh(opening, with_for_update=True)
            if opening.target_tenant_code != data.target_tenant_code:
                await _require_settled_target_identity(self.auth.db, opening_id=opening.id)
            opening.target_tenant_code = data.target_tenant_code
            opening.status = 0
            opening.opened_at = now
            opening.is_deleted = False
            opening.deleted_time = None
            opening.deleted_id = None
            opening.updated_id = actor_id
        await self.auth.db.flush()
        return ControlTenantApplicationOutSchema.model_validate(opening)

    @require_platform_admin
    async def update(self, tenant_application_id: int, data: ControlTenantApplicationUpdateSchema) -> ControlTenantApplicationOutSchema:
        opening = await self._get_scoped(tenant_application_id)
        if opening.is_deleted:
            raise CustomException(msg="租户应用开通记录不存在", status_code=status.HTTP_404_NOT_FOUND)
        await self._validate_same_site(opening.tenant_id, opening.application_id)
        await self.auth.db.refresh(opening, with_for_update=True)
        values = data.model_dump(exclude_unset=True)
        if "target_tenant_code" in values and values["target_tenant_code"] != opening.target_tenant_code:
            await _require_settled_target_identity(self.auth.db, opening_id=opening.id)
        for key, value in values.items():
            setattr(opening, key, value)
        opening.updated_id = self.auth.user.id if self.auth.user else None
        await self.auth.db.flush()
        if opening.status != 0:
            from .user_entitlement.lifecycle import revoke_control_access
            await revoke_control_access(self.auth, opening_ids=[opening.id])
        return ControlTenantApplicationOutSchema.model_validate(opening)

    @require_platform_admin
    async def delete(self, tenant_application_id: int) -> None:
        opening = await self._get_scoped(tenant_application_id)
        if opening.is_deleted:
            return
        opening.status = 1
        opening.is_deleted = True
        opening.deleted_time = datetime.now(UTC)
        opening.deleted_id = self.auth.user.id if self.auth.user else None
        await self.auth.db.flush()
        from .user_entitlement.lifecycle import revoke_control_access
        await revoke_control_access(self.auth, opening_ids=[opening.id])


class ControlUserApplicationGrantService:
    """Tenant-scoped explicit grants; tenant identity always comes from auth."""

    def __init__(self, auth: AuthSchema) -> None:
        if auth.db is None:
            raise RuntimeError("中控用户应用授权缺少数据库会话")
        self.auth = auth

    def _tenant_id(self) -> int:
        if self.auth.tenant_id is None:
            raise CustomException(msg="租户上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.tenant_id

    def _site_id(self) -> int:
        if self.auth.site_id is None:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.site_id

    async def _get_enabled_opening(
        self,
        tenant_application_id: int,
        *,
        for_update: bool = False,
    ) -> ControlTenantApplicationModel:
        query = select(ControlTenantApplicationModel).where(ControlTenantApplicationModel.id == tenant_application_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        opening = (await self.auth.db.execute(query)).scalar_one_or_none()
        if opening is None:
            raise CustomException(msg="租户应用开通记录不存在", status_code=status.HTTP_404_NOT_FOUND)
        if opening.tenant_id != self._tenant_id() or opening.site_id != self._site_id():
            raise CustomException(msg="禁止操作其他租户或站点的应用授权", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        if opening.is_deleted or opening.status != 0:
            raise CustomException(msg="租户应用尚未启用", status_code=status.HTTP_409_CONFLICT)
        application = await self.auth.db.get(ControlApplicationModel, opening.application_id)
        if application is None or application.is_deleted or application.status != 0 or application.site_id != self._site_id():
            raise CustomException(msg="应用尚未启用", status_code=status.HTTP_409_CONFLICT)
        return opening

    async def _require_member(
        self,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> UserModel:
        query = (
            select(UserModel)
            .join(
                TenantUserModel,
                and_(
                    TenantUserModel.user_id == UserModel.id,
                    TenantUserModel.tenant_id == self._tenant_id(),
                ),
            )
            .where(UserModel.id == user_id, UserModel.is_deleted.is_(False))
        )
        if for_update:
            # 成员关系是 grant/revoke 的中央真值；只锁 membership，
            # 与成员删除统一使用 membership -> grant 锁序。
            query = query.with_for_update(of=TenantUserModel)
        row = (await self.auth.db.execute(query)).scalar_one_or_none()
        if row is None:
            raise CustomException(msg="用户不是当前租户成员", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return row

    async def list_members(self, tenant_application_id: int) -> list[ControlGrantMemberSchema]:
        opening = await self._get_enabled_opening(tenant_application_id)
        rows = (
            await self.auth.db.execute(
                select(UserModel, ControlUserApplicationGrantModel)
                .join(
                    TenantUserModel,
                    and_(TenantUserModel.user_id == UserModel.id, TenantUserModel.tenant_id == self._tenant_id()),
                )
                .outerjoin(
                    ControlUserApplicationGrantModel,
                    and_(
                        ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                        ControlUserApplicationGrantModel.user_id == UserModel.id,
                        ControlUserApplicationGrantModel.tenant_id == self._tenant_id(),
                        ControlUserApplicationGrantModel.site_id == self._site_id(),
                    ),
                )
                .where(UserModel.is_deleted.is_(False))
                .order_by(UserModel.id)
            )
        ).all()
        return [
            ControlGrantMemberSchema(
                user_id=user.id,
                username=user.username,
                name=user.name,
                mobile=user.mobile,
                email=user.email,
                avatar=user.avatar,
                granted=grant is not None and grant.desired_state == "active" and not grant.is_deleted,
                grant_id=grant.id if grant is not None else None,
                desired_state=grant.desired_state if grant is not None else None,
                sync_status=grant.sync_status if grant is not None else None,
                sync_version=grant.sync_version if grant is not None else 0,
                error=grant.last_error_message if grant is not None else None,
                launchable=bool(grant is not None and user.status == 0 and not grant.is_deleted and grant.desired_state == "active" and grant.sync_status == "succeeded"),
            )
            for user, grant in rows
        ]

    async def ensure_grant(self, tenant_application_id: int, user_id: int) -> ControlUserApplicationGrantModel:
        """Resolve the stable grant ledger row before the command state transition."""
        await self._require_member(user_id, for_update=True)
        opening = await self._get_enabled_opening(tenant_application_id, for_update=True)
        grant = (
            await self.auth.db.execute(
                select(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                    ControlUserApplicationGrantModel.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        actor_id = self.auth.user.id if self.auth.user else None
        if grant is None:
            try:
                async with self.auth.db.begin_nested():
                    grant = ControlUserApplicationGrantModel(
                        site_id=self._site_id(),
                        tenant_application_id=opening.id,
                        tenant_id=self._tenant_id(),
                        user_id=user_id,
                        status=1,
                        desired_state="inactive",
                        sync_status="succeeded",
                        sync_version=0,
                        granted_at=now,
                        created_id=actor_id,
                        updated_id=actor_id,
                    )
                    self.auth.db.add(grant)
                    await self.auth.db.flush()
            except IntegrityError:
                grant = (
                    await self.auth.db.execute(
                        select(ControlUserApplicationGrantModel)
                        .where(
                            ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                            ControlUserApplicationGrantModel.user_id == user_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one()
        else:
            if grant.tenant_id != self._tenant_id() or grant.site_id != self._site_id():
                raise CustomException(msg="授权记录租户或站点不一致", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        # Legacy revoke used soft deletion. The state-machine ledger is stable;
        # desired_state is now authoritative while these fields remain mirrors.
        grant.is_deleted = False
        grant.deleted_time = None
        grant.deleted_id = None
        grant.updated_id = actor_id
        await self.auth.db.flush()
        return grant


class ControlPortalService:
    """Current-user application catalog and one-time launch issuance."""

    def __init__(self, auth: AuthSchema) -> None:
        if auth.db is None:
            raise RuntimeError("中控应用中心缺少数据库会话")
        if auth.user is None or auth.site_id is None or auth.tenant_id is None:
            raise CustomException(msg="当前用户、站点或租户上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        self.auth = auth

    def _active_application_query(self, *, require_succeeded: bool = False):
        query = (
            select(ControlApplicationModel, ControlUserApplicationGrantModel)
            .join(
                ControlTenantApplicationModel,
                and_(
                    ControlTenantApplicationModel.application_id == ControlApplicationModel.id,
                    ControlTenantApplicationModel.site_id == self.auth.site_id,
                    ControlTenantApplicationModel.tenant_id == self.auth.tenant_id,
                    ControlTenantApplicationModel.status == 0,
                    ControlTenantApplicationModel.is_deleted.is_(False),
                ),
            )
            .join(
                UserModel,
                and_(
                    UserModel.id == self.auth.user.id,
                    UserModel.status == 0,
                    UserModel.is_deleted.is_(False),
                ),
            )
            .join(
                ControlUserApplicationGrantModel,
                and_(
                    ControlUserApplicationGrantModel.tenant_application_id == ControlTenantApplicationModel.id,
                    ControlUserApplicationGrantModel.site_id == self.auth.site_id,
                    ControlUserApplicationGrantModel.tenant_id == self.auth.tenant_id,
                    ControlUserApplicationGrantModel.user_id == self.auth.user.id,
                    ControlUserApplicationGrantModel.desired_state == "active",
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                ),
            )
            .join(
                TenantUserModel,
                and_(
                    TenantUserModel.user_id == UserModel.id,
                    TenantUserModel.tenant_id == self.auth.tenant_id,
                ),
            )
            .join(
                TenantModel,
                and_(
                    TenantModel.id == self.auth.tenant_id,
                    TenantModel.site_id == self.auth.site_id,
                    TenantModel.status == 0,
                    TenantModel.is_deleted.is_(False),
                ),
            )
            .join(
                SiteModel,
                and_(
                    SiteModel.id == self.auth.site_id,
                    SiteModel.status == 0,
                    SiteModel.is_deleted.is_(False),
                ),
            )
            .where(
                ControlApplicationModel.site_id == self.auth.site_id,
                ControlApplicationModel.status == 0,
                ControlApplicationModel.is_deleted.is_(False),
            )
        )
        if require_succeeded:
            query = query.where(ControlUserApplicationGrantModel.sync_status == "succeeded")
        return query

    async def my_applications(self) -> list[ControlPortalApplicationSchema]:
        rows = (await self.auth.db.execute(self._active_application_query().order_by(ControlApplicationModel.sort, ControlApplicationModel.id))).all()
        return [
            ControlPortalApplicationSchema(
                code=application.code,
                name=application.name,
                description=application.description,
                icon=application.icon,
                base_url=application.base_url,
                status=application.status,
                sort=application.sort,
                grant_id=grant.id,
                desired_state=grant.desired_state,
                sync_status=grant.sync_status,
                sync_version=grant.sync_version,
                error=grant.last_error_message,
                launchable=grant.sync_status == "succeeded",
            )
            for application, grant in rows
        ]

    async def launch(self, application_code: str) -> ControlLaunchResultSchema:
        user = (
            await self.auth.db.execute(
                select(UserModel)
                .where(
                    UserModel.id == self.auth.user.id,
                    UserModel.status == 0,
                    UserModel.is_deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        membership = (
            await self.auth.db.execute(
                select(TenantUserModel)
                .where(
                    TenantUserModel.user_id == self.auth.user.id,
                    TenantUserModel.tenant_id == self.auth.tenant_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if user is None or membership is None or membership.role not in {"owner", "admin", "member"}:
            raise CustomException(msg="应用不存在或当前用户无权访问", code=10403, status_code=status.HTTP_403_FORBIDDEN)

        control_state = await _lock_active_control_state(
            self.auth.db,
            site_id=self.auth.site_id,
            tenant_id=self.auth.tenant_id,
            application_code=application_code,
        )
        if control_state is None:
            raise CustomException(msg="应用不存在或当前用户无权访问", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        _tenant, _site, application = control_state

        opening = (
            await self.auth.db.execute(
                select(ControlTenantApplicationModel)
                .where(
                    ControlTenantApplicationModel.application_id == application.id,
                    ControlTenantApplicationModel.site_id == self.auth.site_id,
                    ControlTenantApplicationModel.tenant_id == self.auth.tenant_id,
                    ControlTenantApplicationModel.status == 0,
                    ControlTenantApplicationModel.is_deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if opening is None:
            raise CustomException(msg="应用不存在或当前用户无权访问", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        grant = (
            await self.auth.db.execute(
                select(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                    ControlUserApplicationGrantModel.site_id == self.auth.site_id,
                    ControlUserApplicationGrantModel.tenant_id == self.auth.tenant_id,
                    ControlUserApplicationGrantModel.user_id == self.auth.user.id,
                    ControlUserApplicationGrantModel.desired_state == "active",
                    ControlUserApplicationGrantModel.sync_status == "succeeded",
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if grant is None:
            raise CustomException(msg="应用不存在或当前用户无权访问", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        plain_code = secrets.token_urlsafe(48)
        redirect_url = _build_launch_redirect(application.callback_url, plain_code)
        code_hash = hashlib.sha256(plain_code.encode()).hexdigest()
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=60)
        self.auth.db.add(
            ControlSSOLaunchTicketModel(
                code_hash=code_hash,
                application_id=application.id,
                site_id=self.auth.site_id,
                tenant_id=self.auth.tenant_id,
                user_id=self.auth.user.id,
                target_tenant_code=opening.target_tenant_code,
                status="issued",
                issued_at=issued_at,
                expires_at=expires_at,
            )
        )
        await self.auth.db.flush()
        return ControlLaunchResultSchema(redirect_url=redirect_url, expires_at=expires_at)


class ControlSSOExchangeService:
    """Authenticate a target Client and atomically redeem one launch ticket."""

    @staticmethod
    async def exchange(
        db,
        *,
        client_id: str,
        client_secret: str,
        plain_code: str,
        issuer: str,
        request_ip: str | None,
    ) -> ControlIdentityClaimsSchema:
        authenticated_application = await authenticate_control_client(db, client_id=client_id, client_secret=client_secret)

        now = datetime.now(UTC)
        code_hash = hashlib.sha256(plain_code.encode()).hexdigest()
        ticket = (
            await db.execute(
                select(ControlSSOLaunchTicketModel)
                .where(
                    ControlSSOLaunchTicketModel.code_hash == code_hash,
                    ControlSSOLaunchTicketModel.application_id == authenticated_application.id,
                    ControlSSOLaunchTicketModel.status == "issued",
                    ControlSSOLaunchTicketModel.expires_at > now,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if ticket is None:
            raise CustomException(msg="启动码无效、已过期或已使用", status_code=status.HTTP_400_BAD_REQUEST)

        user = (
            await db.execute(
                select(UserModel)
                .where(
                    UserModel.id == ticket.user_id,
                    UserModel.status == 0,
                    UserModel.is_deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        membership = (
            await db.execute(
                select(TenantUserModel)
                .where(
                    TenantUserModel.user_id == ticket.user_id,
                    TenantUserModel.tenant_id == ticket.tenant_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if user is None or membership is None or membership.role not in {"owner", "admin", "member"}:
            raise CustomException(msg="应用开通或用户授权已变更", code=10403, status_code=status.HTTP_403_FORBIDDEN)

        control_state = await _lock_active_control_state(
            db,
            site_id=ticket.site_id,
            tenant_id=ticket.tenant_id,
            application_id=ticket.application_id,
        )
        if control_state is None:
            raise CustomException(msg="应用开通或用户授权已变更", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        tenant, site, application = control_state

        opening = (
            await db.execute(
                select(ControlTenantApplicationModel)
                .where(
                    ControlTenantApplicationModel.application_id == ticket.application_id,
                    ControlTenantApplicationModel.site_id == ticket.site_id,
                    ControlTenantApplicationModel.tenant_id == ticket.tenant_id,
                    ControlTenantApplicationModel.target_tenant_code == ticket.target_tenant_code,
                    ControlTenantApplicationModel.status == 0,
                    ControlTenantApplicationModel.is_deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if opening is None:
            raise CustomException(msg="应用开通或用户授权已变更", code=10403, status_code=status.HTTP_403_FORBIDDEN)

        grant = (
            await db.execute(
                select(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                    ControlUserApplicationGrantModel.site_id == ticket.site_id,
                    ControlUserApplicationGrantModel.tenant_id == ticket.tenant_id,
                    ControlUserApplicationGrantModel.user_id == ticket.user_id,
                    ControlUserApplicationGrantModel.desired_state == "active",
                    ControlUserApplicationGrantModel.sync_status == "succeeded",
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if grant is None:
            raise CustomException(msg="应用开通或用户授权已变更", code=10403, status_code=status.HTTP_403_FORBIDDEN)

        consumed_ticket_id = (
            await db.execute(
                update(ControlSSOLaunchTicketModel)
                .where(
                    ControlSSOLaunchTicketModel.id == ticket.id,
                    ControlSSOLaunchTicketModel.status == "issued",
                )
                .values(status="redeemed", redeemed_at=now, redeemed_ip=request_ip)
                .returning(ControlSSOLaunchTicketModel.id)
            )
        ).scalar_one_or_none()
        if consumed_ticket_id is None:
            raise CustomException(msg="启动码无效、已过期或已使用", status_code=status.HTTP_400_BAD_REQUEST)
        return ControlIdentityClaimsSchema(
            issuer=issuer,
            central_user_uuid=user.uuid,
            name=user.name,
            mobile=user.mobile,
            email=user.email,
            avatar=user.avatar,
            status=user.status,
            site_code=site.code,
            central_tenant_code=tenant.code,
            central_tenant_role=membership.role,
            central_is_superuser=user.is_superuser,
            target_tenant_code=ticket.target_tenant_code,
        )
