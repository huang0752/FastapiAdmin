from datetime import UTC, datetime
from uuid import uuid4

from fastapi import status

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .crud import BusinessTaskCRUD
from .schema import (
    BusinessTaskActionOutSchema,
    BusinessTaskCreateSchema,
    BusinessTaskDiagnosticOutSchema,
    BusinessTaskOutSchema,
    BusinessTaskQueryParam,
    BusinessTaskUpdateSchema,
    DemoBatchCleanOutSchema,
    DemoBatchOutSchema,
    DemoBatchTriggerSchema,
)


class BusinessTaskService:
    """通用业务长任务服务。"""

    _TERMINAL_STATUSES = {"success", "failed", "canceled"}

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth
        self.crud = BusinessTaskCRUD(auth)

    async def create(self, data: BusinessTaskCreateSchema) -> BusinessTaskOutSchema:
        obj = await self.crud.create(data=data)
        return BusinessTaskOutSchema.model_validate(obj)

    async def detail(self, id: int) -> BusinessTaskOutSchema:
        return await self.crud.get_or_404(id=id, out_schema=BusinessTaskOutSchema)

    async def diagnostic(self, id: int) -> BusinessTaskDiagnosticOutSchema:
        return await self.crud.get_or_404(id=id, out_schema=BusinessTaskDiagnosticOutSchema)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BusinessTaskQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict:
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=BusinessTaskOutSchema,
        )

    async def update_status(self, id: int, data: BusinessTaskUpdateSchema) -> BusinessTaskOutSchema:
        """兼容内部记录更新；不再通过 HTTP 暴露。"""
        current = await self.crud.get_or_404(id=id)
        next_status = data.status or current.status
        from app.plugin.module_task.runtime.state import InvalidBusinessTaskProgress, InvalidBusinessTaskTransition, ensure_progress, ensure_transition

        try:
            ensure_transition(current.status, next_status)
            if data.progress is not None:
                ensure_progress(current.progress, data.progress)
        except (InvalidBusinessTaskTransition, InvalidBusinessTaskProgress) as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_400_BAD_REQUEST)
        update_obj = await self.crud.update(id=id, data=data)
        return BusinessTaskOutSchema.model_validate(update_obj)

    async def cancel(self, id: int) -> BusinessTaskActionOutSchema:
        current = await self.crud.get_or_404(id=id)
        if current.status in self._TERMINAL_STATUSES:
            raise CustomException(msg="终态任务不能取消", status_code=status.HTTP_400_BAD_REQUEST)
        now = datetime.now(UTC)
        if current.status in {"pending", "enqueue_failed", "queued", "retrying"}:
            current.status = "canceled"
            current.cancel_requested_at = now
            current.finished_at = now
        elif current.status == "running":
            from app.plugin.module_task.runtime.loader import load_business_task_modules
            from app.plugin.module_task.runtime.registry import business_task_registry

            load_business_task_modules()
            try:
                definition = business_task_registry.get(current.handler_code or "")
            except LookupError:
                raise CustomException(msg="任务处理器不可用，无法协作取消", status_code=status.HTTP_409_CONFLICT)
            if not definition.supports_cancel:
                raise CustomException(msg="任务处理器不支持运行中取消", status_code=status.HTTP_409_CONFLICT)
            current.cancel_requested_at = now
        await self.auth.db.flush()
        await self.auth.db.refresh(current)
        return BusinessTaskActionOutSchema.model_validate(current)

    async def retry(self, id: int) -> BusinessTaskActionOutSchema:
        current = await self.crud.get_or_404(id=id)
        if not current.handler_code:
            raise CustomException(msg="历史任务没有可重投处理器", status_code=status.HTTP_409_CONFLICT)
        from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher

        try:
            task = await BusinessTaskDispatcher().retry_existing(task_id=current.id, tenant_id=current.tenant_id)
        except (LookupError, ValueError) as exc:
            raise CustomException(msg=str(exc), status_code=status.HTTP_409_CONFLICT)
        return BusinessTaskActionOutSchema.model_validate(task)


class DemoBatchRegistry:
    """试用数据初始化处理器注册表。"""

    _handlers: dict[str, object] = {}

    @classmethod
    def register(cls, module: str, handler: object) -> None:
        cls._handlers[module] = handler

    @classmethod
    def get(cls, module: str) -> object | None:
        return cls._handlers.get(module)


class DemoBatchService:
    """试用数据初始化任务框架。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def trigger(self, data: DemoBatchTriggerSchema) -> DemoBatchOutSchema:
        demo_batch_id = f"demo_{data.module}_{uuid4().hex[:12]}"
        task = await BusinessTaskService(self.auth).create(
            BusinessTaskCreateSchema(
                module=data.module,
                biz_type="demo_batch_init",
                biz_id=demo_batch_id,
                title=f"{data.module} 试用数据初始化",
                payload={"scenario": data.scenario, **(data.payload or {})},
                is_demo=True,
                demo_batch_id=demo_batch_id,
            )
        )
        return DemoBatchOutSchema(
            module=data.module,
            scenario=data.scenario,
            demo_batch_id=demo_batch_id,
            task_id=task.id or 0,
        )

    async def clean(self, demo_batch_id: str) -> DemoBatchCleanOutSchema:
        module = demo_batch_id.split("_", 2)[1] if demo_batch_id.startswith("demo_") and "_" in demo_batch_id else "demo"
        task = await BusinessTaskService(self.auth).create(
            BusinessTaskCreateSchema(
                module=module,
                biz_type="demo_batch_clean",
                biz_id=demo_batch_id,
                title="试用数据清理",
                is_demo=True,
                demo_batch_id=demo_batch_id,
            )
        )
        return DemoBatchCleanOutSchema(demo_batch_id=demo_batch_id, task_id=task.id or 0)
