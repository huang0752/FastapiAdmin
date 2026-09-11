"""Persistence model for one-time Control user-entitlement tickets."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase, ModelMixin


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ControlUserEntitlementTicketModel(ModelMixin):
    """A hashed, short-lived, single-use user-entitlement synchronization ticket."""

    __tablename__ = "control_user_entitlement_ticket"
    __table_args__ = (
        CheckConstraint(
            "status IN ('issued', 'redeemed', 'expired')",
            name="ck_control_user_entitlement_ticket_status",
        ),
        CheckConstraint("expires_at > issued_at", name="ck_control_user_entitlement_ticket_expiry"),
        CheckConstraint("sync_version > 0", name="ck_control_user_entitlement_ticket_sync_version"),
        CheckConstraint(
            "desired_state IN ('active', 'inactive')",
            name="ck_control_user_entitlement_ticket_desired_state",
        ),
        CheckConstraint(
            "(status = 'redeemed' AND redeemed_at IS NOT NULL) OR "
            "(status IN ('issued', 'expired') AND redeemed_at IS NULL)",
            name="ck_control_user_entitlement_ticket_redemption",
        ),
        ForeignKeyConstraint(
            ["grant_id", "tenant_application_id", "site_id"],
            [
                "control_user_application_grant.id",
                "control_user_application_grant.tenant_application_id",
                "control_user_application_grant.site_id",
            ],
            name="fk_control_user_entitlement_ticket_grant_binding_site",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_application_id", "application_id", "site_id"],
            [
                "control_tenant_application.id",
                "control_tenant_application.application_id",
                "control_tenant_application.site_id",
            ],
            name="fk_control_user_entitlement_ticket_opening_app_site",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        {"comment": "中控一次性用户授权同步票据"},
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", "issued")
        kwargs.setdefault("issued_at", _utc_now())
        super().__init__(**kwargs)

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="授权码SHA-256哈希")
    grant_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment="用户应用授权ID",
    )
    tenant_application_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="租户应用ID")
    application_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment="应用ID",
    )
    site_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment="所属品牌站点ID",
    )
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="授权同步事件ID")
    sync_version: Mapped[int] = mapped_column(Integer, nullable=False, comment="授权同步版本")
    desired_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="inactive",
        server_default="inactive",
        comment="签发时授权期望状态快照",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="issued", server_default="issued", index=True, comment="票据状态"
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, comment="签发时间")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="过期时间")
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, comment="兑换时间")


class ControlUserApplicationGrantMigrationBackupModel(MappedBase):
    """Rollback journal for the legacy grant fields rewritten by revision 20260831_01."""

    __tablename__ = "control_user_application_grant_20260831_backup"
    __table_args__ = {"comment": "20260831用户授权迁移回滚备份"}

    grant_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False, comment="原授权ID")
    status: Mapped[int] = mapped_column(Integer, nullable=False, comment="原状态")
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, comment="原软删除状态")
    deleted_time: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True, comment="原删除时间")
    deleted_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="原删除人ID")
