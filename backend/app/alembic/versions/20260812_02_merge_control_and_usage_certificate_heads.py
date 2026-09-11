"""Merge Control provisioning and software usage certificate heads.

Revision ID: 20260812_02
Revises: 20260811_03, 20260812_01
Create Date: 2026-08-12
"""

from collections.abc import Sequence

revision: str = "20260812_02"
down_revision: tuple[str, str] = ("20260811_03", "20260812_01")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
