"""Add tenant provisioning persistence foundation.

Revision ID: 20260811_01
Revises: 20260810_01
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_01"
down_revision: str | None = "20260810_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_tenant",
        sa.Column("unified_social_credit_code", sa.String(length=18), nullable=True),
    )
    op.create_unique_constraint(
        "uq_platform_tenant_site_uscc",
        "platform_tenant",
        ["site_id", "unified_social_credit_code"],
    )
    op.create_table(
        "platform_federated_tenant",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("central_tenant_uuid", sa.String(length=64), nullable=False),
        sa.Column("central_tenant_code", sa.String(length=100), nullable=False),
        sa.Column("local_tenant_id", sa.Integer(), nullable=False),
        sa.Column("provision_request_uuid", sa.String(length=64), nullable=False),
        sa.Column("target_package_code", sa.String(length=100), nullable=False),
        sa.Column("owner_central_user_uuid", sa.String(length=64), nullable=False),
        sa.Column("created_time", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["local_tenant_id"], ["platform_tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "site_id",
            "issuer",
            "central_tenant_uuid",
            name="uq_federated_tenant_site_subject",
        ),
        sa.UniqueConstraint(
            "site_id",
            "issuer",
            "local_tenant_id",
            name="uq_federated_tenant_site_local_tenant",
        ),
        sa.UniqueConstraint(
            "provision_request_uuid",
            name="uq_federated_tenant_provision_request",
        ),
        comment="中控租户与本地租户映射",
    )
    op.create_index(
        "ix_platform_federated_tenant_site_id",
        "platform_federated_tenant",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_federated_tenant_local_tenant_id",
        "platform_federated_tenant",
        ["local_tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("platform_federated_tenant")
    op.drop_constraint(
        "uq_platform_tenant_site_uscc",
        "platform_tenant",
        type_="unique",
    )
    op.drop_column("platform_tenant", "unified_social_credit_code")
