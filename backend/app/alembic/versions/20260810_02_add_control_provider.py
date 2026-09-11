"""Add Control Provider application and authorization tables.

Revision ID: 20260810_02
Revises: 20260810_01
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_02"
down_revision: str | None = "20260810_01"
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


def _audit_foreign_keys() -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(["created_id"], ["sys_user.id"], ondelete="SET NULL", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["updated_id"], ["sys_user.id"], ondelete="SET NULL", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["deleted_id"], ["sys_user.id"], ondelete="SET NULL", onupdate="CASCADE"),
    ]


def upgrade() -> None:
    op.create_table(
        "control_application",
        *_model_columns(include_audit_users=True),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(length=500), nullable=True),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("callback_url", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("client_secret_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        *_audit_foreign_keys(),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_control_application_client_id"),
        sa.UniqueConstraint("site_id", "code", name="uq_control_application_site_code"),
        comment="中控应用实例",
    )
    _create_model_indexes("control_application", include_audit_users=True)
    op.create_index("ix_control_application_site_id", "control_application", ["site_id"], unique=False)
    op.create_index("ix_control_application_status", "control_application", ["status"], unique=False)

    op.create_table(
        "control_tenant_application",
        *_model_columns(include_audit_users=True),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("target_tenant_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        *_audit_foreign_keys(),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["platform_tenant.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["control_application.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "application_id", name="uq_control_tenant_application_tenant_app"),
        comment="中控租户应用开通",
    )
    _create_model_indexes("control_tenant_application", include_audit_users=True)
    op.create_index("ix_control_tenant_application_site_id", "control_tenant_application", ["site_id"], unique=False)
    op.create_index("ix_control_tenant_application_tenant_id", "control_tenant_application", ["tenant_id"], unique=False)
    op.create_index("ix_control_tenant_application_application_id", "control_tenant_application", ["application_id"], unique=False)
    op.create_index("ix_control_tenant_application_status", "control_tenant_application", ["status"], unique=False)

    op.create_table(
        "control_user_application_grant",
        *_model_columns(include_audit_users=True),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("tenant_application_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        *_audit_foreign_keys(),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_application_id"], ["control_tenant_application.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["platform_tenant.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_application_id", "user_id", name="uq_control_user_application_grant_binding_user"),
        comment="中控用户应用授权",
    )
    _create_model_indexes("control_user_application_grant", include_audit_users=True)
    op.create_index("ix_control_user_application_grant_site_id", "control_user_application_grant", ["site_id"], unique=False)
    op.create_index("ix_control_user_application_grant_tenant_application_id", "control_user_application_grant", ["tenant_application_id"], unique=False)
    op.create_index("ix_control_user_application_grant_tenant_id", "control_user_application_grant", ["tenant_id"], unique=False)
    op.create_index("ix_control_user_application_grant_user_id", "control_user_application_grant", ["user_id"], unique=False)
    op.create_index("ix_control_user_application_grant_status", "control_user_application_grant", ["status"], unique=False)

    op.create_table(
        "control_sso_launch_ticket",
        *_model_columns(include_audit_users=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_tenant_code", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["control_application.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["platform_site.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["platform_tenant.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], ondelete="RESTRICT", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_control_sso_launch_ticket_code_hash"),
        comment="中控一次性登录启动票据",
    )
    _create_model_indexes("control_sso_launch_ticket", include_audit_users=False)
    op.create_index("ix_control_sso_launch_ticket_application_id", "control_sso_launch_ticket", ["application_id"], unique=False)
    op.create_index("ix_control_sso_launch_ticket_site_id", "control_sso_launch_ticket", ["site_id"], unique=False)
    op.create_index("ix_control_sso_launch_ticket_tenant_id", "control_sso_launch_ticket", ["tenant_id"], unique=False)
    op.create_index("ix_control_sso_launch_ticket_user_id", "control_sso_launch_ticket", ["user_id"], unique=False)
    op.create_index("ix_control_sso_launch_ticket_status", "control_sso_launch_ticket", ["status"], unique=False)
    op.create_index("ix_control_sso_launch_ticket_expires_at", "control_sso_launch_ticket", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("control_sso_launch_ticket")
    op.drop_table("control_user_application_grant")
    op.drop_table("control_tenant_application")
    op.drop_table("control_application")
