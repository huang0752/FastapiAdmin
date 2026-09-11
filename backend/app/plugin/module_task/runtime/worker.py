"""独立 Celery Worker 入口。

启动：``uv run celery -A app.plugin.module_task.runtime.worker:celery_app worker``
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

from celery import signals
from celery.exceptions import MaxRetriesExceededError, Reject

from app.config.setting import settings
from app.core.assembly import get_assembly
from app.core.control_features import validate_control_capabilities
from app.core.logger import logger

from .celery_app import create_celery_app
from .dispatcher import CELERY_EXECUTE_TASK
from .executor import BusinessTaskExecutor
from .loader import load_business_task_modules
from .registry import business_task_registry

validate_control_capabilities(get_assembly(), settings)
celery_app = create_celery_app()
# 在 Celery 完成 Worker 启动前导入并校验所有 Assembly 任务模块；导入失败、
# payload schema 错误或 handler_code 冲突会直接阻止 Worker 启动。
_loaded_task_modules = load_business_task_modules()


def _safe_broker_summary(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or "unknown"
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


@signals.worker_init.connect
def initialize_business_task_worker(**_kwargs) -> None:
    assembly = get_assembly()
    logger.info("✅ Celery Worker Assembly: {}", assembly.name)
    logger.info("✅ Celery Worker enabled plugins: {}", ",".join(assembly.enabled_plugins) or "all")
    logger.info("✅ Celery Worker task modules: {}", ",".join(_loaded_task_modules) or "-")
    logger.info("✅ Celery Worker handlers: {}", ",".join(item.handler_code for item in business_task_registry.all()) or "-")
    logger.info("✅ Celery Worker broker: {}", _safe_broker_summary(settings.CELERY_BROKER_URL))
    logger.info("✅ Celery Worker default queue: {}", settings.CELERY_DEFAULT_QUEUE)


@celery_app.task(bind=True, name=CELERY_EXECUTE_TASK, ignore_result=True)
def execute_business_task(self, business_task_id: int) -> None:
    executor = BusinessTaskExecutor()
    outcome = asyncio.run(executor.execute(business_task_id))
    if outcome.retry_countdown is not None:
        if outcome.retry_budget is None or outcome.retry_budget < 1:
            raise RuntimeError("执行器返回了无效的 Celery 重试预算")
        celery_max_retries = self.request.retries + outcome.retry_budget
        try:
            retry_signal = self.retry(countdown=outcome.retry_countdown, max_retries=celery_max_retries)
        except MaxRetriesExceededError:
            if outcome.retry_attempt is not None:
                logger.error("Celery 重试次数已耗尽，收口数据库任务 task_id={}", business_task_id)
                asyncio.run(executor.fail_retry_exhausted(business_task_id, expected_attempt=outcome.retry_attempt))
            return
        except Reject:
            if outcome.retry_attempt is not None:
                logger.error("Celery 重试消息发布失败，等待恢复扫描 task_id={}", business_task_id)
                asyncio.run(executor.mark_retry_enqueue_failed(business_task_id, expected_attempt=outcome.retry_attempt))
            return
        raise retry_signal
