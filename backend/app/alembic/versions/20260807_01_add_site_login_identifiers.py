"""Add Site-scoped username, email and mobile login identifiers.

Revision ID: 20260807_01
Revises: 20260730_02
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260807_01"
down_revision: str | None = "20260730_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_USER_IDENTIFIERS_SQL = """
WITH user_sites AS (
    SELECT u.id AS user_id, t.site_id
    FROM sys_user AS u
    JOIN platform_tenant AS t ON t.id = u.tenant_id
    WHERE u.is_deleted = false
    UNION
    SELECT u.id AS user_id, t.site_id
    FROM sys_user AS u
    JOIN platform_user_tenant AS ut ON ut.user_id = u.id
    JOIN platform_tenant AS t ON t.id = ut.tenant_id
    WHERE u.is_deleted = false
), identifiers AS (
    SELECT us.site_id, u.id AS user_id, 'username' AS identifier_type,
           lower(trim(u.username)) AS normalized_value
    FROM sys_user AS u
    JOIN user_sites AS us ON us.user_id = u.id
    WHERE u.is_deleted = false
    UNION ALL
    SELECT us.site_id, u.id, 'email', lower(trim(u.email))
    FROM sys_user AS u
    JOIN user_sites AS us ON us.user_id = u.id
    WHERE u.is_deleted = false AND u.email IS NOT NULL AND trim(u.email) <> ''
    UNION ALL
    SELECT us.site_id, u.id, 'mobile', trim(u.mobile)
    FROM sys_user AS u
    JOIN user_sites AS us ON us.user_id = u.id
    WHERE u.is_deleted = false AND u.mobile IS NOT NULL AND trim(u.mobile) <> ''
)
"""


def _mask_identifier(value: str) -> str:
    if "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    if value.isdigit() and len(value) >= 7:
        return f"{value[:3]}****{value[-4:]}"
    return f"{value[:2]}***"


def upgrade() -> None:
    bind = op.get_bind()
    conflict = bind.execute(
        sa.text(
            _USER_IDENTIFIERS_SQL
            + """
            SELECT site_id, normalized_value, count(*) AS conflict_count
            FROM identifiers
            GROUP BY site_id, normalized_value
            HAVING count(*) > 1
            ORDER BY site_id, normalized_value
            LIMIT 1
            """
        )
    ).first()
    if conflict:
        raise RuntimeError(
            "站点内登录标识冲突，迁移已停止，请先处理: "
            f"site_id={conflict.site_id}, value={_mask_identifier(conflict.normalized_value)}, "
            f"count={conflict.conflict_count}"
        )

    op.create_table(
        "sys_user_login_identifier",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("identifier_type", sa.String(length=16), nullable=False),
        sa.Column("normalized_value", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["platform_site.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["sys_user.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "site_id",
            "normalized_value",
            name="uq_user_login_identifier_site_value",
        ),
        sa.UniqueConstraint(
            "site_id",
            "user_id",
            "identifier_type",
            name="uq_user_login_identifier_site_user_type",
        ),
        comment="用户登录标识表",
    )
    op.create_index(
        "ix_sys_user_login_identifier_site_id",
        "sys_user_login_identifier",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_user_login_identifier_user_id",
        "sys_user_login_identifier",
        ["user_id"],
        unique=False,
    )
    bind.execute(
        sa.text(
            _USER_IDENTIFIERS_SQL
            + """
            INSERT INTO sys_user_login_identifier
                (site_id, user_id, identifier_type, normalized_value)
            SELECT site_id, user_id, identifier_type, normalized_value
            FROM identifiers
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sys_user_login_identifier_user_id",
        table_name="sys_user_login_identifier",
    )
    op.drop_index(
        "ix_sys_user_login_identifier_site_id",
        table_name="sys_user_login_identifier",
    )
    op.drop_table("sys_user_login_identifier")
