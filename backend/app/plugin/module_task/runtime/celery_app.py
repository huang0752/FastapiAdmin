"""Celery 应用工厂。

仅显式调用工厂或 Worker 入口时才构造 Celery 实例；导入 Web 应用不会连接
Broker，也不会加载插件任务处理器。
"""

from __future__ import annotations

from celery import Celery

from app.config.setting import settings
from app.core.assembly import is_plugin_enabled


class CeleryRuntimeDisabledError(RuntimeError):
    """当前装配未启用 Celery 业务任务运行时。"""


def ensure_runtime_enabled() -> None:
    if not is_plugin_enabled("module_task"):
        raise CeleryRuntimeDisabledError("当前 Assembly 未启用 module_task")
    if not settings.CELERY_ENABLED:
        raise CeleryRuntimeDisabledError("CELERY_ENABLED 未开启")


def create_celery_app() -> Celery:
    """按当前 Settings 构造无结果后端的安全 Celery 应用。"""
    ensure_runtime_enabled()
    app = Celery("fastapiadmin-business-tasks", broker=settings.CELERY_BROKER_URL, backend=None)
    app.conf.update(
        task_default_queue=settings.CELERY_DEFAULT_QUEUE,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
        worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
        task_acks_late=settings.CELERY_TASK_ACKS_LATE,
        task_reject_on_worker_lost=settings.CELERY_TASK_REJECT_ON_WORKER_LOST,
        task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
        task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
        broker_transport_options={
            "global_keyprefix": settings.CELERY_BROKER_KEY_PREFIX,
            "visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT,
        },
        broker_connection_retry_on_startup=True,
        enable_utc=True,
        timezone="Asia/Shanghai",
    )
    return app
