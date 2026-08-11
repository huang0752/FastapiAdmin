import threading
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
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_platform.tenant.schema import TenantCreateSchema
from app.api.v1.module_platform.tenant.service import TenantService
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

    @classmethod
    async def provision(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        code: str,
    ) -> ControlTenantProvisionOut:
        claims = await cls._exchange_code(code)
        await anyio.to_thread.run_sync(cls._provision_lock.acquire)
        try:
            return await cls._provision_claims(request=request, db=db, claims=claims)
        finally:
            cls._provision_lock.release()

    @classmethod
    async def _provision_claims(
        cls,
        *,
        request: Request,
        db: AsyncSession,
        claims: ControlTenantProvisionClaims,
    ) -> ControlTenantProvisionOut:
        site = await resolve_request_site(db, request)
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
            return await cls._existing_result(db, existing, claims)

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
        except IntegrityError:
            mapping = await cls._find_mapping(db, site.id, claims)
            if mapping is not None:
                return await cls._existing_result(db, mapping, claims)
            await cls._raise_explicit_tenant_conflict(db, site.id, claims)
            raise CustomException(msg="目标租户开户并发冲突", status_code=409) from None

        return cls._result("created", tenant)

    @classmethod
    async def _exchange_code(cls, code: str) -> ControlTenantProvisionClaims:
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
                    f"{issuer}/control/provisioning/exchange",
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
            claims = ControlTenantProvisionClaims.model_validate(payload)
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
    ) -> ControlTenantProvisionOut:
        tenant = await db.get(TenantModel, mapping.local_tenant_id)
        if tenant is None:
            raise CustomException(msg="中控租户开户映射对应的本地租户不存在", status_code=409)
        drifted = (
            mapping.site_id != tenant.site_id
            or mapping.issuer != claims.issuer
            or mapping.central_tenant_uuid != claims.central_tenant_uuid
            or mapping.central_tenant_code != claims.central_tenant_code
            or mapping.provision_request_uuid != claims.provision_request_uuid
            or mapping.target_package_code != claims.target_package_code
            or mapping.owner_central_user_uuid != claims.owner.central_user_uuid
            or tenant.code != claims.target_tenant_code
            or tenant.unified_social_credit_code != claims.unified_social_credit_code
        )
        if drifted:
            raise CustomException(msg="中控租户开户请求与目标数据发生漂移", status_code=409)
        return cls._result("already_exists", tenant)

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
