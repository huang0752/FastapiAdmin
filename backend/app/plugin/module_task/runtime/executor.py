"""Celery Worker 的数据库租约抢占与通用处理器执行器。"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.setting import settings
from app.core.logger import logger

from ..business.task.model import BusinessTaskModel
from .context import BusinessTaskContext, build_background_auth, utc_now
from .database import worker_async_session
from .exceptions import BusinessTaskCancelled, InvalidBackgroundActorError, RetryableBusinessTaskError
from .registry import BusinessTaskRegistry, UnknownBusinessTaskHandlerError, business_task_registry


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    status: str
    retry_countdown: int | None = None
    retry_budget: int | None = None
    retry_attempt: int | None = None


class BusinessTaskExecutor:
    """以 PostgreSQL 为事实来源的至少一次消费执行器。"""

    def __init__(
        self,
        *,
        registry: BusinessTaskRegistry = business_task_registry,
        session_factory: async_sessionmaker[AsyncSession] = worker_async_session,
    ) -> None:
        self.registry = registry
        self.session_factory = session_factory

    async def execute(self, business_task_id: int) -> ExecutionOutcome:
        claimed = await self._claim(business_task_id)
        if claimed is None:
            return ExecutionOutcome(status="noop")
        if isinstance(claimed, int):
            # 租约冲突不消耗数据库业务重试次数；允许 Celery 在当前计数上再试一次。
            return ExecutionOutcome(status="deferred", retry_countdown=claimed, retry_budget=1)
        task, execution_token = claimed
        try:
            definition = self.registry.get(task.handler_code or "")
            payload = definition.validate_payload(task.payload)
        except (UnknownBusinessTaskHandlerError, ValidationError, ValueError) as exc:
            await self._finish_failed(task.id, execution_token, error_code="INVALID_TASK", summary=str(exc))
            return ExecutionOutcome(status="failed")

        async with self.session_factory() as handler_db:
            try:
                auth = await build_background_auth(
                    handler_db,
                    tenant_id=task.tenant_id,
                    actor_user_id=task.created_id,
                    required_permissions=definition.required_permissions,
                )
                context = BusinessTaskContext(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    actor_user_id=task.created_id or 0,
                    trace_id=task.trace_id or "",
                    execution_token=execution_token,
                    db=handler_db,
                    auth=auth,
                    session_factory=self.session_factory,
                )
                await context.check_cancelled()
                heartbeat_task = asyncio.create_task(self._heartbeat_loop(context))
                try:
                    result = await definition.handler(context, payload)
                finally:
                    heartbeat_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat_task
                await context.check_cancelled()
                safe_result = self._validate_result(result)
                await handler_db.commit()
            except BusinessTaskCancelled:
                await handler_db.rollback()
                await self._finish_canceled(task.id, execution_token)
                return ExecutionOutcome(status="canceled")
            except InvalidBackgroundActorError as exc:
                await handler_db.rollback()
                await self._finish_failed(task.id, execution_token, error_code=exc.error_code, summary=str(exc))
                return ExecutionOutcome(status="failed")
            except Exception as exc:
                await handler_db.rollback()
                logger.exception("业务任务执行失败 task_id={} trace_id={}", task.id, task.trace_id or "-")
                retryable = isinstance(exc, (RetryableBusinessTaskError, *definition.retryable_exceptions))
                return await self._handle_failure(task, execution_token, exc, retryable=retryable)

        if await self._finish_success(task.id, execution_token, safe_result):
            return ExecutionOutcome(status="success")
        await self._finish_canceled(task.id, execution_token)
        return ExecutionOutcome(status="canceled")

    @staticmethod
    async def _heartbeat_loop(context: BusinessTaskContext) -> None:
        while True:
            await asyncio.sleep(settings.CELERY_HEARTBEAT_INTERVAL)
            if not await context.heartbeat():
                return

    async def _claim(self, task_id: int) -> tuple[BusinessTaskModel, str] | int | None:
        now = utc_now()
        token = uuid4().hex
        async with self.session_factory() as db:
            existing = await db.get(BusinessTaskModel, task_id)
            if existing is None or existing.is_deleted or existing.status in {"success", "failed", "canceled"}:
                return None
            comparable_now = now
            if existing.lease_expires_at is not None and existing.lease_expires_at.tzinfo is None:
                comparable_now = now.replace(tzinfo=None)
            if existing.status == "running" and existing.lease_expires_at and existing.lease_expires_at > comparable_now:
                seconds = max(1, int((existing.lease_expires_at - comparable_now).total_seconds()) + 1)
                return seconds
            result = await db.execute(
                update(BusinessTaskModel)
                .where(
                    BusinessTaskModel.id == task_id,
                    BusinessTaskModel.is_deleted.is_(False),
                    or_(
                        BusinessTaskModel.status.in_(("pending", "enqueue_failed", "queued", "retrying")),
                        (
                            (BusinessTaskModel.status == "running")
                            & (BusinessTaskModel.lease_expires_at.is_not(None))
                            & (BusinessTaskModel.lease_expires_at <= now)
                        ),
                    ),
                )
                .values(
                    status="running",
                    execution_token=token,
                    lease_expires_at=now + timedelta(seconds=settings.CELERY_LEASE_SECONDS),
                    heartbeat_at=now,
                    started_at=func.coalesce(BusinessTaskModel.started_at, now),
                    attempt=BusinessTaskModel.attempt + 1,
                    error_code=None,
                    error=None,
                )
            )
            if not result.rowcount:
                await db.rollback()
                return None
            await db.commit()
            task = (await db.execute(select(BusinessTaskModel).where(BusinessTaskModel.id == task_id))).scalar_one()
            return task, token

    async def _handle_failure(self, task: BusinessTaskModel, token: str, exc: Exception, *, retryable: bool) -> ExecutionOutcome:
        summary = self._safe_error_summary(exc)
        if retryable and task.attempt <= task.max_retries:
            countdown = settings.CELERY_RETRY_BACKOFF * (2 ** max(0, task.attempt - 1))
            async with self.session_factory() as db:
                await db.execute(
                    update(BusinessTaskModel)
                    .where(
                        BusinessTaskModel.id == task.id,
                        BusinessTaskModel.status == "running",
                        BusinessTaskModel.execution_token == token,
                        BusinessTaskModel.cancel_requested_at.is_(None),
                    )
                    .values(
                        status="retrying",
                        execution_token=None,
                        lease_expires_at=None,
                        error_code=getattr(exc, "error_code", "RETRYABLE_FAILURE"),
                        error=summary,
                    )
                )
                await db.commit()
            return ExecutionOutcome(
                status="retrying",
                retry_countdown=countdown,
                retry_budget=task.max_retries - task.attempt + 1,
                retry_attempt=task.attempt,
            )
        await self._finish_failed(
            task.id,
            token,
            error_code="RETRIES_EXHAUSTED" if retryable else "BUSINESS_TASK_FAILED",
            summary=summary,
        )
        return ExecutionOutcome(status="failed")

    async def fail_retry_exhausted(self, business_task_id: int, *, expected_attempt: int) -> None:
        """Celery 拒绝继续重试时，收口已经进入 retrying 的数据库状态。"""
        now = utc_now()
        async with self.session_factory() as db:
            await db.execute(
                update(BusinessTaskModel)
                .where(
                    BusinessTaskModel.id == business_task_id,
                    BusinessTaskModel.status == "retrying",
                    BusinessTaskModel.attempt == expected_attempt,
                )
                .values(
                    status="failed",
                    finished_at=now,
                    execution_token=None,
                    lease_expires_at=None,
                    heartbeat_at=now,
                    error_code="RETRIES_EXHAUSTED",
                    error="Celery 已拒绝继续重试",
                )
            )
            await db.commit()

    async def _finish_success(self, task_id: int, token: str, result: dict | None) -> bool:
        now = utc_now()
        async with self.session_factory() as db:
            execution = await db.execute(
                update(BusinessTaskModel)
                .where(
                    BusinessTaskModel.id == task_id,
                    BusinessTaskModel.status == "running",
                    BusinessTaskModel.execution_token == token,
                    BusinessTaskModel.cancel_requested_at.is_(None),
                )
                .values(
                    status="success",
                    progress=100,
                    result=result,
                    finished_at=now,
                    execution_token=None,
                    lease_expires_at=None,
                    heartbeat_at=now,
                    error_code=None,
                    error=None,
                )
            )
            await db.commit()
            return bool(execution.rowcount)

    async def _finish_failed(self, task_id: int, token: str, *, error_code: str, summary: str) -> None:
        now = utc_now()
        async with self.session_factory() as db:
            await db.execute(
                update(BusinessTaskModel)
                .where(
                    BusinessTaskModel.id == task_id,
                    BusinessTaskModel.status == "running",
                    BusinessTaskModel.execution_token == token,
                )
                .values(
                    status="failed",
                    finished_at=now,
                    execution_token=None,
                    lease_expires_at=None,
                    heartbeat_at=now,
                    error_code=error_code,
                    error=self._safe_error_summary(summary),
                )
            )
            await db.commit()

    async def _finish_canceled(self, task_id: int, token: str) -> None:
        now = utc_now()
        async with self.session_factory() as db:
            await db.execute(
                update(BusinessTaskModel)
                .where(
                    BusinessTaskModel.id == task_id,
                    BusinessTaskModel.status == "running",
                    BusinessTaskModel.execution_token == token,
                )
                .values(
                    status="canceled",
                    finished_at=now,
                    execution_token=None,
                    lease_expires_at=None,
                    heartbeat_at=now,
                    error_code="TASK_CANCELED",
                    error="任务已安全取消",
                )
            )
            await db.commit()

    @staticmethod
    def _validate_result(result: dict | None) -> dict | None:
        if result is None:
            return None
        if not isinstance(result, dict):
            raise ValueError("业务任务处理器必须返回 JSON 对象或 None")
        encoded = json.dumps(result, ensure_ascii=False, default=lambda _: (_ for _ in ()).throw(TypeError()))
        if len(encoded.encode("utf-8")) > 1024 * 1024:
            raise ValueError("业务任务结果摘要超过 1 MiB 限制")
        return result

    @staticmethod
    def _safe_error_summary(error: object) -> str:
        text = str(error).replace("\r", " ").replace("\n", " ").strip()
        sensitive_markers = ("authorization", "bearer ", "password", "secret", "token=", "api_key")
        if any(marker in text.lower() for marker in sensitive_markers):
            return "任务执行失败，详细信息仅记录于服务端日志"
        return (text or "任务执行失败")[:500]
