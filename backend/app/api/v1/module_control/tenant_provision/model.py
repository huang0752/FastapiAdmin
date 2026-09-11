"""Persistence models for Control tenant provisioning."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, UserMixin


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ControlTenantProvisionModel(ModelMixin, UserMixin):
    """The single Control-side fact source for one tenant/application opening."""

    __tablename__ = "control_tenant_provision"
    __table_args__ = (
        UniqueConstraint("tenant_id", "application_id", name="uq_control_tenant_provision_tenant_app"),
        UniqueConstraint("provision_request_uuid", name="uq_control_tenant_provision_request_uuid"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed')",
            name="ck_control_tenant_provision_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_control_tenant_provision_attempt_count"),
        CheckConstraint("max_attempts = 2", name="ck_control_tenant_provision_max_attempts"),
        CheckConstraint("attempt_count <= max_attempts", name="ck_control_tenant_provision_attempt_limit"),
        {"comment": "中控租户产品开通账本"},
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", "pending")
        kwargs.setdefault("attempt_count", 0)
        kwargs.setdefault("max_attempts", 2)
        super().__init__(**kwargs)

    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_site.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="所属品牌站点ID",
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_tenant.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="中控租户ID",
    )
    application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("control_application.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="应用ID",
    )
    application_package_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("control_application_package.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="中控应用套餐ID",
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sys_user.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="中控租户初始管理员ID",
    )
    provision_request_uuid: Mapped[str] = mapped_column(String(64), nullable=False, comment="幂等开户请求UUID")
    desired_target_tenant_code: Mapped[str] = mapped_column(String(100), nullable=False, comment="期望目标租户编码")
    target_tenant_code: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None, comment="目标实际租户编码")
    target_tenant_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, comment="目标实际租户UUID")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True, comment="开通状态")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="已执行次数")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2, comment="最大执行次数")
    active_execution_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None, index=True, comment="当前业务任务执行代际令牌"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, comment="最近安全错误码")
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None, comment="最近安全错误摘要")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, index=True, comment="下次自动重试时间")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, comment="最近开始时间")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, comment="完成时间")


class ControlTenantProvisionTicketModel(ModelMixin):
    """A hashed, short-lived and single-use tenant-provisioning ticket."""

    __tablename__ = "control_tenant_provision_ticket"
    __table_args__ = (
        CheckConstraint(
            "status IN ('issued', 'redeemed', 'expired')",
            name="ck_control_tenant_provision_ticket_status",
        ),
        CheckConstraint("expires_at > issued_at", name="ck_control_tenant_provision_ticket_expiry"),
        CheckConstraint(
            "(status = 'redeemed' AND redeemed_at IS NOT NULL) OR "
            "(status IN ('issued', 'expired') AND redeemed_at IS NULL)",
            name="ck_control_tenant_provision_ticket_redemption",
        ),
        {"comment": "中控一次性租户开户票据"},
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", "issued")
        kwargs.setdefault("issued_at", _utc_now())
        super().__init__(**kwargs)

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="开户码SHA-256哈希")
    provision_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("control_tenant_provision.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="开通账本ID",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued", index=True, comment="票据状态")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, comment="签发时间")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="过期时间")
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, comment="兑换时间")
