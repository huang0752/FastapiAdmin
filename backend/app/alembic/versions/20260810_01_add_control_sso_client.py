"""Add Control SSO client identity foundation.

Revision ID: 20260810_01
Revises: 20260807_01
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_01"
down_revision: str | None = "20260807_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sys_user",
        sa.Column("auth_source", sa.String(length=16), server_default="local", nullable=False),
    )
    op.add_column(
        "sys_user",
        sa.Column("password_login_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index("ix_sys_user_auth_source", "sys_user", ["auth_source"], unique=False)
    op.create_table(
        "sys_federated_identity",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("central_user_uuid", sa.String(length=64), nullable=False),
        sa.Column("local_user_id", sa.Integer(), nullable=False),
        sa.Column("created_time", sa.DateTime(), nullable=False),
        sa.Column("last_login_time", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["local_user_id"], ["sys_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "site_id",
            "issuer",
            "central_user_uuid",
            name="uq_federated_identity_site_subject",
        ),
        sa.UniqueConstraint(
            "site_id",
            "issuer",
            "local_user_id",
            name="uq_federated_identity_site_local_user",
        ),
        comment="中控身份与本地用户映射",
    )
    op.create_index(
        "ix_sys_federated_identity_site_id",
        "sys_federated_identity",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_federated_identity_local_user_id",
        "sys_federated_identity",
        ["local_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("sys_federated_identity")
    op.drop_index("ix_sys_user_auth_source", table_name="sys_user")
    op.drop_column("sys_user", "password_login_enabled")
    op.drop_column("sys_user", "auth_source")
