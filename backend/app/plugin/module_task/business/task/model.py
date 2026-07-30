from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class BusinessTaskModel(ModelMixin, TenantMixin, UserMixin):
    """通用业务长任务表。"""

    __tablename__: str = "task_business_task"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_business_task_tenant_idempotency"),
        UniqueConstraint("tenant_id", "external_task_id", name="uq_business_task_tenant_external_task"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_business_task_progress"),
        CheckConstraint("attempt >= 0", name="ck_business_task_attempt"),
        CheckConstraint("max_retries >= 0", name="ck_business_task_max_retries"),
        Index("ix_business_task_recovery", "status", "lease_expires_at"),
        Index("ix_business_task_tenant_status_created", "tenant_id", "status", "created_time"),
        {"comment": "通用业务长任务表", "extend_existing": True},
    )
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    module: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="业务模块")
    biz_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="业务类型")
    biz_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, comment="业务对象ID")
    title: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="任务标题")
    handler_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, comment="服务端任务处理器编码")
    queue: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True, comment="Celery队列")
    external_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="Celery任务ID")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="租户内幂等键")
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True, comment="任务状态")
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="进度百分比")
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="已抢占执行次数")
    max_retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="最大重试次数")
    execution_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="当前执行租约令牌")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True, comment="执行租约过期时间")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="Worker最后心跳时间")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="首次开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="完成时间")
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="协作取消请求时间")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近发布成功时间")
    enqueue_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近发布失败时间")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="安全错误编码")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="内部追踪ID")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="任务输入")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="任务结果")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="错误信息")
    is_demo: Mapped[bool] = mapped_column(default=False, nullable=False, index=True, comment="是否试用数据")
    demo_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="试用数据批次ID")
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="备注")
