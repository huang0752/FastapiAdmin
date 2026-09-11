"""Persistence model for Control application-package mappings."""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, UserMixin


class ControlApplicationPackageModel(ModelMixin, UserMixin):
    """A Control-visible package mapped to one target-local package code."""

    __tablename__ = "control_application_package"
    __table_args__ = (
        UniqueConstraint("application_id", "code", name="uq_control_application_package_app_code"),
        UniqueConstraint(
            "application_id",
            "target_package_code",
            name="uq_control_application_package_app_target_code",
        ),
        Index(
            "uq_control_application_package_one_default",
            "application_id",
            unique=True,
            postgresql_where=text("is_default = true AND is_deleted = false"),
            sqlite_where=text("is_default = true AND is_deleted = false"),
        ),
        CheckConstraint("status IN (0, 1)", name="ck_control_application_package_status"),
        {"comment": "中控应用套餐目录"},
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("is_default", False)
        kwargs.setdefault("status", 0)
        kwargs.setdefault("sort", 0)
        super().__init__(**kwargs)

    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_site.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="所属品牌站点ID",
    )
    application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("control_application.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="应用ID",
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="中控套餐编码")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="套餐名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None, comment="套餐说明")
    target_package_code: Mapped[str] = mapped_column(String(100), nullable=False, comment="目标产品本地套餐编码")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, comment="是否默认套餐")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="状态(0:启用 1:停用)")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="排序")
