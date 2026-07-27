from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.common.enums import PermissionFilterStrategy
from app.core.base_model import ModelMixin

if TYPE_CHECKING:
    from app.api.v1.module_platform.package.model import PackageModel
    from app.api.v1.module_platform.tenant.model import TenantModel


class SiteModel(ModelMixin):
    """共享部署中的品牌站点。"""

    __tablename__ = "platform_site"
    __table_args__ = {"comment": "品牌站点表"}
    __permission_strategy__ = PermissionFilterStrategy.DATA_SCOPE

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True, comment="站点编码")
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, comment="站点名称")
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    favicon: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    login_bg: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    copyright: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    keep_record: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    help_doc: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    privacy: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    clause: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:启用 1:停用)")

    domains: Mapped[list["SiteDomainModel"]] = relationship(
        "SiteDomainModel",
        back_populates="site",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SiteDomainModel.id",
    )
    tenants: Mapped[list["TenantModel"]] = relationship("TenantModel", back_populates="site", lazy="raise")
    packages: Mapped[list["PackageModel"]] = relationship("PackageModel", back_populates="site", lazy="raise")

    @validates("code")
    def validate_code(self, key: str, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not normalized.replace("_", "").isalnum():
            raise ValueError("站点编码仅允许字母、数字和下划线")
        return normalized


class SiteDomainModel(ModelMixin):
    """站点域名；规范化后的 host 在全平台唯一。"""

    __tablename__ = "platform_site_domain"
    __table_args__ = (
        UniqueConstraint("host", name="uq_platform_site_domain_host"),
        {"comment": "站点域名表"},
    )

    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_site.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    site: Mapped[SiteModel] = relationship("SiteModel", back_populates="domains", lazy="joined")
