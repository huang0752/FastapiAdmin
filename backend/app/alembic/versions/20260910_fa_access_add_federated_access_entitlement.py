"""Add product-local federated access entitlement and event receipts.

Revision ID: 20260910_fa_access
Revises: 20260812_02
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_fa_access"
down_revision: str | None = "20260812_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sys_role",
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="是否系统保留角色",
        ),
    )
    op.execute(
        "UPDATE sys_role SET is_system = true "
        "WHERE code IN ('SUPER_ADMIN', 'ADMIN', 'USER', 'owner', 'admin', 'member')"
    )
    op.create_table(
        "sys_federated_access_entitlement",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("local_user_id", sa.Integer(), nullable=True),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("central_user_uuid", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="inactive",
        ),
        sa.Column(
            "applied_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_event_id", sa.String(length=64), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "session_cleanup_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_federated_access_entitlement_status",
        ),
        sa.CheckConstraint(
            "applied_version >= 0",
            name="ck_federated_access_entitlement_version",
        ),
        sa.ForeignKeyConstraint(["local_user_id"], ["sys_user.id"]),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["platform_tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issuer",
            "central_user_uuid",
            "tenant_id",
            name="uq_federated_access_entitlement_subject_tenant",
        ),
        comment="中控用户在产品租户中的联邦访问资格",
    )
    op.create_index(
        "ix_sys_federated_access_entitlement_site_id",
        "sys_federated_access_entitlement",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_federated_access_entitlement_tenant_id",
        "sys_federated_access_entitlement",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_federated_access_entitlement_local_user_id",
        "sys_federated_access_entitlement",
        ["local_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_federated_access_entitlement_status",
        "sys_federated_access_entitlement",
        ["status"],
        unique=False,
    )

    op.create_table(
        "sys_federated_access_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), nullable=False),
        sa.Column("sync_version", sa.Integer(), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "sync_version > 0",
            name="ck_federated_access_event_version",
        ),
        sa.CheckConstraint(
            "desired_state IN ('active', 'inactive')",
            name="ck_federated_access_event_desired_state",
        ),
        sa.ForeignKeyConstraint(
            ["entitlement_id"],
            ["sys_federated_access_entitlement.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            name="uq_federated_access_event_event_id",
        ),
        comment="联邦访问资格同步事件回执",
    )
    op.create_index(
        "ix_sys_federated_access_event_entitlement_id",
        "sys_federated_access_event",
        ["entitlement_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("sys_federated_access_event")
    op.drop_table("sys_federated_access_entitlement")
    op.drop_column("sys_role", "is_system")
