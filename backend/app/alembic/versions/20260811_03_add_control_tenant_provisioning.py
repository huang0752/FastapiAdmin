"""Add Control tenant-provisioning catalog and ledger.

Revision ID: 20260811_03
Revises: 20260811_02
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_03"
down_revision: str | None = "20260811_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _model_columns(*, include_audit_users: bool) -> list[sa.Column]:
    columns = [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("created_time", sa.DateTime(), nullable=False),
        sa.Column("updated_time", sa.DateTime(), nullable=False),
        sa.Column("deleted_time", sa.DateTime(), nullable=True),
    ]
    if include_audit_users:
        columns.extend(
            [
                sa.Column("created_id", sa.Integer(), nullable=True),
                sa.Column("updated_id", sa.Integer(), nullable=True),
                sa.Column("deleted_id", sa.Integer(), nullable=True),
            ]
        )
    return columns


def _audit_foreign_keys() -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(["created_id"], ["sys_user.id"], ondelete="SET NULL", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["updated_id"], ["sys_user.id"], ondelete="SET NULL", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["deleted_id"], ["sys_user.id"], ondelete="SET NULL", onupdate="CASCADE"),
    ]


def _create_model_indexes(table_name: str, *, include_audit_users: bool) -> None:
    op.create_index(f"ix_{table_name}_id", table_name, ["id"], unique=False)
    op.create_index(f"ix_{table_name}_uuid", table_name, ["uuid"], unique=True)
    op.create_index(f"ix_{table_name}_is_deleted", table_name, ["is_deleted"], unique=False)
    op.create_index(f"ix_{table_name}_created_time", table_name, ["created_time"], unique=False)
    op.create_index(f"ix_{table_name}_updated_time", table_name, ["updated_time"], unique=False)
    op.create_index(f"ix_{table_name}_deleted_time", table_name, ["deleted_time"], unique=False)
    if include_audit_users:
        op.create_index(f"ix_{table_name}_created_id", table_name, ["created_id"], unique=False)
        op.create_index(f"ix_{table_name}_updated_id", table_name, ["updated_id"], unique=False)
        op.create_index(f"ix_{table_name}_deleted_id", table_name, ["deleted_id"], unique=False)


def upgrade() -> None:
    op.add_column("control_application", sa.Column("provisioning_url", sa.String(length=500), nullable=True))
    op.add_column(
        "control_application",
        sa.Column("provisioning_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "control_application",
        sa.Column("provisioning_timeout_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
    )
    op.create_check_constraint(
        "ck_control_application_provisioning_timeout",
        "control_application",
        "provisioning_timeout_seconds >= 1 AND provisioning_timeout_seconds <= 60",
    )

    op.create_table(
        "control_application_package",
        *_model_columns(include_audit_users=True),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_package_code", sa.String(length=100), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        *_audit_foreign_keys(),
        sa.CheckConstraint("status IN (0, 1)", name="ck_control_application_package_status"),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["control_application.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "code", name="uq_control_application_package_app_code"),
        sa.UniqueConstraint(
            "application_id",
            "target_package_code",
            name="uq_control_application_package_app_target_code",
        ),
        comment="中控应用套餐目录",
    )
    _create_model_indexes("control_application_package", include_audit_users=True)
    op.create_index("ix_control_application_package_site_id", "control_application_package", ["site_id"], unique=False)
    op.create_index("ix_control_application_package_application_id", "control_application_package", ["application_id"], unique=False)
    op.create_index("ix_control_application_package_is_default", "control_application_package", ["is_default"], unique=False)
    op.create_index("ix_control_application_package_status", "control_application_package", ["status"], unique=False)
    op.create_index(
        "uq_control_application_package_one_default",
        "control_application_package",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND is_deleted = false"),
        sqlite_where=sa.text("is_default = true AND is_deleted = false"),
    )

    op.create_table(
        "control_tenant_provision",
        *_model_columns(include_audit_users=True),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("application_package_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("provision_request_uuid", sa.String(length=64), nullable=False),
        sa.Column("desired_target_tenant_code", sa.String(length=100), nullable=False),
        sa.Column("target_tenant_code", sa.String(length=100), nullable=True),
        sa.Column("target_tenant_uuid", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("active_execution_token", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_foreign_keys(),
        sa.CheckConstraint("status IN ('pending', 'processing', 'succeeded', 'failed')", name="ck_control_tenant_provision_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_control_tenant_provision_attempt_count"),
        sa.CheckConstraint("max_attempts = 2", name="ck_control_tenant_provision_max_attempts"),
        sa.CheckConstraint("attempt_count <= max_attempts", name="ck_control_tenant_provision_attempt_limit"),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["platform_tenant.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["control_application.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(
            ["application_package_id"],
            ["control_application_package.id"],
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["sys_user.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "application_id", name="uq_control_tenant_provision_tenant_app"),
        sa.UniqueConstraint("provision_request_uuid", name="uq_control_tenant_provision_request_uuid"),
        comment="中控租户产品开通账本",
    )
    _create_model_indexes("control_tenant_provision", include_audit_users=True)
    op.create_index("ix_control_tenant_provision_site_id", "control_tenant_provision", ["site_id"], unique=False)
    op.create_index("ix_control_tenant_provision_tenant_id", "control_tenant_provision", ["tenant_id"], unique=False)
    op.create_index("ix_control_tenant_provision_application_id", "control_tenant_provision", ["application_id"], unique=False)
    op.create_index("ix_control_tenant_provision_application_package_id", "control_tenant_provision", ["application_package_id"], unique=False)
    op.create_index("ix_control_tenant_provision_owner_user_id", "control_tenant_provision", ["owner_user_id"], unique=False)
    op.create_index("ix_control_tenant_provision_status", "control_tenant_provision", ["status"], unique=False)
    op.create_index(
        "ix_control_tenant_provision_active_execution_token",
        "control_tenant_provision",
        ["active_execution_token"],
        unique=False,
    )
    op.create_index("ix_control_tenant_provision_next_retry_at", "control_tenant_provision", ["next_retry_at"], unique=False)

    op.create_table(
        "control_tenant_provision_ticket",
        *_model_columns(include_audit_users=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("provision_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('issued', 'redeemed', 'expired')", name="ck_control_tenant_provision_ticket_status"),
        sa.CheckConstraint("expires_at > issued_at", name="ck_control_tenant_provision_ticket_expiry"),
        sa.CheckConstraint(
            "(status = 'redeemed' AND redeemed_at IS NOT NULL) OR "
            "(status IN ('issued', 'expired') AND redeemed_at IS NULL)",
            name="ck_control_tenant_provision_ticket_redemption",
        ),
        sa.ForeignKeyConstraint(["provision_id"], ["control_tenant_provision.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_control_tenant_provision_ticket_code_hash"),
        comment="中控一次性租户开户票据",
    )
    _create_model_indexes("control_tenant_provision_ticket", include_audit_users=False)
    op.create_index("ix_control_tenant_provision_ticket_provision_id", "control_tenant_provision_ticket", ["provision_id"], unique=False)
    op.create_index("ix_control_tenant_provision_ticket_status", "control_tenant_provision_ticket", ["status"], unique=False)
    op.create_index("ix_control_tenant_provision_ticket_expires_at", "control_tenant_provision_ticket", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("control_tenant_provision_ticket")
    op.drop_table("control_tenant_provision")
    op.drop_table("control_application_package")
    op.drop_constraint("ck_control_application_provisioning_timeout", "control_application", type_="check")
    op.drop_column("control_application", "provisioning_timeout_seconds")
    op.drop_column("control_application", "provisioning_enabled")
    op.drop_column("control_application", "provisioning_url")
