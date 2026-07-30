"""Add reliable Celery runtime fields to business tasks.

Revision ID: 20260730_01
Revises: 20260729_f01
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_01"
down_revision: str | None = "20260729_f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_business_task", sa.Column("handler_code", sa.String(length=128), nullable=True, comment="服务端任务处理器编码"))
    op.add_column("task_business_task", sa.Column("queue", sa.String(length=128), nullable=True, comment="Celery队列"))
    op.add_column("task_business_task", sa.Column("external_task_id", sa.String(length=128), nullable=True, comment="Celery任务ID"))
    op.add_column("task_business_task", sa.Column("idempotency_key", sa.String(length=128), nullable=True, comment="租户内幂等键"))
    op.add_column("task_business_task", sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False, comment="已抢占执行次数"))
    op.add_column("task_business_task", sa.Column("max_retries", sa.Integer(), server_default=sa.text("0"), nullable=False, comment="最大重试次数"))
    op.add_column("task_business_task", sa.Column("execution_token", sa.String(length=64), nullable=True, comment="当前执行租约令牌"))
    op.add_column("task_business_task", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True, comment="执行租约过期时间"))
    op.add_column("task_business_task", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True, comment="Worker最后心跳时间"))
    op.add_column("task_business_task", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True, comment="首次开始时间"))
    op.add_column("task_business_task", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间"))
    op.add_column("task_business_task", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True, comment="协作取消请求时间"))
    op.add_column("task_business_task", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True, comment="最近发布成功时间"))
    op.add_column("task_business_task", sa.Column("enqueue_failed_at", sa.DateTime(timezone=True), nullable=True, comment="最近发布失败时间"))
    op.add_column("task_business_task", sa.Column("error_code", sa.String(length=64), nullable=True, comment="安全错误编码"))
    op.add_column("task_business_task", sa.Column("trace_id", sa.String(length=64), nullable=True, comment="内部追踪ID"))
    op.create_index("ix_task_business_task_handler_code", "task_business_task", ["handler_code"], unique=False)
    op.create_index("ix_task_business_task_queue", "task_business_task", ["queue"], unique=False)
    op.create_index("ix_task_business_task_execution_token", "task_business_task", ["execution_token"], unique=False)
    op.create_index("ix_task_business_task_lease_expires_at", "task_business_task", ["lease_expires_at"], unique=False)
    op.create_index("ix_task_business_task_trace_id", "task_business_task", ["trace_id"], unique=False)
    op.create_index("ix_business_task_recovery", "task_business_task", ["status", "lease_expires_at"], unique=False)
    op.create_index("ix_business_task_tenant_status_created", "task_business_task", ["tenant_id", "status", "created_time"], unique=False)
    op.create_unique_constraint("uq_business_task_tenant_idempotency", "task_business_task", ["tenant_id", "idempotency_key"])
    op.create_unique_constraint("uq_business_task_tenant_external_task", "task_business_task", ["tenant_id", "external_task_id"])
    op.create_check_constraint("ck_business_task_progress", "task_business_task", "progress >= 0 AND progress <= 100")
    op.create_check_constraint("ck_business_task_attempt", "task_business_task", "attempt >= 0")
    op.create_check_constraint("ck_business_task_max_retries", "task_business_task", "max_retries >= 0")


def downgrade() -> None:
    op.execute("UPDATE task_business_task SET status = 'pending' WHERE status IN ('enqueue_failed', 'queued', 'retrying')")
    op.drop_constraint("ck_business_task_max_retries", "task_business_task", type_="check")
    op.drop_constraint("ck_business_task_attempt", "task_business_task", type_="check")
    op.drop_constraint("ck_business_task_progress", "task_business_task", type_="check")
    op.drop_constraint("uq_business_task_tenant_external_task", "task_business_task", type_="unique")
    op.drop_constraint("uq_business_task_tenant_idempotency", "task_business_task", type_="unique")
    op.drop_index("ix_business_task_recovery", table_name="task_business_task")
    op.drop_index("ix_business_task_tenant_status_created", table_name="task_business_task")
    op.drop_index("ix_task_business_task_trace_id", table_name="task_business_task")
    op.drop_index("ix_task_business_task_lease_expires_at", table_name="task_business_task")
    op.drop_index("ix_task_business_task_execution_token", table_name="task_business_task")
    op.drop_index("ix_task_business_task_queue", table_name="task_business_task")
    op.drop_index("ix_task_business_task_handler_code", table_name="task_business_task")
    for column in (
        "trace_id",
        "error_code",
        "enqueue_failed_at",
        "published_at",
        "cancel_requested_at",
        "finished_at",
        "started_at",
        "heartbeat_at",
        "lease_expires_at",
        "execution_token",
        "max_retries",
        "attempt",
        "idempotency_key",
        "external_task_id",
        "queue",
        "handler_code",
    ):
        op.drop_column("task_business_task", column)
