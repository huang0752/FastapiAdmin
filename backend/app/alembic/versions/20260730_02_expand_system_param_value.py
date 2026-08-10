"""Expand system parameter values to text.

Revision ID: 20260730_02
Revises: 20260730_01
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_02"
down_revision: str | None = "20260730_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "sys_param",
        "config_value",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=True,
        existing_comment="参数键值",
    )


def downgrade() -> None:
    params_table = sa.table("sys_param", sa.column("config_value", sa.Text()))
    max_value_length = op.get_bind().scalar(
        sa.select(sa.func.max(sa.func.char_length(params_table.c.config_value)))
    )
    target_length = max(500, int(max_value_length or 0))

    op.alter_column(
        "sys_param",
        "config_value",
        existing_type=sa.Text(),
        # 降级不得截断扩容后已经写入的参数值；空表仍恢复原始 500 长度。
        type_=sa.String(length=target_length),
        existing_nullable=True,
        existing_comment="参数键值",
    )
