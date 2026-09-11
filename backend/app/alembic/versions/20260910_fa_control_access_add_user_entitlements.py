"""Add Control user-entitlement ledger and one-time ticket.

Revision ID: 20260910_fa_control_access
Revises: 20260910_fa_access
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_fa_control_access"
down_revision: str | None = "20260910_fa_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_model_indexes(table_name: str) -> None:
    op.create_index(f"ix_{table_name}_id", table_name, ["id"], unique=False)
    op.create_index(f"ix_{table_name}_uuid", table_name, ["uuid"], unique=True)
    op.create_index(f"ix_{table_name}_is_deleted", table_name, ["is_deleted"], unique=False)
    op.create_index(f"ix_{table_name}_created_time", table_name, ["created_time"], unique=False)
    op.create_index(f"ix_{table_name}_updated_time", table_name, ["updated_time"], unique=False)
    op.create_index(f"ix_{table_name}_deleted_time", table_name, ["deleted_time"], unique=False)


def upgrade() -> None:
    # Operational boundary: the backup copy, ledger rewrite, and parent unique
    # constraints can lock large grant/opening tables. Run this revision in a
    # maintenance window; concurrent-index/online-DDL variants are out of scope.
    op.add_column("control_application", sa.Column("entitlement_sync_url", sa.String(length=500), nullable=True))
    op.add_column(
        "control_application",
        sa.Column("entitlement_sync_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "control_application",
        sa.Column("entitlement_sync_timeout_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
    )
    op.create_check_constraint(
        "ck_control_application_entitlement_sync_timeout",
        "control_application",
        "entitlement_sync_timeout_seconds >= 1 AND entitlement_sync_timeout_seconds <= 60",
    )

    op.add_column(
        "control_user_application_grant",
        sa.Column("desired_state", sa.String(length=16), server_default="inactive", nullable=False),
    )
    op.add_column(
        "control_user_application_grant",
        sa.Column("sync_status", sa.String(length=16), server_default="pending", nullable=False),
    )
    op.add_column(
        "control_user_application_grant",
        sa.Column("sync_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("control_user_application_grant", sa.Column("last_event_id", sa.String(length=64), nullable=True))
    op.add_column(
        "control_user_application_grant",
        sa.Column("active_execution_token", sa.String(length=64), nullable=True),
    )
    op.add_column("control_user_application_grant", sa.Column("last_error_code", sa.String(length=64), nullable=True))
    op.add_column("control_user_application_grant", sa.Column("last_error_message", sa.String(length=500), nullable=True))
    op.add_column(
        "control_user_application_grant",
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "control_user_application_grant",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "control_user_application_grant",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "control_user_application_grant",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "control_user_application_grant_20260910_backup",
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_time", sa.DateTime(), nullable=True),
        sa.Column("deleted_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("grant_id"),
        comment="20260910用户授权迁移回滚备份",
    )
    op.execute(
        sa.text(
            """
            INSERT INTO control_user_application_grant_20260910_backup
                (grant_id, status, is_deleted, deleted_time, deleted_id)
            SELECT id, status, is_deleted, deleted_time, deleted_id
            FROM control_user_application_grant
            """
        )
    )

    # A legacy soft delete represented revocation. Restore every binding row so
    # the new ledger can express the current desired state without creating a
    # second row that would violate the binding unique key.
    op.execute(
        sa.text(
            """
            UPDATE control_user_application_grant
            SET desired_state = CASE
                    WHEN is_deleted = false AND status = 0 THEN 'active'
                    ELSE 'inactive'
                END,
                sync_status = CASE
                    WHEN is_deleted = false AND status = 0 THEN 'pending'
                    ELSE 'succeeded'
                END,
                sync_version = 1,
                status = CASE
                    WHEN is_deleted = false AND status = 0 THEN 0
                    ELSE 1
                END,
                is_deleted = false,
                deleted_time = NULL,
                deleted_id = NULL
            """
        )
    )

    op.create_check_constraint(
        "ck_control_user_application_grant_desired_state",
        "control_user_application_grant",
        "desired_state IN ('active', 'inactive')",
    )
    op.create_check_constraint(
        "ck_control_user_application_grant_sync_status",
        "control_user_application_grant",
        "sync_status IN ('pending', 'processing', 'succeeded', 'failed')",
    )
    op.create_check_constraint(
        "ck_control_user_application_grant_sync_version",
        "control_user_application_grant",
        "sync_version >= 0",
    )
    op.create_check_constraint(
        "ck_control_user_application_grant_retry_count",
        "control_user_application_grant",
        "retry_count >= 0",
    )
    op.create_check_constraint(
        "ck_control_user_application_grant_status",
        "control_user_application_grant",
        "status IN (0, 1)",
    )
    op.create_index(
        "ix_control_user_application_grant_desired_state",
        "control_user_application_grant",
        ["desired_state"],
        unique=False,
    )
    op.create_index(
        "ix_control_user_application_grant_sync_status",
        "control_user_application_grant",
        ["sync_status"],
        unique=False,
    )
    op.create_index(
        "ix_control_user_application_grant_last_event_id",
        "control_user_application_grant",
        ["last_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_control_user_application_grant_active_execution_token",
        "control_user_application_grant",
        ["active_execution_token"],
        unique=False,
    )
    op.create_index(
        "ix_control_user_application_grant_next_retry_at",
        "control_user_application_grant",
        ["next_retry_at"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_control_tenant_application_id_app_site",
        "control_tenant_application",
        ["id", "application_id", "site_id"],
    )
    op.create_unique_constraint(
        "uq_control_user_application_grant_id_binding_site",
        "control_user_application_grant",
        ["id", "tenant_application_id", "site_id"],
    )

    op.create_table(
        "control_user_entitlement_ticket",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("created_time", sa.DateTime(), nullable=False),
        sa.Column("updated_time", sa.DateTime(), nullable=False),
        sa.Column("deleted_time", sa.DateTime(), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("tenant_application_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("sync_version", sa.Integer(), nullable=False),
        sa.Column("desired_state", sa.String(length=16), server_default="inactive", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="issued", nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('issued', 'redeemed', 'expired')",
            name="ck_control_user_entitlement_ticket_status",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_control_user_entitlement_ticket_expiry"),
        sa.CheckConstraint("sync_version > 0", name="ck_control_user_entitlement_ticket_sync_version"),
        sa.CheckConstraint(
            "desired_state IN ('active', 'inactive')",
            name="ck_control_user_entitlement_ticket_desired_state",
        ),
        sa.CheckConstraint(
            "(status = 'redeemed' AND redeemed_at IS NOT NULL) OR "
            "(status IN ('issued', 'expired') AND redeemed_at IS NULL)",
            name="ck_control_user_entitlement_ticket_redemption",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "tenant_application_id", "site_id"],
            [
                "control_user_application_grant.id",
                "control_user_application_grant.tenant_application_id",
                "control_user_application_grant.site_id",
            ],
            name="fk_control_user_entitlement_ticket_grant_binding_site",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_application_id", "application_id", "site_id"],
            [
                "control_tenant_application.id",
                "control_tenant_application.application_id",
                "control_tenant_application.site_id",
            ],
            name="fk_control_user_entitlement_ticket_opening_app_site",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash", name="uq_control_user_entitlement_ticket_code_hash"),
        comment="中控一次性用户授权同步票据",
    )
    _create_model_indexes("control_user_entitlement_ticket")
    op.create_index("ix_control_user_entitlement_ticket_grant_id", "control_user_entitlement_ticket", ["grant_id"])
    op.create_index(
        "ix_control_user_entitlement_ticket_tenant_application_id",
        "control_user_entitlement_ticket",
        ["tenant_application_id"],
    )
    op.create_index(
        "ix_control_user_entitlement_ticket_application_id", "control_user_entitlement_ticket", ["application_id"]
    )
    op.create_index("ix_control_user_entitlement_ticket_site_id", "control_user_entitlement_ticket", ["site_id"])
    op.create_index("ix_control_user_entitlement_ticket_event_id", "control_user_entitlement_ticket", ["event_id"])
    op.create_index("ix_control_user_entitlement_ticket_status", "control_user_entitlement_ticket", ["status"])
    op.create_index("ix_control_user_entitlement_ticket_expires_at", "control_user_entitlement_ticket", ["expires_at"])


def downgrade() -> None:
    op.drop_table("control_user_entitlement_ticket")

    # Restore the exact legacy snapshot only when the upgraded ledger row was
    # never acted on. Once its version/event/state changed, project the current
    # desired state back to legacy status + soft-delete semantics so downgrade
    # cannot resurrect a revoked user or revoke a newly granted user.
    op.execute(
        sa.text(
            """
            UPDATE control_user_application_grant AS current_grant
            SET status = CASE
                    WHEN current_grant.sync_version = 1
                         AND current_grant.last_event_id IS NULL
                         AND current_grant.active_execution_token IS NULL
                         AND current_grant.retry_count = 0
                         AND current_grant.last_attempt_at IS NULL
                         AND current_grant.last_synced_at IS NULL
                         AND current_grant.desired_state = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'active'
                             ELSE 'inactive'
                         END
                         AND current_grant.sync_status = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'pending'
                             ELSE 'succeeded'
                         END
                    THEN backup.status
                    WHEN current_grant.desired_state = 'active' THEN 0
                    ELSE 1
                END,
                is_deleted = CASE
                    WHEN current_grant.sync_version = 1
                         AND current_grant.last_event_id IS NULL
                         AND current_grant.active_execution_token IS NULL
                         AND current_grant.retry_count = 0
                         AND current_grant.last_attempt_at IS NULL
                         AND current_grant.last_synced_at IS NULL
                         AND current_grant.desired_state = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'active'
                             ELSE 'inactive'
                         END
                         AND current_grant.sync_status = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'pending'
                             ELSE 'succeeded'
                         END
                    THEN backup.is_deleted
                    WHEN current_grant.desired_state = 'active' THEN false
                    ELSE true
                END,
                deleted_time = CASE
                    WHEN current_grant.sync_version = 1
                         AND current_grant.last_event_id IS NULL
                         AND current_grant.active_execution_token IS NULL
                         AND current_grant.retry_count = 0
                         AND current_grant.last_attempt_at IS NULL
                         AND current_grant.last_synced_at IS NULL
                         AND current_grant.desired_state = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'active'
                             ELSE 'inactive'
                         END
                         AND current_grant.sync_status = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'pending'
                             ELSE 'succeeded'
                         END
                    THEN backup.deleted_time
                    WHEN current_grant.desired_state = 'active' THEN NULL
                    ELSE COALESCE(current_grant.deleted_time, CURRENT_TIMESTAMP)
                END,
                deleted_id = CASE
                    WHEN current_grant.sync_version = 1
                         AND current_grant.last_event_id IS NULL
                         AND current_grant.active_execution_token IS NULL
                         AND current_grant.retry_count = 0
                         AND current_grant.last_attempt_at IS NULL
                         AND current_grant.last_synced_at IS NULL
                         AND current_grant.desired_state = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'active'
                             ELSE 'inactive'
                         END
                         AND current_grant.sync_status = CASE
                             WHEN backup.is_deleted = false AND backup.status = 0 THEN 'pending'
                             ELSE 'succeeded'
                         END
                    THEN backup.deleted_id
                    WHEN current_grant.desired_state = 'active' THEN NULL
                    ELSE COALESCE(current_grant.deleted_id, current_grant.updated_id)
                END
            FROM control_user_application_grant_20260910_backup AS backup
            WHERE current_grant.id = backup.grant_id
            """
        )
    )
    op.drop_table("control_user_application_grant_20260910_backup")
    op.drop_constraint(
        "uq_control_user_application_grant_id_binding_site",
        "control_user_application_grant",
        type_="unique",
    )
    op.drop_constraint(
        "uq_control_tenant_application_id_app_site",
        "control_tenant_application",
        type_="unique",
    )

    op.drop_index("ix_control_user_application_grant_next_retry_at", table_name="control_user_application_grant")
    op.drop_index("ix_control_user_application_grant_active_execution_token", table_name="control_user_application_grant")
    op.drop_index("ix_control_user_application_grant_last_event_id", table_name="control_user_application_grant")
    op.drop_index("ix_control_user_application_grant_sync_status", table_name="control_user_application_grant")
    op.drop_index("ix_control_user_application_grant_desired_state", table_name="control_user_application_grant")
    op.drop_constraint(
        "ck_control_user_application_grant_status",
        "control_user_application_grant",
        type_="check",
    )
    op.drop_constraint(
        "ck_control_user_application_grant_retry_count",
        "control_user_application_grant",
        type_="check",
    )
    op.drop_constraint(
        "ck_control_user_application_grant_sync_version",
        "control_user_application_grant",
        type_="check",
    )
    op.drop_constraint(
        "ck_control_user_application_grant_sync_status",
        "control_user_application_grant",
        type_="check",
    )
    op.drop_constraint(
        "ck_control_user_application_grant_desired_state",
        "control_user_application_grant",
        type_="check",
    )
    for column_name in (
        "last_synced_at",
        "last_attempt_at",
        "next_retry_at",
        "retry_count",
        "last_error_message",
        "last_error_code",
        "active_execution_token",
        "last_event_id",
        "sync_version",
        "sync_status",
        "desired_state",
    ):
        op.drop_column("control_user_application_grant", column_name)

    op.drop_constraint(
        "ck_control_application_entitlement_sync_timeout",
        "control_application",
        type_="check",
    )
    op.drop_column("control_application", "entitlement_sync_timeout_seconds")
    op.drop_column("control_application", "entitlement_sync_enabled")
    op.drop_column("control_application", "entitlement_sync_url")
