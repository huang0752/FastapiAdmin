"""One-time tenant-provision ticket issuance and claim exchange."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.model import UserModel
from app.core.exceptions import CustomException

from ..application_package.model import ControlApplicationPackageModel
from ..model import ControlApplicationModel
from ..service import authenticate_control_client
from .model import ControlTenantProvisionModel, ControlTenantProvisionTicketModel
from .schema import ControlProvisionExchangeClaimsSchema, ControlProvisionOwnerClaimSchema


class ControlProvisionTicketService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def issue(self, provision_id: int, *, ttl_seconds: int = 60) -> str:
        if ttl_seconds < 1:
            raise ValueError("开户票据有效期必须大于零")
        provision = await self.db.get(ControlTenantProvisionModel, provision_id)
        if provision is None or provision.is_deleted:
            raise CustomException(msg="开通记录不存在", status_code=status.HTTP_404_NOT_FOUND)
        plain_code = secrets.token_urlsafe(32)
        issued_at = datetime.now(UTC)
        self.db.add(
            ControlTenantProvisionTicketModel(
                code_hash=hashlib.sha256(plain_code.encode()).hexdigest(),
                provision_id=provision.id,
                status="issued",
                issued_at=issued_at,
                expires_at=issued_at + timedelta(seconds=ttl_seconds),
            )
        )
        await self.db.flush()
        return plain_code

    async def exchange(
        self,
        *,
        client_id: str,
        client_secret: str,
        plain_code: str,
        issuer: str,
    ) -> ControlProvisionExchangeClaimsSchema:
        application = await authenticate_control_client(self.db, client_id=client_id, client_secret=client_secret)
        now = datetime.now(UTC)
        code_hash = hashlib.sha256(plain_code.encode()).hexdigest()
        provision_for_application = select(ControlTenantProvisionModel.id).where(
            ControlTenantProvisionModel.id == ControlTenantProvisionTicketModel.provision_id,
            ControlTenantProvisionModel.application_id == application.id,
            ControlTenantProvisionModel.site_id == application.site_id,
        )
        provision_id = (
            await self.db.execute(
                update(ControlTenantProvisionTicketModel)
                .where(
                    ControlTenantProvisionTicketModel.code_hash == code_hash,
                    ControlTenantProvisionTicketModel.status == "issued",
                    ControlTenantProvisionTicketModel.expires_at > now,
                    provision_for_application.exists(),
                )
                .values(status="redeemed", redeemed_at=now)
                .returning(ControlTenantProvisionTicketModel.provision_id)
            )
        ).scalar_one_or_none()
        if provision_id is None:
            raise CustomException(msg="开户码无效、已过期或已使用", status_code=status.HTTP_401_UNAUTHORIZED)

        state = (
            await self.db.execute(
                select(
                    ControlTenantProvisionModel,
                    ControlApplicationModel,
                    ControlApplicationPackageModel,
                    TenantModel,
                    UserModel,
                    SiteModel,
                )
                .join(
                    ControlApplicationModel,
                    and_(
                        ControlApplicationModel.id == ControlTenantProvisionModel.application_id,
                        ControlApplicationModel.site_id == ControlTenantProvisionModel.site_id,
                        ControlApplicationModel.status == 0,
                        ControlApplicationModel.provisioning_enabled.is_(True),
                        ControlApplicationModel.is_deleted.is_(False),
                    ),
                )
                .join(
                    ControlApplicationPackageModel,
                    and_(
                        ControlApplicationPackageModel.id == ControlTenantProvisionModel.application_package_id,
                        ControlApplicationPackageModel.application_id == ControlTenantProvisionModel.application_id,
                        ControlApplicationPackageModel.site_id == ControlTenantProvisionModel.site_id,
                        ControlApplicationPackageModel.status == 0,
                        ControlApplicationPackageModel.is_deleted.is_(False),
                    ),
                )
                .join(
                    TenantModel,
                    and_(
                        TenantModel.id == ControlTenantProvisionModel.tenant_id,
                        TenantModel.site_id == ControlTenantProvisionModel.site_id,
                        TenantModel.status == 0,
                        TenantModel.is_deleted.is_(False),
                    ),
                )
                .join(
                    UserModel,
                    and_(
                        UserModel.id == ControlTenantProvisionModel.owner_user_id,
                        UserModel.status == 0,
                        UserModel.is_deleted.is_(False),
                    ),
                )
                .join(
                    TenantUserModel,
                    and_(
                        TenantUserModel.user_id == UserModel.id,
                        TenantUserModel.tenant_id == TenantModel.id,
                        TenantUserModel.role == "owner",
                    ),
                )
                .join(
                    SiteModel,
                    and_(
                        SiteModel.id == ControlTenantProvisionModel.site_id,
                        SiteModel.status == 0,
                        SiteModel.is_deleted.is_(False),
                    ),
                )
                .where(
                    ControlTenantProvisionModel.id == provision_id,
                    ControlTenantProvisionModel.application_id == application.id,
                    ControlTenantProvisionModel.site_id == application.site_id,
                    ControlTenantProvisionModel.status.in_(("pending", "processing")),
                    ControlTenantProvisionModel.is_deleted.is_(False),
                )
            )
        ).one_or_none()
        if state is None:
            raise CustomException(msg="应用、租户、管理员或套餐状态已变更", code=10403, status_code=status.HTTP_403_FORBIDDEN)

        provision, _application, package, tenant, owner, site = state
        return ControlProvisionExchangeClaimsSchema(
            provision_request_uuid=provision.provision_request_uuid,
            central_tenant_uuid=tenant.uuid,
            central_tenant_code=tenant.code,
            tenant_name=tenant.name,
            unified_social_credit_code=tenant.unified_social_credit_code,
            contact_name=tenant.contact_name,
            contact_phone=tenant.contact_phone,
            contact_email=tenant.contact_email,
            address=tenant.address,
            site_code=site.code,
            target_tenant_code=provision.desired_target_tenant_code,
            target_package_code=package.target_package_code,
            owner=ControlProvisionOwnerClaimSchema(
                central_user_uuid=owner.uuid,
                username=owner.username,
                name=owner.name,
                mobile=owner.mobile,
                email=owner.email,
                avatar=owner.avatar,
                status=owner.status,
            ),
            issuer=issuer,
        )
