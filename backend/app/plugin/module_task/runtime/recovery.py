"""APScheduler 定时触发的轻量待发布恢复扫描。"""

from __future__ import annotations

from app.core.logger import logger

from .dispatcher import BusinessTaskDispatcher


async def recover_business_task_publications() -> None:
    recovered = await BusinessTaskDispatcher().recover_pending(limit=100)
    if recovered:
        logger.info("✅ 已恢复投递 {} 个业务任务", recovered)


def install_business_task_recovery(scheduler) -> None:
    scheduler.add_job(
        recover_business_task_publications,
        "interval",
        seconds=30,
        id="system_business_task_publication_recovery",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        jobstore="memory",
    )
