"""Celery Broker 与 Worker 分层健康诊断。"""

from __future__ import annotations

import asyncio

from app.config.setting import settings
from app.core.assembly import is_plugin_enabled

from ..business.task.schema import BusinessTaskHealthOutSchema
from .celery_app import create_celery_app


async def get_business_task_health(timeout: float = 1.0) -> BusinessTaskHealthOutSchema:
    if not is_plugin_enabled("module_task"):
        return BusinessTaskHealthOutSchema(status="module_task_disabled", detail="当前 Assembly 未启用 module_task")
    if not settings.CELERY_ENABLED:
        return BusinessTaskHealthOutSchema(status="celery_disabled", detail="Celery 业务任务运行时未启用")
    app = create_celery_app()
    try:
        await asyncio.wait_for(asyncio.to_thread(_check_broker, app), timeout=timeout)
    except Exception:
        return BusinessTaskHealthOutSchema(status="broker_unreachable", detail="Celery Broker 不可达")
    try:
        replies = await asyncio.wait_for(asyncio.to_thread(app.control.inspect(timeout=timeout).ping), timeout=timeout + 0.5)
    except Exception:
        replies = None
    worker_count = len(replies or {})
    if not worker_count:
        return BusinessTaskHealthOutSchema(
            status="broker_reachable_no_worker",
            broker_reachable=True,
            detail="Broker 可达，但未发现可用 Worker",
        )
    return BusinessTaskHealthOutSchema(
        status="worker_available",
        broker_reachable=True,
        worker_available=True,
        worker_count=worker_count,
    )


def _check_broker(app) -> None:
    with app.connection_for_write() as connection:
        connection.ensure_connection(max_retries=0)
