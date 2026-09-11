"""Merge framework tenant provisioning and Control Provider heads.

Revision ID: 20260811_02
Revises: 20260810_02, 20260811_01
Create Date: 2026-08-11
"""

from collections.abc import Sequence

revision: str = "20260811_02"
down_revision: tuple[str, str] = ("20260810_02", "20260811_01")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
