"""可靠业务任务投递器：数据库先落盘，Broker 后发布。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.setting import settings
from app.core.assembly import is_plugin_enabled
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session

from ..business.task.model import BusinessTaskModel
from .celery_app import CeleryRuntimeDisabledError, create_celery_app
from .context import utc_now
from .loader import load_business_task_modules
from .registry import BusinessTaskRegistry, UnknownBusinessTaskHandlerError, business_task_registry

CELERY_EXECUTE_TASK = "fastapiadmin.business_task.execute"


class BusinessTaskPublisher(Protocol):
    async def publish(self, *, business_task_id: int, celery_task_id: str, queue: str, soft_time_limit: int, hard_time_limit: int) -> None: ...


class CeleryBusinessTaskPublisher:
    """Celery publisher；消息正文只含 business_task_id。"""

    async def publish(self, *, business_task_id: int, celery_task_id: str, queue: str, soft_time_limit: int, hard_time_limit: int) -> None:
        app = create_celery_app()
        await asyncio.to_thread(
            app.send_task,
            CELERY_EXECUTE_TASK,
            args=[business_task_id],
            task_id=celery_task_id,
            queue=queue,
            serializer="json",
            ignore_result=True,
            soft_time_limit=soft_time_limit,
            time_limit=hard_time_limit,
            retry=True,
            retry_policy={"max_retries": 3, "interval_start": 0, "interval_step": 0.5, "interval_max": 1},
        )


@dataclass(slots=True)
class DispatchRequest:
    handler_code: str
    biz_type: str
    module: str | None = None
    payload: dict | None = None
    biz_id: str | None = None
    title: str | None = None
    queue: str | None = None
    max_retries: int | None = None
    idempotency_key: str | None = None
    description: str | None = None


class BusinessTaskDispatcher:
    """仅供服务端业务模块调用的可信投递入口。"""

    def __init__(
        self,
        *,
        registry: BusinessTaskRegistry = business_task_registry,
        publisher: BusinessTaskPublisher | None = None,
        session_factory: async_sessionmaker[AsyncSession] = async_db_session,
    ) -> None:
        self.registry = registry
        self.publisher = publisher or CeleryBusinessTaskPublisher()
        self.session_factory = session_factory

    async def dispatch(self, *, auth: AuthSchema, request: DispatchRequest) -> BusinessTaskModel:
        definition, payload = self._validate_request(auth=auth, request=request)
        task = await self._create_committed_task(
            tenant_id=auth.tenant_id,
            actor_user_id=auth.user.id,
            definition=definition,
            request=request,
            payload=payload,
        )
        if task.status not in {"pending", "enqueue_failed"}:
            return task
        return await self.publish_existing(task.id)

    async def prepare(self, *, auth: AuthSchema, request: DispatchRequest) -> BusinessTaskModel:
        """在调用者事务内写入 pending outbox；只 flush，不提交或发布。"""
        definition, payload = self._validate_request(auth=auth, request=request)
        if auth.db is None:
            raise PermissionError("事务内业务任务投递缺少数据库会话")
        return await self._prepare_task(
            db=auth.db,
            tenant_id=auth.tenant_id,
            actor_user_id=auth.user.id,
            definition=definition,
            request=request,
            payload=payload,
        )

    def _validate_request(self, *, auth: AuthSchema, request: DispatchRequest):
        if not is_plugin_enabled("module_task") or not settings.CELERY_ENABLED:
            raise CeleryRuntimeDisabledError("module_task Celery 业务任务运行时未启用")
        if auth.tenant_id is None or auth.user is None:
            raise PermissionError("业务任务投递必须来自可信 tenant/actor 上下文")
        if self.registry is business_task_registry:
            load_business_task_modules()
        definition = self.registry.get(request.handler_code)
        if request.module is not None and request.module != definition.module:
            raise ValueError("投递模块与服务端处理器注册信息不一致")
        if request.max_retries is not None and not 0 <= request.max_retries <= definition.max_retries:
            raise ValueError("投递重试次数只能在处理器上限内收紧")
        if request.queue is not None and (not request.queue.strip() or len(request.queue) > 128):
            raise ValueError("投递队列名称不合法")
        validated = definition.validate_payload(request.payload)
        payload = validated.model_dump(mode="json") if hasattr(validated, "model_dump") else validated
        return definition, payload

    async def _create_committed_task(self, *, tenant_id: int, actor_user_id: int, definition, request: DispatchRequest, payload: dict) -> BusinessTaskModel:
        async with self.session_factory() as db:
            task = await self._prepare_task(
                db=db,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                definition=definition,
                request=request,
                payload=payload,
            )
            await db.commit()
            await db.refresh(task)
            return task

    @staticmethod
    async def _find_idempotent_task(*, db: AsyncSession, tenant_id: int, idempotency_key: str) -> BusinessTaskModel | None:
        return (
            await db.execute(
                select(BusinessTaskModel).where(
                    BusinessTaskModel.tenant_id == tenant_id,
                    BusinessTaskModel.idempotency_key == idempotency_key,
                    BusinessTaskModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()

    async def _prepare_task(self, *, db: AsyncSession, tenant_id: int, actor_user_id: int, definition, request: DispatchRequest, payload: dict) -> BusinessTaskModel:
        if request.idempotency_key:
            existing = await self._find_idempotent_task(db=db, tenant_id=tenant_id, idempotency_key=request.idempotency_key)
            if existing:
                return existing
        task = BusinessTaskModel(
            tenant_id=tenant_id,
            created_id=actor_user_id,
            updated_id=actor_user_id,
            handler_code=definition.handler_code,
            module=definition.module,
            biz_type=request.biz_type,
            biz_id=request.biz_id,
            title=request.title,
            payload=payload,
            queue=(request.queue or definition.default_queue).strip(),
            idempotency_key=request.idempotency_key,
            status="pending",
            progress=0,
            attempt=0,
            max_retries=definition.max_retries if request.max_retries is None else request.max_retries,
            trace_id=uuid4().hex,
            description=request.description,
        )
        try:
            async with db.begin_nested():
                db.add(task)
                await db.flush()
                task.external_task_id = f"business-task-{task.id}"
                await db.flush()
        except IntegrityError:
            if not request.idempotency_key:
                raise
            existing = await self._find_idempotent_task(db=db, tenant_id=tenant_id, idempotency_key=request.idempotency_key)
            if existing is None:
                raise
            return existing
        return task

    async def publish_existing(self, task_id: int) -> BusinessTaskModel:
        async with self.session_factory() as db:
            task = await db.get(BusinessTaskModel, task_id)
            now = utc_now()
            expired_running = bool(
                task
                and task.status == "running"
                and task.lease_expires_at
                and task.lease_expires_at <= (now if task.lease_expires_at.tzinfo else now.replace(tzinfo=None))
            )
            if task is None or task.is_deleted or (task.status not in {"pending", "enqueue_failed"} and not expired_running):
                if task is None:
                    raise LookupError("业务任务不存在")
                return task
            try:
                definition = self.registry.get(task.handler_code or "")
            except UnknownBusinessTaskHandlerError:
                await db.execute(
                    update(BusinessTaskModel)
                    .where(
                        BusinessTaskModel.id == task.id,
                        or_(
                            BusinessTaskModel.status.in_(("pending", "enqueue_failed")),
                            (
                                (BusinessTaskModel.status == "running")
                                & (BusinessTaskModel.execution_token == task.execution_token)
                                & (BusinessTaskModel.lease_expires_at.is_not(None))
                                & (BusinessTaskModel.lease_expires_at <= now)
                            ),
                        ),
                    )
                    .values(
                        status="failed",
                        finished_at=now,
                        execution_token=None,
                        lease_expires_at=None,
                        error_code="UNKNOWN_HANDLER",
                        error="任务处理器未注册或已停用",
                    )
                )
                await db.commit()
                return (await db.execute(select(BusinessTaskModel).where(BusinessTaskModel.id == task.id))).scalar_one()
            try:
                await self.publisher.publish(
                    business_task_id=task.id,
                    celery_task_id=task.external_task_id or f"business-task-{task.id}",
                    queue=task.queue or definition.default_queue,
                    soft_time_limit=definition.soft_time_limit,
                    hard_time_limit=definition.hard_time_limit,
                )
            except Exception:
                await db.execute(
                    update(BusinessTaskModel)
                    .where(
                        BusinessTaskModel.id == task.id,
                        or_(
                            BusinessTaskModel.status.in_(("pending", "enqueue_failed")),
                            (
                                (BusinessTaskModel.status == "running")
                                & (BusinessTaskModel.lease_expires_at.is_not(None))
                                & (BusinessTaskModel.lease_expires_at <= now)
                            ),
                        ),
                    )
                    .values(
                        status="enqueue_failed",
                        enqueue_failed_at=now,
                        execution_token=None,
                        lease_expires_at=None,
                        error_code="BROKER_PUBLISH_FAILED",
                        error="任务暂未进入队列",
                    )
                )
                await db.commit()
                raise
            await db.execute(
                update(BusinessTaskModel)
                .where(BusinessTaskModel.id == task.id, BusinessTaskModel.status.in_(("pending", "enqueue_failed")))
                .values(status="queued", published_at=now, enqueue_failed_at=None, error_code=None, error=None)
            )
            await db.commit()
            return (await db.execute(select(BusinessTaskModel).where(BusinessTaskModel.id == task.id))).scalar_one()

    async def recover_pending(self, *, limit: int = 100) -> int:
        """扫描 commit 后未发布/发布失败任务并使用原 ID 安全重投。"""
        if self.registry is business_task_registry:
            load_business_task_modules()
        async with self.session_factory() as db:
            task_ids = (
                await db.execute(
                    select(BusinessTaskModel.id)
                    .where(
                        or_(
                            BusinessTaskModel.status.in_(("pending", "enqueue_failed")),
                            (
                                (BusinessTaskModel.status == "running")
                                & (BusinessTaskModel.lease_expires_at.is_not(None))
                                & (BusinessTaskModel.lease_expires_at <= utc_now())
                            ),
                        ),
                        BusinessTaskModel.handler_code.is_not(None),
                        BusinessTaskModel.is_deleted.is_(False),
                    )
                    .order_by(BusinessTaskModel.created_time)
                    .limit(limit)
                )
            ).scalars().all()
        published = 0
        for task_id in task_ids:
            try:
                await self.publish_existing(task_id)
            except Exception:
                continue
            published += 1
        return published

    async def retry_existing(self, *, task_id: int, tenant_id: int) -> BusinessTaskModel:
        """通过专用动作重投失败记录；普通状态机仍保持终态不可回退。"""
        if self.registry is business_task_registry:
            load_business_task_modules()
        async with self.session_factory() as db:
            task = (
                await db.execute(
                    select(BusinessTaskModel).where(
                        BusinessTaskModel.id == task_id,
                        BusinessTaskModel.tenant_id == tenant_id,
                        BusinessTaskModel.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                raise LookupError("业务任务不存在")
            definition = self.registry.get(task.handler_code or "")
            if task.status == "enqueue_failed":
                pass
            elif task.status == "failed" and task.attempt <= task.max_retries:
                task.status = "pending"
                task.finished_at = None
                task.error_code = None
                task.error = None
                task.execution_token = None
                task.lease_expires_at = None
                await db.commit()
            else:
                raise ValueError("当前任务状态或重试次数不允许重投")
            if not task.queue:
                task.queue = definition.default_queue
                await db.commit()
        return await self.publish_existing(task_id)
