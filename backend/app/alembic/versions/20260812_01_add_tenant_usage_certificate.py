"""Add fixed software usage certificate identity to tenants.

Revision ID: 20260812_01
Revises: 20260811_01
Create Date: 2026-08-12
"""

import secrets
import string
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_01"
down_revision: str | None = "20260811_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALPHABET = string.ascii_uppercase + string.digits


def _identity(code: str, numbers: set[str], tokens: set[str]) -> tuple[str, str]:
    while True:
        suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        number = f"FA-SW-{code.upper()}-{suffix}"
        token = secrets.token_urlsafe(32)
        if number not in numbers and token not in tokens:
            numbers.add(number)
            tokens.add(token)
            return number, token


def upgrade() -> None:
    op.add_column("platform_tenant", sa.Column("usage_certificate_no", sa.String(length=160), nullable=True))
    op.add_column("platform_tenant", sa.Column("usage_certificate_token", sa.String(length=64), nullable=True))
    op.add_column("platform_tenant", sa.Column("usage_certificate_created_at", sa.DateTime(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, code, created_time FROM platform_tenant ORDER BY id")).mappings()
    numbers: set[str] = set()
    tokens: set[str] = set()
    fallback = datetime.now()
    for row in rows:
        number, token = _identity(str(row["code"]), numbers, tokens)
        bind.execute(
            sa.text(
                "UPDATE platform_tenant SET usage_certificate_no=:number, "
                "usage_certificate_token=:token, usage_certificate_created_at=:created_at WHERE id=:id"
            ),
            {"number": number, "token": token, "created_at": row["created_time"] or fallback, "id": row["id"]},
        )

    op.create_unique_constraint("uq_platform_tenant_usage_certificate_no", "platform_tenant", ["usage_certificate_no"])
    op.create_unique_constraint("uq_platform_tenant_usage_certificate_token", "platform_tenant", ["usage_certificate_token"])
    op.alter_column("platform_tenant", "usage_certificate_no", nullable=False)
    op.alter_column("platform_tenant", "usage_certificate_token", nullable=False)
    op.alter_column("platform_tenant", "usage_certificate_created_at", nullable=False)


def downgrade() -> None:
    op.drop_constraint("uq_platform_tenant_usage_certificate_token", "platform_tenant", type_="unique")
    op.drop_constraint("uq_platform_tenant_usage_certificate_no", "platform_tenant", type_="unique")
    op.drop_column("platform_tenant", "usage_certificate_created_at")
    op.drop_column("platform_tenant", "usage_certificate_token")
    op.drop_column("platform_tenant", "usage_certificate_no")
