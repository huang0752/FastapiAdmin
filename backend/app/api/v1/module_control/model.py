"""Persistence models for the Control Provider."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, UserMixin


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ControlApplicationModel(ModelMixin, UserMixin):
    """A registered target-system deployment, isolated by Site."""

    __tablename__ = "control_application"
    __table_args__ = (
        UniqueConstraint("site_id", "code", name="uq_control_application_site_code"),
        CheckConstraint(
            "provisioning_timeout_seconds >= 1 AND provisioning_timeout_seconds <= 60",
            name="ck_control_application_provisioning_timeout",
        ),
        CheckConstraint(
            "entitlement_sync_timeout_seconds >= 1 AND entitlement_sync_timeout_seconds <= 60",
            name="ck_control_application_entitlement_sync_timeout",
        ),
        {"comment": "中控应用实例"},
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("provisioning_enabled", False)
        kwargs.setdefault("provisioning_timeout_seconds", 10)
        kwargs.setdefault("entitlement_sync_enabled", False)
        kwargs.setdefault("entitlement_sync_timeout_seconds", 10)
        super().__init__(**kwargs)

    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_site.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="所属品牌站点ID",
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="应用编码")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="应用名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None, comment="应用说明")
    icon: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None, comment="应用图标")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, comment="应用访问地址")
    callback_url: Mapped[str] = mapped_column(String(500), nullable=False, comment="精确回调地址")
    client_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, comment="客户端标识")
    client_secret_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="客户端密钥哈希")
    provisioning_url: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None, comment="目标租户开户地址")
    provisioning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否启用目标租户自动开户")
    provisioning_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10, comment="目标租户开户超时秒数")
    entitlement_sync_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True, default=None, comment="目标系统用户授权同步地址"
    )
    entitlement_sync_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
        comment="是否启用用户授权同步",
    )
    entitlement_sync_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default=text("10"),
        comment="用户授权同步超时秒数",
    )
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:启用 1:停用)")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="排序")


class ControlTenantApplicationModel(ModelMixin, UserMixin):
    """A target application opened for one central tenant."""

    __tablename__ = "control_tenant_application"
    __table_args__ = (
        UniqueConstraint("tenant_id", "application_id", name="uq_control_tenant_application_tenant_app"),
        UniqueConstraint(
            "id",
            "application_id",
            "site_id",
            name="uq_control_tenant_application_id_app_site",
        ),
        {"comment": "中控租户应用开通"},
    )

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
    target_tenant_code: Mapped[str] = mapped_column(String(100), nullable=False, comment="目标系统租户编码")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:启用 1:停用)")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, comment="开通时间")


class ControlUserApplicationGrantModel(ModelMixin, UserMixin):
    """An explicit user grant for a tenant application."""

    __tablename__ = "control_user_application_grant"
    __table_args__ = (
        UniqueConstraint("tenant_application_id", "user_id", name="uq_control_user_application_grant_binding_user"),
        UniqueConstraint(
            "id",
            "tenant_application_id",
            "site_id",
            name="uq_control_user_application_grant_id_binding_site",
        ),
        CheckConstraint(
            "desired_state IN ('active', 'inactive')",
            name="ck_control_user_application_grant_desired_state",
        ),
        CheckConstraint(
            "sync_status IN ('pending', 'processing', 'succeeded', 'failed')",
            name="ck_control_user_application_grant_sync_status",
        ),
        CheckConstraint("sync_version >= 0", name="ck_control_user_application_grant_sync_version"),
        CheckConstraint("retry_count >= 0", name="ck_control_user_application_grant_retry_count"),
        CheckConstraint("status IN (0, 1)", name="ck_control_user_application_grant_status"),
        {"comment": "中控用户应用授权"},
    )

    def __init__(self, **kwargs: object) -> None:
        if "desired_state" not in kwargs:
            kwargs["desired_state"] = "active" if kwargs.get("status") == 0 else "inactive"
        kwargs.setdefault("status", 0 if kwargs["desired_state"] == "active" else 1)
        kwargs.setdefault("sync_status", "pending")
        kwargs.setdefault("sync_version", 0)
        kwargs.setdefault("retry_count", 0)
        super().__init__(**kwargs)

    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_site.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="所属品牌站点ID",
    )
    tenant_application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("control_tenant_application.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="租户应用ID",
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_tenant.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="中控租户ID",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sys_user.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="中控用户ID",
    )
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True, comment="状态(0:启用 1:停用)")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, comment="授权时间")
    desired_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="inactive", server_default="inactive", index=True, comment="期望授权状态"
    )
    sync_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True, comment="授权同步状态"
    )
    sync_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="授权同步版本"
    )
    last_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, index=True, comment="最近同步事件ID")
    active_execution_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None, index=True, comment="当前执行代际令牌"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, comment="最近安全错误码")
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None, comment="最近脱敏错误摘要")
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="自动重试次数"
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True, comment="下次自动重试时间"
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, comment="最近同步尝试时间"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, comment="最近同步成功时间"
    )


class ControlSSOLaunchTicketModel(ModelMixin):
    """A hashed, short-lived, one-time SSO launch ticket."""

    __tablename__ = "control_sso_launch_ticket"
    __table_args__ = {"comment": "中控一次性登录启动票据"}

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="启动码SHA-256哈希")
    application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("control_application.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="应用ID",
    )
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
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sys_user.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="中控用户ID",
    )
    target_tenant_code: Mapped[str] = mapped_column(String(100), nullable=False, comment="目标系统租户编码")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued", index=True, comment="状态")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, comment="签发时间")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, comment="过期时间")
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None, comment="兑换时间")
    redeemed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, comment="兑换来源IP")
