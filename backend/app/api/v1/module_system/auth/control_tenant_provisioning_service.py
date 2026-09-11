import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import ClassVar

import anyio
import httpx
from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.federated_tenant.model import FederatedTenantModel
from app.api.v1.module_platform.package.model import PackageModel
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_platform.tenant.credit_code import validate_unified_social_credit_code
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_platform.tenant.schema import TenantCreateSchema
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.federated_access.model import FederatedAccessEventModel
from app.api.v1.module_system.federated_access.service import (
    ControlUserAccessSyncService,
)
from app.api.v1.module_system.federated_access.tenant_role_lock import (
    lock_tenant_membership_users,
    lock_tenant_role_assignment,
)
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .control_sso_schema import ControlTenantProvisionClaims, ControlTenantProvisionOut
from .federated_identity_service import FederatedIdentityService, FederatedUserProfile
from .service import resolve_request_site


class ControlTenantProvisioningService:
    """使用中控一次性开户码，在目标系统事务内装配租户和联邦 owner。"""

    transport: ClassVar[httpx.AsyncBaseTransport | None] = None
    _provision_lock: ClassVar[threading.Lock] = threading.Lock()
    _retryable_exchange_statuses: ClassVar[set[int]] = {429, 502, 503, 504}

    @classmethod
    async def provision(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        code: str,
    ) -> ControlTenantProvisionOut:
        site = await resolve_request_site(db, request)
        claims = await cls._exchange_code(code, site.code)
        await anyio.to_thread.run_sync(cls._provision_lock.acquire)
        try:
            return await cls._provision_claims(request=request, db=db, claims=claims, site=site)
        finally:
            cls._provision_lock.release()

    @classmethod
    async def _provision_claims(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        claims: ControlTenantProvisionClaims,
        site=None,
    ) -> ControlTenantProvisionOut:
        site = site or await resolve_request_site(db, request)
        if claims.site_code != site.code:
            raise CustomException(msg="开户声明站点与当前访问站点不一致", status_code=403)

        package = (
            await db.execute(
                select(PackageModel).where(
                    PackageModel.site_id == site.id,
                    PackageModel.code == claims.target_package_code,
                    PackageModel.status == 0,
                    PackageModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if package is None:
            raise CustomException(msg="目标套餐不存在或已停用", status_code=400)

        existing = await cls._find_mapping(db, site.id, claims)
        if existing is not None:
            return await cls._existing_result(db, existing, claims, package)

        await cls._raise_explicit_tenant_conflict(db, site.id, claims)
        auth = AuthSchema(db=db, check_data_scope=False)
        tenant_service = TenantService(auth)
        try:
            async with db.begin_nested():
                tenant = await tenant_service.create_tenant_record(
                    TenantCreateSchema(
                        name=claims.tenant_name,
                        code=claims.target_tenant_code,
                        site_id=site.id,
                        package_id=package.id,
                        unified_social_credit_code=claims.unified_social_credit_code,
                        contact_name=claims.contact_name,
                        contact_phone=claims.contact_phone,
                        contact_email=claims.contact_email,
                        address=claims.address,
                    ),
                    preserve_integrity_error=True,
                )
                owner = await FederatedIdentityService.upsert_user_and_membership(
                    db=db,
                    site_id=site.id,
                    issuer=claims.issuer,
                    tenant_id=tenant.id,
                    profile=FederatedUserProfile(
                        central_user_uuid=claims.owner.central_user_uuid,
                        name=claims.owner.name,
                        mobile=claims.owner.mobile,
                        email=claims.owner.email,
                        avatar=claims.owner.avatar,
                        status=claims.owner.status,
                    ),
                    membership_role="owner",
                )
                await TenantService.ensure_tenant_owner(db, tenant.id, owner.id)
                await PackageService.sync_tenant_plugins(db, tenant.id, package.id)
                mapping = FederatedTenantModel(
                    site_id=site.id,
                    issuer=claims.issuer,
                    central_tenant_uuid=claims.central_tenant_uuid,
                    central_tenant_code=claims.central_tenant_code,
                    local_tenant_id=tenant.id,
                    provision_request_uuid=claims.provision_request_uuid,
                    target_package_code=claims.target_package_code,
                    owner_central_user_uuid=claims.owner.central_user_uuid,
                )
                db.add(mapping)
                await db.flush()
                await cls._ensure_owner_entitlement(
                    db=db,
                    mapping=mapping,
                    claims=claims,
                )
        except IntegrityError:
            mapping = await cls._find_mapping(db, site.id, claims)
            if mapping is not None:
                return await cls._existing_result(db, mapping, claims, package)
            await cls._raise_explicit_tenant_conflict(db, site.id, claims)
            raise CustomException(msg="目标租户开户并发冲突", status_code=409) from None

        return cls._result("created", tenant)

    @classmethod
    async def _exchange_code(cls, code: str, site_code: str) -> ControlTenantProvisionClaims:
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
                    f"{issuer}/control/provisioning/exchange",
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
            claims = ControlTenantProvisionClaims.model_validate(payload)
            claims.unified_social_credit_code = validate_unified_social_credit_code(
                claims.unified_social_credit_code
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in cls._retryable_exchange_statuses:
                raise CustomException(msg="中控租户开户码兑换暂时不可用", status_code=503) from exc
            raise CustomException(msg="中控租户开户码兑换失败", status_code=401) from exc
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise CustomException(msg="中控租户开户码兑换暂时不可用", status_code=503) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CustomException(msg="中控租户开户码兑换失败", status_code=401) from exc

        claims.issuer = claims.issuer.rstrip("/")
        if claims.issuer != issuer:
            raise CustomException(msg="中控签发方不匹配", status_code=401)
        return claims

    @staticmethod
    async def _find_mapping(
        db: AsyncSession,
        site_id: int,
        claims: ControlTenantProvisionClaims,
    ) -> FederatedTenantModel | None:
        mappings = (
            await db.execute(
                select(FederatedTenantModel).where(
                    or_(
                        (
                            (FederatedTenantModel.site_id == site_id)
                            & (FederatedTenantModel.issuer == claims.issuer)
                            & (
                                FederatedTenantModel.central_tenant_uuid
                                == claims.central_tenant_uuid
                            )
                        ),
                        FederatedTenantModel.provision_request_uuid
                        == claims.provision_request_uuid,
                    )
                )
            )
        ).scalars().all()
        if not mappings:
            return None
        if len({mapping.id for mapping in mappings}) != 1:
            raise CustomException(msg="中控租户开户幂等映射漂移", status_code=409)
        return mappings[0]

    @classmethod
    async def _existing_result(
        cls,
        db: AsyncSession,
        mapping: FederatedTenantModel,
        claims: ControlTenantProvisionClaims,
        package: PackageModel,
    ) -> ControlTenantProvisionOut:
        tenant = await db.get(TenantModel, mapping.local_tenant_id)
        if tenant is None:
            raise CustomException(msg="中控租户开户映射对应的本地租户不存在", status_code=409)
        drifted = (
            mapping.site_id != tenant.site_id
            or mapping.issuer != claims.issuer
            or mapping.central_tenant_uuid != claims.central_tenant_uuid
            or mapping.provision_request_uuid != claims.provision_request_uuid
            or mapping.target_package_code != claims.target_package_code
            or mapping.owner_central_user_uuid != claims.owner.central_user_uuid
            or tenant.code != claims.target_tenant_code
            or tenant.package_id != package.id
            or tenant.unified_social_credit_code != claims.unified_social_credit_code
        )
        if drifted:
            raise CustomException(msg="中控租户开户请求与目标数据发生漂移", status_code=409)
        if mapping.central_tenant_code != claims.central_tenant_code:
            mapping.central_tenant_code = claims.central_tenant_code
            await db.flush()
        await cls._ensure_owner_entitlement(
            db=db,
            mapping=mapping,
            claims=claims,
        )
        return cls._result("already_exists", tenant)

    @classmethod
    async def _ensure_owner_entitlement(
        cls,
        *,
        db: AsyncSession,
        mapping: FederatedTenantModel,
        claims: ControlTenantProvisionClaims,
    ) -> None:
        """为开户 owner 补建 v1 访问资格，不改动 owner/admin 治理角色。"""
        if (
            mapping.issuer.rstrip("/") != claims.issuer.rstrip("/")
            or mapping.central_tenant_uuid != claims.central_tenant_uuid
            or mapping.owner_central_user_uuid != claims.owner.central_user_uuid
            or mapping.provision_request_uuid != claims.provision_request_uuid
        ):
            raise CustomException(
                msg="中控租户开户 owner 映射不一致",
                status_code=409,
            )

        issuer = claims.issuer.rstrip("/")
        async with lock_tenant_role_assignment(db, mapping.local_tenant_id):
            entitlement = (
                await ControlUserAccessSyncService._lock_or_create_entitlement(
                    db=db,
                    site_id=mapping.site_id,
                    tenant_id=mapping.local_tenant_id,
                    issuer=issuer,
                    central_user_uuid=claims.owner.central_user_uuid,
                )
            )
            # 已有用户授权治理版本时，开户幂等路径不反向覆盖。
            if entitlement.applied_version >= 1:
                return

            identity = await ControlUserAccessSyncService._find_identity(
                db=db,
                site_id=mapping.site_id,
                issuer=issuer,
                central_user_uuid=claims.owner.central_user_uuid,
            )
            if identity is None:
                raise CustomException(
                    msg="中控租户开户 owner 本地身份不存在",
                    status_code=409,
                )
            async with lock_tenant_membership_users(db, [identity.local_user_id]):
                entitlement.local_user_id = identity.local_user_id
                entitlement.status = "active"
                entitlement.applied_version = 1
                entitlement.last_event_id = claims.provision_request_uuid
                entitlement.last_synced_at = datetime.now(UTC)
                await db.flush()

                fingerprint_payload = {
                    "issuer": issuer,
                    "site_id": mapping.site_id,
                    "central_tenant_uuid": mapping.central_tenant_uuid,
                    "tenant_id": mapping.local_tenant_id,
                    "central_user_uuid": mapping.owner_central_user_uuid,
                    "event_id": mapping.provision_request_uuid,
                    "sync_version": 1,
                    "desired_state": "active",
                }
                fingerprint = hashlib.sha256(
                    json.dumps(
                        fingerprint_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                receipt = await ControlUserAccessSyncService._lock_event(
                    db,
                    claims.provision_request_uuid,
                )
                if receipt is not None:
                    if (
                        receipt.entitlement_id != entitlement.id
                        or receipt.request_fingerprint != fingerprint
                    ):
                        raise CustomException(
                            msg="中控租户开户 owner 事件冲突",
                            status_code=409,
                        )
                    return
                result = await ControlUserAccessSyncService._build_result(
                    db=db,
                    entitlement=entitlement,
                    disposition="applied",
                )
                db.add(
                    FederatedAccessEventModel(
                        event_id=claims.provision_request_uuid,
                        entitlement_id=entitlement.id,
                        sync_version=1,
                        desired_state="active",
                        request_fingerprint=fingerprint,
                        result_json=result.model_dump(mode="json"),
                    )
                )
                await db.flush()

    @staticmethod
    async def _raise_explicit_tenant_conflict(
        db: AsyncSession,
        site_id: int,
        claims: ControlTenantProvisionClaims,
    ) -> None:
        code_tenant = (
            await db.execute(
                select(TenantModel.id).where(
                    TenantModel.code == claims.target_tenant_code,
                    TenantModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if code_tenant is not None:
            raise CustomException(msg="目标租户编码已存在", status_code=409)
        if claims.unified_social_credit_code is None:
            return
        credit_tenant = (
            await db.execute(
                select(TenantModel.id).where(
                    TenantModel.site_id == site_id,
                    TenantModel.unified_social_credit_code
                    == claims.unified_social_credit_code,
                    TenantModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if credit_tenant is not None:
            raise CustomException(msg="统一社会信用代码在目标站点已存在", status_code=409)

    @staticmethod
    def _result(result: str, tenant: TenantModel) -> ControlTenantProvisionOut:
        return ControlTenantProvisionOut(
            result=result,
            target_tenant_id=tenant.id,
            target_tenant_uuid=tenant.uuid,
            target_tenant_code=tenant.code,
        )
