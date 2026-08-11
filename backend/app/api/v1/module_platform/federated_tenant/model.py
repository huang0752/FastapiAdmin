from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class FederatedTenantModel(MappedBase):
    """中控租户与目标产品本地租户的稳定映射。"""

    __tablename__ = "platform_federated_tenant"
    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "issuer",
            "central_tenant_uuid",
            name="uq_federated_tenant_site_subject",
        ),
        UniqueConstraint(
            "site_id",
            "issuer",
            "local_tenant_id",
            name="uq_federated_tenant_site_local_tenant",
        ),
        UniqueConstraint(
            "provision_request_uuid",
            name="uq_federated_tenant_provision_request",
        ),
        {"comment": "中控租户与本地租户映射"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_site.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    central_tenant_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    central_tenant_code: Mapped[str] = mapped_column(String(100), nullable=False)
    local_tenant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_tenant.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provision_request_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    target_package_code: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_central_user_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    created_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
