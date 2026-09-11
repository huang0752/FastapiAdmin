from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

import httpx
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.federated_tenant.model import FederatedTenantModel
from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.auth.federated_identity_service import (
    FederatedIdentityService,
    FederatedUserProfile,
)
from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.auth.service import resolve_request_site
from app.api.v1.module_system.auth.session_registry import UserSessionRegistry
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserRolesModel
from app.config.setting import settings, validate_control_issuer_url
from app.core.assembly import get_assembly
from app.core.exceptions import CustomException

from .default_role import DefaultUserRoleService
from .model import FederatedAccessEntitlementModel, FederatedAccessEventModel
from .schema import ControlUserAccessClaims, ControlUserAccessSyncOut
from .tenant_role_lock import (
    lock_tenant_membership_users,
    lock_tenant_role_assignment,
)


class ControlUserAccessSyncService:
    """用产品自身的 Control Client 兑换 ticket 并原子应用访问资格。"""

    transport: ClassVar[httpx.AsyncBaseTransport | None] = None
    _retryable_exchange_statuses: ClassVar[set[int]] = {429, 502, 503, 504}

    @classmethod
    async def sync(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        redis,
        code: str,
    ) -> ControlUserAccessSyncOut:
        site = await resolve_request_site(db, request)
        claims = await cls._exchange_code(code, site.code)
        fence_scope: tuple[int, int, int] | None = None
        if claims.desired_state == "inactive":
            tenant_id = await cls._tenant_id_for_claims(
                db=db,
                site_id=site.id,
                issuer=claims.issuer.rstrip("/"),
                central_tenant_uuid=claims.central_tenant_uuid,
            )
            local_user_id = (
                await db.execute(
                    select(FederatedAccessEntitlementModel.local_user_id).where(
                        FederatedAccessEntitlementModel.site_id == site.id,
                        FederatedAccessEntitlementModel.tenant_id == tenant_id,
                        FederatedAccessEntitlementModel.issuer == claims.issuer.rstrip("/"),
                        FederatedAccessEntitlementModel.central_user_uuid == claims.central_user_uuid,
                    )
                )
            ).scalar_one_or_none()
            if local_user_id is not None:
                fence_scope = (site.id, tenant_id, int(local_user_id))
        # 不在持有 DB 事务/行锁时等待 Redis fence，避免
        # login(fence -> DB read) 与 sync(DB lock -> fence) 形成锁序反转。
        site = SimpleNamespace(id=int(site.id), code=str(site.code))
        await db.rollback()
        if fence_scope is not None:
            async with UserSessionRegistry.user_fences(
                redis, [fence_scope]
            ) as ownerships:
                return await cls._apply_claims_and_cleanup(
                    request=request,
                    db=db,
                    redis=redis,
                    claims=claims,
                    site=site,
                    fence_ownerships=ownerships,
                )
        return await cls._apply_claims_and_cleanup(
            request=request,
            db=db,
            redis=redis,
            claims=claims,
            site=site,
            fence_ownerships=None,
        )

    @classmethod
    async def _apply_claims_and_cleanup(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        redis,
        claims: ControlUserAccessClaims,
        site,
        fence_ownerships,
    ) -> ControlUserAccessSyncOut:
        if fence_ownerships is not None:
            await UserSessionRegistry.ensure_ownerships(fence_ownerships)
        result = await cls._sync_claims(
            request=request,
            db=db,
            claims=claims,
            site=site,
        )
        should_revoke = result.status == "inactive" and result.local_user_id is not None and (result.disposition == "applied" or result.session_cleanup_pending)
        tenant_id = None
        if should_revoke:
            tenant_id = await cls._tenant_id_for_claims(
                db=db,
                site_id=site.id,
                issuer=claims.issuer,
                central_tenant_uuid=claims.central_tenant_uuid,
            )
        # The sync endpoint owns this explicit transaction boundary. Inactive
        # becomes durable before Redis cleanup; active and no-op results retain
        # the same atomic entitlement/receipt commit semantics.
        if fence_ownerships is not None:
            await UserSessionRegistry.ensure_ownerships(fence_ownerships)
        await db.commit()
        if not should_revoke:
            return result

        try:
            scan_complete = await UserSessionRegistry.index_user_historical_sessions(
                redis, site.id, int(tenant_id), result.local_user_id, fence_ownerships=fence_ownerships or [],
            )
            await UserSessionRegistry.revoke_user(
                redis,
                site.id,
                int(tenant_id),
                result.local_user_id,
                _fence_ownerships=fence_ownerships,
            )
            if scan_complete:
                entitlement = (await db.scalars(select(FederatedAccessEntitlementModel).where(
                    FederatedAccessEntitlementModel.site_id == site.id,
                    FederatedAccessEntitlementModel.tenant_id == tenant_id,
                    FederatedAccessEntitlementModel.issuer == claims.issuer,
                    FederatedAccessEntitlementModel.central_user_uuid == claims.central_user_uuid,
                    FederatedAccessEntitlementModel.status == "inactive",
                    FederatedAccessEntitlementModel.applied_version == claims.sync_version,
                    FederatedAccessEntitlementModel.last_event_id == claims.event_id,
                ).with_for_update())).one_or_none()
                if entitlement is not None:
                    entitlement.session_cleanup_pending = False
                    receipt = await cls._lock_event(db, claims.event_id)
                    if receipt is not None and receipt.entitlement_id == entitlement.id and receipt.sync_version == claims.sync_version:
                        receipt.result_json = {**receipt.result_json, "session_cleanup_pending": False}
                    if fence_ownerships is not None:
                        await UserSessionRegistry.ensure_ownerships(fence_ownerships)
                    await db.commit()
                    result = result.model_copy(update={"session_cleanup_pending": False})
        except Exception:
            await db.rollback()
            from app.core.logger import logger

            logger.exception(
                "联邦用户访问资格已停用，但会话清理失败: user_id={}",
                result.local_user_id,
            )
        return result

    @classmethod
    async def _sync_claims(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        claims: ControlUserAccessClaims,
        site=None,
    ) -> ControlUserAccessSyncOut:
        site = site or await resolve_request_site(db, request)
        normalized_issuer = claims.issuer.rstrip("/")
        expected_issuer = settings.CONTROL_SSO_ISSUER.rstrip("/")
        if not expected_issuer or normalized_issuer != expected_issuer:
            raise CustomException(msg="中控签发方不匹配", status_code=401)
        claims.issuer = normalized_issuer
        normalized_site_code = claims.site_code.strip().lower()
        if normalized_site_code != site.code:
            raise CustomException(msg="同步声明站点与当前访问站点不一致", status_code=403)
        # 先写回已验证的规范值，再计算指纹，避免等价站点代码破坏重放。
        claims.site_code = site.code
        if not get_assembly().application_code or claims.application_code != get_assembly().application_code:
            raise CustomException(msg="同步声明应用与当前产品不一致", status_code=403)
        if claims.desired_state == "active" and claims.user_status != 0:
            raise CustomException(msg="中控用户状态不允许开通产品访问", status_code=409)

        mapping, tenant = await cls._resolve_tenant_mapping(
            db=db,
            site_id=site.id,
            issuer=normalized_issuer,
            claims=claims,
        )
        fingerprint = cls._request_fingerprint(claims)

        # Task 2 的统一写协议要求先锁租户、再锁已存在用户。
        # entitlement 行锁放在租户锁内，不会引入 user -> tenant 的锁序反转。
        async with lock_tenant_role_assignment(db, tenant.id):
            entitlement = await cls._lock_or_create_entitlement(
                db=db,
                site_id=site.id,
                tenant_id=tenant.id,
                issuer=normalized_issuer,
                central_user_uuid=claims.central_user_uuid,
            )
            receipt = await cls._lock_event(db, claims.event_id)
            if receipt is not None:
                if receipt.entitlement_id != entitlement.id or receipt.request_fingerprint != fingerprint:
                    raise CustomException(msg="同步事件与已保存请求不一致", status_code=409)
                return cls._replayed_result(receipt.result_json)

            if claims.sync_version < entitlement.applied_version:
                result = await cls._build_result(
                    db=db,
                    entitlement=entitlement,
                    disposition="superseded",
                )
                return await cls._save_receipt(
                    db=db,
                    entitlement=entitlement,
                    claims=claims,
                    fingerprint=fingerprint,
                    result=result,
                )
            if claims.sync_version == entitlement.applied_version:
                raise CustomException(msg="同步版本已由不同事件占用", status_code=409)

            local_user_id = entitlement.local_user_id
            if claims.desired_state == "active":
                # declared 策略必须先校验默认权限；manual 允许待授权身份。
                if DefaultUserRoleService.is_applicable():
                    await DefaultUserRoleService(db)._ensure_locked(tenant.id)
                identity = await cls._find_identity(
                    db=db,
                    site_id=site.id,
                    issuer=normalized_issuer,
                    central_user_uuid=claims.central_user_uuid,
                )
                user_ids = [] if identity is None else [identity.local_user_id]
                async with lock_tenant_membership_users(db, user_ids):
                    user = await FederatedIdentityService.upsert_user_and_membership(
                        db=db,
                        site_id=site.id,
                        issuer=normalized_issuer,
                        tenant_id=tenant.id,
                        profile=FederatedUserProfile(
                            central_user_uuid=claims.central_user_uuid,
                            name=claims.name,
                            mobile=claims.mobile,
                            email=claims.email,
                            avatar=claims.avatar,
                            status=claims.user_status,
                        ),
                        membership_role="member",
                    )
                    await DefaultUserRoleService(db).bind_if_user_has_no_active_role(tenant.id, user.id)
                    local_user_id = user.id

            entitlement.local_user_id = local_user_id
            entitlement.status = claims.desired_state
            entitlement.session_cleanup_pending = claims.desired_state == "inactive"
            entitlement.applied_version = claims.sync_version
            entitlement.last_event_id = claims.event_id
            entitlement.last_synced_at = datetime.now(UTC)
            await db.flush()
            result = await cls._build_result(
                db=db,
                entitlement=entitlement,
                disposition="applied",
            )
            return await cls._save_receipt(
                db=db,
                entitlement=entitlement,
                claims=claims,
                fingerprint=fingerprint,
                result=result,
            )

    @staticmethod
    async def _tenant_id_for_claims(
        *,
        db: AsyncSession,
        site_id: int,
        issuer: str,
        central_tenant_uuid: str,
    ) -> int:
        tenant_id = (
            await db.execute(
                select(FederatedTenantModel.local_tenant_id).where(
                    FederatedTenantModel.site_id == site_id,
                    FederatedTenantModel.issuer == issuer,
                    FederatedTenantModel.central_tenant_uuid == central_tenant_uuid,
                )
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise CustomException(msg="中控租户映射不存在", status_code=409)
        return int(tenant_id)

    @classmethod
    async def _exchange_code(
        cls,
        code: str,
        site_code: str,
    ) -> ControlUserAccessClaims:
        try:
            issuer = validate_control_issuer_url(
                settings.CONTROL_SSO_ISSUER,
                settings.ENVIRONMENT,
            ).rstrip("/")
        except ValueError as exc:
            raise CustomException(msg=str(exc), status_code=503) from None
        try:
            credentials = settings.control_client_for_site(site_code)
        except ValueError as exc:
            raise CustomException(msg="当前站点未配置中控客户端", status_code=503) from exc
        client_secret = credentials.client_secret.get_secret_value()
        if not credentials.client_id.strip() or not client_secret.strip():
            raise CustomException(msg="当前站点中控客户端凭据无效", status_code=503)
        timeout_seconds = settings.CONTROL_SSO_TIMEOUT_SECONDS
        timeout = httpx.Timeout(
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                transport=cls.transport,
            ) as client:
                response = await client.post(
                    f"{issuer}/control/user-entitlements/exchange",
                    json={"code": code},
                    auth=httpx.BasicAuth(
                        credentials.client_id,
                        client_secret,
                    ),
                )
                response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            claims = ControlUserAccessClaims.model_validate(payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in cls._retryable_exchange_statuses:
                raise CustomException(msg="中控用户授权码兑换暂时不可用", status_code=503) from exc
            raise CustomException(msg="中控用户授权码兑换失败", status_code=401) from exc
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise CustomException(msg="中控用户授权码兑换暂时不可用", status_code=503) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CustomException(msg="中控用户授权码兑换失败", status_code=401) from exc

        claims.issuer = claims.issuer.rstrip("/")
        if not issuer or claims.issuer != issuer:
            raise CustomException(msg="中控签发方不匹配", status_code=401)
        return claims

    @staticmethod
    async def _resolve_tenant_mapping(
        *,
        db: AsyncSession,
        site_id: int,
        issuer: str,
        claims: ControlUserAccessClaims,
    ) -> tuple[FederatedTenantModel, TenantModel]:
        mapping = (
            await db.execute(
                select(FederatedTenantModel).where(
                    FederatedTenantModel.site_id == site_id,
                    FederatedTenantModel.issuer == issuer,
                    FederatedTenantModel.central_tenant_uuid == claims.central_tenant_uuid,
                )
            )
        ).scalar_one_or_none()
        if mapping is None:
            raise CustomException(msg="中控租户映射不存在", status_code=409)
        tenant = await db.get(TenantModel, mapping.local_tenant_id)
        drifted = (
            tenant is None
            or tenant.site_id != site_id
            or tenant.code != claims.target_tenant_code
            or tenant.status != 0
            or tenant.is_deleted
            or mapping.central_tenant_code != claims.central_tenant_code
        )
        if drifted:
            raise CustomException(msg="中控租户映射与目标租户不一致", status_code=409)
        return mapping, tenant

    @staticmethod
    async def _lock_or_create_entitlement(
        *,
        db: AsyncSession,
        site_id: int,
        tenant_id: int,
        issuer: str,
        central_user_uuid: str,
    ) -> FederatedAccessEntitlementModel:
        entitlement = (
            await db.execute(
                select(FederatedAccessEntitlementModel)
                .where(
                    FederatedAccessEntitlementModel.issuer == issuer,
                    FederatedAccessEntitlementModel.central_user_uuid == central_user_uuid,
                    FederatedAccessEntitlementModel.tenant_id == tenant_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if entitlement is None:
            entitlement = FederatedAccessEntitlementModel(
                site_id=site_id,
                tenant_id=tenant_id,
                local_user_id=None,
                issuer=issuer,
                central_user_uuid=central_user_uuid,
                status="inactive",
                applied_version=0,
                session_cleanup_pending=False,
            )
            db.add(entitlement)
            await db.flush()
        elif entitlement.site_id != site_id:
            raise CustomException(msg="访问资格所属站点与租户映射不一致", status_code=409)
        return entitlement

    @staticmethod
    async def _lock_event(
        db: AsyncSession,
        event_id: str,
    ) -> FederatedAccessEventModel | None:
        return (await db.execute(select(FederatedAccessEventModel).where(FederatedAccessEventModel.event_id == event_id).with_for_update())).scalar_one_or_none()

    @staticmethod
    async def _find_identity(
        *,
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
    async def _build_result(
        *,
        db: AsyncSession,
        entitlement: FederatedAccessEntitlementModel,
        disposition: str,
    ) -> ControlUserAccessSyncOut:
        role_codes: list[str] = []
        effective_menu_count = 0
        if entitlement.local_user_id is not None:
            role_codes = list(
                (
                    await db.execute(
                        select(RoleModel.code)
                        .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                        .where(
                            UserRolesModel.user_id == entitlement.local_user_id,
                            RoleModel.tenant_id == entitlement.tenant_id,
                            RoleModel.status == 0,
                            RoleModel.is_deleted.is_(False),
                        )
                        .order_by(RoleModel.code)
                    )
                )
                .scalars()
                .all()
            )
            effective_menu_count = (
                await db.execute(
                    select(func.count(func.distinct(RoleMenusModel.menu_id)))
                    .select_from(RoleMenusModel)
                    .join(RoleModel, RoleModel.id == RoleMenusModel.role_id)
                    .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                    .join(MenuModel, MenuModel.id == RoleMenusModel.menu_id)
                    .where(
                        UserRolesModel.user_id == entitlement.local_user_id,
                        RoleModel.tenant_id == entitlement.tenant_id,
                        RoleModel.status == 0,
                        RoleModel.is_deleted.is_(False),
                        MenuModel.status == 0,
                        MenuModel.is_deleted.is_(False),
                    )
                )
            ).scalar_one()
        return ControlUserAccessSyncOut(
            disposition=disposition,
            status=entitlement.status,
            applied_version=entitlement.applied_version,
            local_user_id=entitlement.local_user_id,
            role_codes=role_codes,
            effective_menu_count=effective_menu_count,
            session_cleanup_pending=entitlement.session_cleanup_pending,
        )

    @staticmethod
    async def _save_receipt(
        *,
        db: AsyncSession,
        entitlement: FederatedAccessEntitlementModel,
        claims: ControlUserAccessClaims,
        fingerprint: str,
        result: ControlUserAccessSyncOut,
    ) -> ControlUserAccessSyncOut:
        try:
            async with db.begin_nested():
                db.add(
                    FederatedAccessEventModel(
                        event_id=claims.event_id,
                        entitlement_id=entitlement.id,
                        sync_version=claims.sync_version,
                        desired_state=claims.desired_state,
                        request_fingerprint=fingerprint,
                        result_json=result.model_dump(mode="json"),
                    )
                )
                await db.flush()
            return result
        except IntegrityError:
            # 跨租户的全局 event_id 竞态不受单租户锁覆盖；
            # 唯一键失败后在 savepoint 外安全重读，收敛为重放或 409。
            receipt = await ControlUserAccessSyncService._lock_event(db, claims.event_id)
            if receipt is None:
                raise CustomException(msg="同步事件并发冲突", status_code=409) from None
            if receipt.entitlement_id != entitlement.id or receipt.request_fingerprint != fingerprint:
                raise CustomException(msg="同步事件与已保存请求不一致", status_code=409) from None
            return ControlUserAccessSyncService._replayed_result(receipt.result_json)

    @staticmethod
    def _replayed_result(result_json: dict) -> ControlUserAccessSyncOut:
        return ControlUserAccessSyncOut.model_validate({**result_json, "disposition": "replayed"})

    @staticmethod
    def _request_fingerprint(claims: ControlUserAccessClaims) -> str:
        canonical = json.dumps(
            claims.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()
