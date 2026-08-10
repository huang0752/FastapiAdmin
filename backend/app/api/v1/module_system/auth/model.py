from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import MappedBase


class FederatedIdentityModel(MappedBase):
    """中控身份与本地用户映射。"""

    __tablename__ = "sys_federated_identity"
    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "issuer",
            "central_user_uuid",
            name="uq_federated_identity_site_subject",
        ),
        UniqueConstraint(
            "site_id",
            "issuer",
            "local_user_id",
            name="uq_federated_identity_site_local_user",
        ),
        {"comment": "中控身份与本地用户映射"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platform_site.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    central_user_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    local_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    last_login_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
