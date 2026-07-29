"""Preserve the Host/Site foundation revision as a compatibility marker.

The repository originally shipped this revision as its first Alembic file even
though it altered pre-existing tables.  ``20260726_00`` now provides a complete
static schema for fresh databases.  This marker keeps databases already stamped
at ``20260727_01`` compatible while fresh installs receive the default framework
Site records.

Revision ID: 20260727_01
Revises: 20260726_00
Create Date: 2026-07-27
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260727_01"
down_revision: str | None = "20260726_00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    site_table = sa.table(
        "platform_site",
        sa.column("id", sa.Integer()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("status", sa.Integer()),
        sa.column("uuid", sa.String()),
        sa.column("is_deleted", sa.Boolean()),
        sa.column("created_time", sa.DateTime()),
        sa.column("updated_time", sa.DateTime()),
    )
    existing_site_id = connection.execute(sa.select(site_table.c.id).where(site_table.c.code == "default")).scalar_one_or_none()
    if existing_site_id is not None:
        return

    now = datetime.now()
    default_site_id = connection.execute(
        site_table.insert()
        .values(
            code="default",
            name="FastapiAdmin",
            status=0,
            uuid=uuid4().hex,
            is_deleted=False,
            created_time=now,
            updated_time=now,
        )
        .returning(site_table.c.id)
    ).scalar_one()
    domain_table = sa.table(
        "platform_site_domain",
        sa.column("site_id", sa.Integer()),
        sa.column("host", sa.String()),
        sa.column("is_primary", sa.Boolean()),
        sa.column("uuid", sa.String()),
        sa.column("is_deleted", sa.Boolean()),
        sa.column("created_time", sa.DateTime()),
        sa.column("updated_time", sa.DateTime()),
    )
    op.bulk_insert(
        domain_table,
        [
            {"site_id": default_site_id, "host": "service.fastapiadmin.com", "is_primary": True, "uuid": uuid4().hex, "is_deleted": False, "created_time": now, "updated_time": now},
            {"site_id": default_site_id, "host": "localhost", "is_primary": False, "uuid": uuid4().hex, "is_deleted": False, "created_time": now, "updated_time": now},
            {"site_id": default_site_id, "host": "127.0.0.1", "is_primary": False, "uuid": uuid4().hex, "is_deleted": False, "created_time": now, "updated_time": now},
            {"site_id": default_site_id, "host": "testserver", "is_primary": False, "uuid": uuid4().hex, "is_deleted": False, "created_time": now, "updated_time": now},
        ],
    )


def downgrade() -> None:
    connection = op.get_bind()
    site_table = sa.table("platform_site", sa.column("id", sa.Integer()), sa.column("code", sa.String()), sa.column("name", sa.String()))
    site_id = connection.execute(sa.select(site_table.c.id).where(site_table.c.code == "default", site_table.c.name == "FastapiAdmin")).scalar_one_or_none()
    if site_id is None:
        return
    domain_table = sa.table("platform_site_domain", sa.column("site_id", sa.Integer()))
    connection.execute(domain_table.delete().where(domain_table.c.site_id == site_id))
    connection.execute(site_table.delete().where(site_table.c.id == site_id))
