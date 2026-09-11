from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class FederatedAccessEntitlementModel(MappedBase):
    """Product-local access state for one central user and tenant."""

    __tablename__ = "sys_federated_access_entitlement"
    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "central_user_uuid",
            "tenant_id",
            name="uq_federated_access_entitlement_subject_tenant",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_federated_access_entitlement_status",
        ),
        CheckConstraint(
            "applied_version >= 0",
            name="ck_federated_access_entitlement_version",
        ),
        {"comment": "中控用户在产品租户中的联邦访问资格"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("platform_site.id"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("platform_tenant.id"),
        nullable=False,
        index=True,
    )
    local_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("sys_user.id"),
        nullable=True,
        index=True,
    )
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    central_user_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        default="inactive",
        server_default="inactive",
        nullable=False,
        index=True,
    )
    applied_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    last_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    session_cleanup_pending: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )


class FederatedAccessEventModel(MappedBase):
    """Immutable receipt used to replay the original result of a sync event."""

    __tablename__ = "sys_federated_access_event"
    __table_args__ = (
        CheckConstraint(
            "sync_version > 0",
            name="ck_federated_access_event_version",
        ),
        CheckConstraint(
            "desired_state IN ('active', 'inactive')",
            name="ck_federated_access_event_desired_state",
        ),
        {"comment": "联邦访问资格同步事件回执"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    entitlement_id: Mapped[int] = mapped_column(
        ForeignKey("sys_federated_access_entitlement.id"),
        nullable=False,
        index=True,
    )
    sync_version: Mapped[int] = mapped_column(Integer, nullable=False)
    desired_state: Mapped[str] = mapped_column(String(16), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
