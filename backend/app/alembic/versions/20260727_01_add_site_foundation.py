"""add Host/Site foundation

Revision ID: 20260727_01
Revises: None
Create Date: 2026-07-27
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20260727_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _model_columns(*, include_site_id: bool = False) -> list[sa.Column]:
    columns = [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_time", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_time", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_time", sa.DateTime(), nullable=True),
    ]
    if include_site_id:
        columns.insert(
            0,
            sa.Column(
                "site_id",
                sa.Integer(),
                sa.ForeignKey("platform_site.id", ondelete="CASCADE", onupdate="CASCADE"),
                nullable=False,
            ),
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "platform_site",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("logo_url", sa.String(length=500), nullable=True),
        sa.Column("favicon", sa.String(length=500), nullable=True),
        sa.Column("login_bg", sa.String(length=500), nullable=True),
        sa.Column("copyright", sa.String(length=255), nullable=True),
        sa.Column("keep_record", sa.String(length=100), nullable=True),
        sa.Column("help_doc", sa.String(length=500), nullable=True),
        sa.Column("privacy", sa.String(length=500), nullable=True),
        sa.Column("clause", sa.String(length=500), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        *_model_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_platform_site_code"),
        sa.UniqueConstraint("name", name="uq_platform_site_name"),
        sa.UniqueConstraint("uuid", name="uq_platform_site_uuid"),
        comment="品牌站点表",
    )
    op.create_index("ix_platform_site_code", "platform_site", ["code"], unique=False)
    op.create_index("ix_platform_site_status", "platform_site", ["status"], unique=False)

    op.create_table(
        "platform_site_domain",
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_model_columns(include_site_id=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host", name="uq_platform_site_domain_host"),
        sa.UniqueConstraint("uuid", name="uq_platform_site_domain_uuid"),
        comment="站点域名表",
    )
    op.create_index("ix_platform_site_domain_host", "platform_site_domain", ["host"], unique=False)
    op.create_index("ix_platform_site_domain_site_id", "platform_site_domain", ["site_id"], unique=False)

    op.add_column("platform_tenant", sa.Column("site_id", sa.Integer(), nullable=True, comment="所属品牌站点ID"))
    op.add_column("platform_package", sa.Column("site_id", sa.Integer(), nullable=True, comment="所属品牌站点ID"))

    now = datetime.now()
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
    default_site_id = op.get_bind().execute(
        site_table.insert().values(
            code="default",
            name="FastapiAdmin",
            status=0,
            uuid=uuid4().hex,
            is_deleted=False,
            created_time=now,
            updated_time=now,
        ).returning(site_table.c.id)
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
    op.execute(sa.update(sa.table("platform_tenant", sa.column("site_id", sa.Integer()))).values(site_id=default_site_id))
    op.execute(sa.update(sa.table("platform_package", sa.column("site_id", sa.Integer()))).values(site_id=default_site_id))
    op.alter_column("platform_tenant", "site_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("platform_package", "site_id", existing_type=sa.Integer(), nullable=False)

    op.create_index("ix_platform_tenant_site_id", "platform_tenant", ["site_id"], unique=False)
    op.create_foreign_key(
        "fk_platform_tenant_site_id_platform_site",
        "platform_tenant",
        "platform_site",
        ["site_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )
    op.drop_constraint("platform_package_name_key", "platform_package", type_="unique")
    op.drop_constraint("platform_package_code_key", "platform_package", type_="unique")
    op.create_unique_constraint("uq_platform_package_site_name", "platform_package", ["site_id", "name"])
    op.create_unique_constraint("uq_platform_package_site_code", "platform_package", ["site_id", "code"])
    op.create_index("ix_platform_package_site_id", "platform_package", ["site_id"], unique=False)
    op.create_foreign_key(
        "fk_platform_package_site_id_platform_site",
        "platform_package",
        "platform_site",
        ["site_id"],
        ["id"],
        onupdate="CASCADE",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("uq_platform_package_site_code", "platform_package", type_="unique")
    op.drop_constraint("uq_platform_package_site_name", "platform_package", type_="unique")
    op.create_unique_constraint("platform_package_code_key", "platform_package", ["code"])
    op.create_unique_constraint("platform_package_name_key", "platform_package", ["name"])
    op.drop_constraint("fk_platform_package_site_id_platform_site", "platform_package", type_="foreignkey")
    op.drop_index("ix_platform_package_site_id", table_name="platform_package")
    op.drop_column("platform_package", "site_id")
    op.drop_constraint("fk_platform_tenant_site_id_platform_site", "platform_tenant", type_="foreignkey")
    op.drop_index("ix_platform_tenant_site_id", table_name="platform_tenant")
    op.drop_column("platform_tenant", "site_id")
    op.drop_index("ix_platform_site_domain_site_id", table_name="platform_site_domain")
    op.drop_index("ix_platform_site_domain_host", table_name="platform_site_domain")
    op.drop_table("platform_site_domain")
    op.drop_index("ix_platform_site_status", table_name="platform_site")
    op.drop_index("ix_platform_site_code", table_name="platform_site")
    op.drop_table("platform_site")
