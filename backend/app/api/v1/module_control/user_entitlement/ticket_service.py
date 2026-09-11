"""Issue and atomically redeem Control user-entitlement tickets."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.model import UserModel
from app.core.exceptions import CustomException

from ..model import (
    ControlApplicationModel,
    ControlTenantApplicationModel,
    ControlUserApplicationGrantModel,
)
from ..service import authenticate_control_client
from .model import ControlUserEntitlementTicketModel
from .schema import ControlUserAccessClaims


class ControlUserEntitlementTicketService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def issue(
        self,
        grant_id: int,
        *,
        event_id: str,
        sync_version: int,
        ttl_seconds: int = 60,
    ) -> str:
        if ttl_seconds < 1:
            raise ValueError("用户授权票据有效期必须大于零")
        grant_state = (
            await self.db.execute(
                select(
                    ControlUserApplicationGrantModel,
                    ControlTenantApplicationModel.application_id,
                )
                .join(
                    ControlTenantApplicationModel,
                    and_(
                        ControlTenantApplicationModel.id
                        == ControlUserApplicationGrantModel.tenant_application_id,
                        ControlTenantApplicationModel.site_id
                        == ControlUserApplicationGrantModel.site_id,
                    ),
                )
                .where(
                    ControlUserApplicationGrantModel.id == grant_id,
                    ControlUserApplicationGrantModel.last_event_id == event_id,
                    ControlUserApplicationGrantModel.sync_version == sync_version,
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
            )
        ).one_or_none()
        if grant_state is None:
            raise CustomException(msg="用户授权同步代际已变化", status_code=status.HTTP_409_CONFLICT)

        grant, application_id = grant_state
        plain_code = secrets.token_urlsafe(48)
        issued_at = datetime.now(UTC)
        self.db.add(
            ControlUserEntitlementTicketModel(
                code_hash=hashlib.sha256(plain_code.encode()).hexdigest(),
                grant_id=grant.id,
                tenant_application_id=grant.tenant_application_id,
                application_id=application_id,
                site_id=grant.site_id,
                event_id=event_id,
                sync_version=sync_version,
                desired_state=grant.desired_state,
                status="issued",
                issued_at=issued_at,
                expires_at=issued_at + timedelta(seconds=ttl_seconds),
            )
        )
        await self.db.flush()
        return plain_code

    @staticmethod
    async def exchange(
        db: AsyncSession,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        issuer: str,
    ) -> ControlUserAccessClaims:
        client_application = await authenticate_control_client(
            db,
            client_id=client_id,
            client_secret=client_secret,
        )
        now = datetime.now(UTC)
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        ticket = ControlUserEntitlementTicketModel
        grant = ControlUserApplicationGrantModel
        opening = ControlTenantApplicationModel
        application = ControlApplicationModel

        revocation = ticket.desired_state == "inactive"
        current_grant = select(grant.id).where(
            grant.id == ticket.grant_id,
            grant.tenant_application_id == ticket.tenant_application_id,
            grant.site_id == ticket.site_id,
            grant.last_event_id == ticket.event_id,
            grant.sync_version == ticket.sync_version,
            grant.desired_state == ticket.desired_state,
            grant.is_deleted.is_(False),
        )
        current_opening = select(opening.id).where(
            opening.id == ticket.tenant_application_id,
            opening.application_id == ticket.application_id,
            opening.site_id == ticket.site_id,
            or_(revocation, opening.status == 0),
            or_(revocation, opening.is_deleted.is_(False)),
        )
        ticket_id = (
            await db.execute(
                update(ticket)
                .where(
                    ticket.code_hash == code_hash,
                    ticket.status == "issued",
                    ticket.expires_at > now,
                    ticket.application_id == client_application.id,
                    ticket.site_id == client_application.site_id,
                    application.id == ticket.application_id,
                    application.site_id == ticket.site_id,
                    or_(revocation, application.status == 0),
                    or_(revocation, application.is_deleted.is_(False)),
                    current_grant.exists(),
                    current_opening.exists(),
                )
                .values(status="redeemed", redeemed_at=now)
                .returning(ticket.id)
            )
        ).scalar_one_or_none()
        if ticket_id is None:
            raise CustomException(
                msg="用户授权码无效、已过期、已使用或同步代际已变化",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        state = (
            await db.execute(
                select(ticket, application, opening, TenantModel, SiteModel, UserModel, grant)
                .join(grant, grant.id == ticket.grant_id)
                .join(
                    opening,
                    and_(
                        opening.id == ticket.tenant_application_id,
                        opening.application_id == ticket.application_id,
                        opening.site_id == ticket.site_id,
                        opening.tenant_id == grant.tenant_id,
                    ),
                )
                .join(
                    application,
                    and_(
                        application.id == ticket.application_id,
                        application.site_id == ticket.site_id,
                    ),
                )
                .join(
                    TenantModel,
                    and_(
                        TenantModel.id == opening.tenant_id,
                        TenantModel.site_id == ticket.site_id,
                    ),
                )
                .join(SiteModel, SiteModel.id == ticket.site_id)
                .join(UserModel, UserModel.id == grant.user_id)
                .outerjoin(
                    TenantUserModel,
                    and_(
                        TenantUserModel.user_id == UserModel.id,
                        TenantUserModel.tenant_id == TenantModel.id,
                    ),
                )
                .where(
                    ticket.id == ticket_id,
                    ticket.status == "redeemed",
                    grant.last_event_id == ticket.event_id,
                    grant.sync_version == ticket.sync_version,
                    grant.desired_state == ticket.desired_state,
                    grant.is_deleted.is_(False),
                    or_(revocation, opening.status == 0),
                    or_(revocation, opening.is_deleted.is_(False)),
                    or_(revocation, application.status == 0),
                    or_(revocation, application.is_deleted.is_(False)),
                    or_(revocation, TenantModel.status == 0),
                    or_(revocation, TenantModel.is_deleted.is_(False)),
                    or_(revocation, SiteModel.status == 0),
                    or_(revocation, SiteModel.is_deleted.is_(False)),
                    or_(revocation, UserModel.is_deleted.is_(False)),
                    or_(revocation, UserModel.status == 0),
                    or_(revocation, and_(TenantUserModel.user_id.is_not(None), TenantUserModel.role.in_(("owner", "admin", "member")))),
                )
            )
        ).one_or_none()
        if state is None:
            raise CustomException(
                msg="用户授权关联状态已变化",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        redeemed_ticket, current_application, current_opening_row, tenant, site, user, _grant = state
        return ControlUserAccessClaims(
            issuer=issuer,
            event_id=redeemed_ticket.event_id,
            sync_version=redeemed_ticket.sync_version,
            desired_state=redeemed_ticket.desired_state,
            application_code=current_application.code,
            site_code=site.code,
            central_tenant_uuid=tenant.uuid,
            central_tenant_code=tenant.code,
            target_tenant_code=current_opening_row.target_tenant_code,
            central_user_uuid=user.uuid,
            name=user.name,
            mobile=user.mobile,
            email=user.email,
            avatar=user.avatar,
            user_status=user.status,
        )
