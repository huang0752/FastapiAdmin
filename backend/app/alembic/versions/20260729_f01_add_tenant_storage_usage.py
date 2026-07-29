"""Add tenant private-file storage usage ledger.

Revision ID: 20260729_f01
Revises: 20260727_01
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_f01"
down_revision: str | None = "20260727_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_tenant_storage_usage",
        sa.Column("tenant_id", sa.Integer(), nullable=False, comment="租户ID"),
        sa.Column("used_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False, comment="已用字节数"),
        sa.Column("reserved_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False, comment="预占字节数"),
        sa.Column("updated_time", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="更新时间"),
        sa.CheckConstraint("reserved_bytes >= 0", name="ck_tenant_storage_reserved_nonnegative"),
        sa.CheckConstraint("used_bytes >= 0", name="ck_tenant_storage_used_nonnegative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["platform_tenant.id"], onupdate="CASCADE", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
        comment="租户私有文件存储用量",
    )


def downgrade() -> None:
    op.drop_table("platform_tenant_storage_usage")
