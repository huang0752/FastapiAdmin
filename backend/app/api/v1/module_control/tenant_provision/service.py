"""Control tenant creation orchestration, provisioning state and worker logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal
from uuid import uuid4

import httpx
from fastapi import status
from pydantic import ValidationError
from sqlalchemy import and_, func, select, update

from app.api.v1.module_control.application_package.service import ControlApplicationPackageService
from app.api.v1.module_control.model import ControlApplicationModel, ControlTenantApplicationModel, ControlUserApplicationGrantModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.user.model import UserModel
from app.config.setting import settings
from app.core.assembly import is_plugin_enabled
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import is_tenant_package_active, require_platform_admin
from app.core.exceptions import CustomException
from app.plugin.module_task.business.task.model import BusinessTaskModel
from app.plugin.module_task.runtime.context import BusinessTaskContext
from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
from app.plugin.module_task.runtime.exceptions import RetryableBusinessTaskError

from .model import ControlTenantProvisionModel
from .schema import ControlTenantProvisionOutSchema, ControlTenantProvisionQueryParam, ControlTenantProvisionUpdateSchema
from .task_schema import (
    ControlTargetProvisionResult,
    ControlTenantProvisionTaskPayload,
    ControlTenantWithProvisionsCreateSchema,
    ControlTenantWithProvisionsOutSchema,
)
from .ticket_service import ControlProvisionTicketService


@dataclass(slots=True)
class ControlTenantProvisionCreateOutcome:
    result: ControlTenantWithProvisionsOutSchema
    task_ids: list[int]


@dataclass(frozen=True, slots=True)
class _TargetProvisionEndpoint:
    url: str
    timeout_seconds: int


class ControlTenantProvisionCreateService:
    """Create the central tenant and every provisioning outbox atomically."""

    def __init__(self, auth: AuthSchema, *, dispatcher: BusinessTaskDispatcher | None = None) -> None:
        if auth.db is None:
            raise RuntimeError("中控租户自动开户缺少数据库会话")
        self.auth = auth
        self.db = auth.db
        self.dispatcher = dispatcher or BusinessTaskDispatcher()

    def _site_id(self) -> int:
        if self.auth.site_id is None:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.site_id

    @staticmethod
    def _ensure_runtime_enabled() -> None:
        if not is_plugin_enabled("module_task") or not is_plugin_enabled("module_control_provision") or not settings.CELERY_ENABLED:
            raise CustomException(msg="租户自动开户任务运行时未启用", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    @require_platform_admin
    async def create(self, data: ControlTenantWithProvisionsCreateSchema) -> ControlTenantProvisionCreateOutcome:
        self._ensure_runtime_enabled()
        site_id = self._site_id()
        if data.tenant.site_id != site_id:
            raise CustomException(msg="禁止为其他站点创建租户", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        if self.auth.tenant_id is None or not await is_tenant_package_active(self.auth, self.auth.tenant_id):
            raise CustomException(msg="当前平台租户未绑定有效中控套餐，请先配置后再开户", status_code=status.HTTP_409_CONFLICT)
        application_ids = [item.application_id for item in data.applications]
        if len(set(application_ids)) != len(application_ids):
            raise CustomException(msg="同一应用只能选择一个套餐", status_code=status.HTTP_409_CONFLICT)

        # Preflight every selection before TenantService mutates the request transaction.
        package_service = ControlApplicationPackageService(self.auth)
        for item in data.applications:
            application = await self.db.get(ControlApplicationModel, item.application_id)
            if application is None or application.is_deleted:
                raise CustomException(msg="应用不存在", status_code=status.HTTP_404_NOT_FOUND)
            if application.site_id != site_id:
                raise CustomException(msg="禁止选择其他站点的应用", code=10403, status_code=status.HTTP_403_FORBIDDEN)
            if application.status != 0 or not application.provisioning_enabled or not application.provisioning_url:
                raise CustomException(msg="应用自动开户未启用或配置不完整", status_code=status.HTTP_409_CONFLICT)
            await package_service.require_selectable(item.application_package_id, application_id=item.application_id)

        tenant_result = await TenantService(self.auth).create(data.tenant)
        owner = (
            await self.db.execute(
                select(UserModel)
                .join(
                    TenantUserModel,
                    (TenantUserModel.user_id == UserModel.id) & (TenantUserModel.tenant_id == tenant_result.id) & (TenantUserModel.role == "owner"),
                )
                .where(UserModel.username == f"{tenant_result.code}_admin")
            )
        ).scalar_one()

        actor_id = self.auth.user.id if self.auth.user else None
        provisions: list[ControlTenantProvisionModel] = []
        task_ids: list[int] = []
        for item in data.applications:
            provision = ControlTenantProvisionModel(
                site_id=site_id,
                tenant_id=tenant_result.id,
                application_id=item.application_id,
                application_package_id=item.application_package_id,
                owner_user_id=owner.id,
                provision_request_uuid=str(uuid4()),
                desired_target_tenant_code=item.desired_target_tenant_code or tenant_result.code,
                status="pending",
                attempt_count=0,
                max_attempts=2,
                created_id=actor_id,
                updated_id=actor_id,
            )
            self.db.add(provision)
            await self.db.flush()
            task = await self.dispatcher.prepare(
                auth=self.auth,
                request=DispatchRequest(
                    handler_code="control.tenant_provision",
                    module="control",
                    biz_type="tenant_provision",
                    biz_id=str(provision.id),
                    title=f"开通租户应用 {item.application_id}",
                    payload={"provision_id": provision.id, "mode": "initial"},
                    max_retries=1,
                    idempotency_key=f"tenant-provision:{provision.provision_request_uuid}:initial",
                ),
            )
            provisions.append(provision)
            task_ids.append(task.id)

        return ControlTenantProvisionCreateOutcome(
            result=ControlTenantWithProvisionsOutSchema(
                tenant=tenant_result,
                provisions=[ControlTenantProvisionOutSchema.model_validate(item) for item in provisions],
            ),
            task_ids=task_ids,
        )


class ControlTenantProvisionService:
    """Site-scoped provisioning status, correction and manual operations."""

    def __init__(self, auth: AuthSchema, *, dispatcher: BusinessTaskDispatcher | None = None) -> None:
        if auth.db is None:
            raise RuntimeError("中控租户开通管理缺少数据库会话")
        self.auth = auth
        self.db = auth.db
        self.dispatcher = dispatcher or BusinessTaskDispatcher()

    def _site_id(self) -> int:
        if self.auth.site_id is None:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.site_id

    async def _get_scoped(self, provision_id: int, *, for_update: bool = False) -> ControlTenantProvisionModel:
        stmt = select(ControlTenantProvisionModel).where(ControlTenantProvisionModel.id == provision_id)
        if for_update:
            stmt = stmt.with_for_update()
        provision = (await self.db.execute(stmt)).scalar_one_or_none()
        if provision is None or provision.is_deleted:
            raise CustomException(msg="开通记录不存在", status_code=status.HTTP_404_NOT_FOUND)
        if provision.site_id != self._site_id():
            raise CustomException(msg="禁止访问其他站点的开通记录", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return provision

    @require_platform_admin
    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ControlTenantProvisionQueryParam,
    ) -> PageResultSchema[ControlTenantProvisionOutSchema]:
        filters = [
            ControlTenantProvisionModel.site_id == self._site_id(),
            ControlTenantProvisionModel.is_deleted.is_(False),
        ]
        for name, column in (
            ("tenant_id", ControlTenantProvisionModel.tenant_id),
            ("application_id", ControlTenantProvisionModel.application_id),
            ("status", ControlTenantProvisionModel.status),
        ):
            value = vars(search).get(name)
            if isinstance(value, tuple):
                filters.append(column == value[1])
        total = (await self.db.execute(select(func.count()).select_from(ControlTenantProvisionModel).where(*filters))).scalar_one()
        rows = (
            await self.db.execute(
                select(ControlTenantProvisionModel, ControlTenantApplicationModel.id.label("tenant_application_id"))
                .outerjoin(
                    ControlTenantApplicationModel,
                    and_(
                        ControlTenantApplicationModel.site_id == ControlTenantProvisionModel.site_id,
                        ControlTenantApplicationModel.tenant_id == ControlTenantProvisionModel.tenant_id,
                        ControlTenantApplicationModel.application_id == ControlTenantProvisionModel.application_id,
                        ControlTenantApplicationModel.is_deleted.is_(False),
                    ),
                )
                .where(*filters)
                .order_by(ControlTenantProvisionModel.id.desc())
                .offset((page_no - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=page_no * page_size < total,
            items=[ControlTenantProvisionOutSchema.model_validate(provision).model_copy(update={"tenant_application_id": tenant_application_id}) for provision, tenant_application_id in rows],
        )

    @require_platform_admin
    async def update(self, provision_id: int, data: ControlTenantProvisionUpdateSchema) -> ControlTenantProvisionOutSchema:
        provision = await self._get_scoped(provision_id, for_update=True)
        if provision.status != "failed":
            raise CustomException(msg="只有失败的开通记录允许修改", status_code=status.HTTP_409_CONFLICT)
        values = data.model_dump(exclude_unset=True)
        if not values:
            raise CustomException(msg="没有可修改的开户字段", status_code=status.HTTP_400_BAD_REQUEST)
        if "application_package_id" in values:
            await ControlApplicationPackageService(self.auth).require_selectable(values["application_package_id"], application_id=provision.application_id)
        for name, value in values.items():
            setattr(provision, name, value)
        provision.last_error_code = None
        provision.last_error_message = None
        provision.next_retry_at = None
        provision.completed_at = None
        provision.updated_id = self.auth.user.id if self.auth.user else None
        await self.db.flush()
        return ControlTenantProvisionOutSchema.model_validate(provision)

    @require_platform_admin
    async def dispatch_action(
        self,
        provision_id: int,
        action: Literal["retry", "reconcile"],
    ) -> BusinessTaskModel:
        ControlTenantProvisionCreateService._ensure_runtime_enabled()
        provision = await self._get_scoped(provision_id, for_update=True)
        if provision.status == "processing":
            active_task = None
            if provision.active_execution_token:
                active_task = (
                    await self.db.execute(
                        select(BusinessTaskModel)
                        .where(
                            BusinessTaskModel.handler_code == "control.tenant_provision",
                            BusinessTaskModel.biz_type == "tenant_provision",
                            BusinessTaskModel.biz_id == str(provision.id),
                            BusinessTaskModel.status == "running",
                            BusinessTaskModel.execution_token == provision.active_execution_token,
                            BusinessTaskModel.is_deleted.is_(False),
                        )
                        .order_by(BusinessTaskModel.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if active_task is not None and active_task.lease_expires_at is not None:
                comparable_now = datetime.now(UTC)
                if active_task.lease_expires_at.tzinfo is None:
                    comparable_now = comparable_now.replace(tzinfo=None)
                if active_task.lease_expires_at > comparable_now:
                    raise CustomException(msg="当前开通任务仍在执行", status_code=status.HTTP_409_CONFLICT)
            provision.status = "failed"
            provision.last_error_code = "STALE_PROCESSING"
            provision.last_error_message = "上一次开通执行已失去有效租约"
            provision.completed_at = datetime.now(UTC)
            provision.active_execution_token = None
        if action == "retry" and provision.status == "succeeded":
            raise CustomException(msg="已成功的开通记录不能重试", status_code=status.HTTP_409_CONFLICT)
        if action == "retry" and provision.status != "failed":
            raise CustomException(msg="只有失败的开通记录允许重试", status_code=status.HTTP_409_CONFLICT)
        if action == "reconcile" and provision.status not in {"failed", "succeeded"}:
            raise CustomException(msg="只有失败或已成功的开通记录允许对账", status_code=status.HTTP_409_CONFLICT)

        provision.status = "pending"
        provision.attempt_count = 0
        provision.next_retry_at = None
        provision.completed_at = None
        provision.last_error_code = None
        provision.last_error_message = None
        provision.active_execution_token = None
        provision.updated_id = self.auth.user.id if self.auth.user else None
        task = await self.dispatcher.prepare(
            auth=self.auth,
            request=DispatchRequest(
                handler_code="control.tenant_provision",
                module="control",
                biz_type="tenant_provision",
                biz_id=str(provision.id),
                title="人工重试租户开通" if action == "retry" else "对账租户开通",
                payload={"provision_id": provision.id, "mode": action},
                max_retries=0,
                idempotency_key=f"tenant-provision:{provision.provision_request_uuid}:{action}:{uuid4().hex}",
            ),
        )
        await self.db.flush()
        return task


class _TargetProvisionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ControlTenantProvisionWorkerService:
    """Execute one target provisioning attempt and preserve its domain ledger."""

    transport: ClassVar[httpx.AsyncBaseTransport | None] = None
    _temporary_statuses: ClassVar[set[int]] = {429, 502, 503, 504}

    def __init__(self, context: BusinessTaskContext) -> None:
        self.context = context

    async def execute(self, payload: ControlTenantProvisionTaskPayload) -> dict:
        user = self.context.auth.user
        platform_role = any(
            role.code == "SUPER_ADMIN"
            and role.tenant_id == self.context.tenant_id
            and role.status == 0
            and not role.is_deleted
            for role in (getattr(user, "roles", None) or [])
        )
        if not getattr(user, "is_superuser", False) or (self.context.tenant_id != 1 and not platform_role):
            raise CustomException(msg="租户自动开户任务仅允许平台管理员执行", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        actor_tenant = await self.context.db.get(TenantModel, self.context.tenant_id)
        if actor_tenant is None or (
            self.context.auth.site_id is not None and self.context.auth.site_id != actor_tenant.site_id
        ):
            raise CustomException(msg="开户任务站点上下文无效", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        self.context.auth.site_id = actor_tenant.site_id
        try:
            attempt = await self._start_attempt(payload.provision_id)
        except _TargetProvisionError as exc:
            await self._record_failure(
                payload.provision_id,
                code=exc.code,
                message=exc.message,
                will_retry=False,
            )
            raise CustomException(msg=exc.message, status_code=status.HTTP_409_CONFLICT) from exc
        except Exception:
            await self._record_failure(
                payload.provision_id,
                code="UNEXPECTED_ERROR",
                message="租户自动开户执行异常",
                will_retry=False,
            )
            raise CustomException(msg="租户自动开户执行异常", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR) from None
        if attempt is None:
            return {"status": "already_succeeded", "provision_id": payload.provision_id}
        endpoint, code, expected_tenant_code, attempt_count, max_attempts = attempt
        try:
            result = await self._call_target(endpoint, code)
            self._validate_target_result(expected_tenant_code, result)
        except _TargetProvisionError as exc:
            task_retry_available = await self._task_retry_available()
            will_retry = payload.mode == "initial" and exc.retryable and attempt_count < max_attempts and task_retry_available
            await self._record_failure(
                payload.provision_id,
                code=exc.code,
                message=exc.message,
                will_retry=will_retry,
            )
            if will_retry:
                raise RetryableBusinessTaskError(exc.message) from exc
            raise CustomException(msg=exc.message, status_code=status.HTTP_409_CONFLICT) from exc
        except Exception:
            await self._record_failure(
                payload.provision_id,
                code="UNEXPECTED_ERROR",
                message="租户自动开户执行异常",
                will_retry=False,
            )
            raise CustomException(msg="租户自动开户执行异常", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR) from None

        try:
            await self._record_success(payload.provision_id, result)
        except Exception:
            await self.context.db.rollback()
            await self._record_failure(
                payload.provision_id,
                code="UNEXPECTED_ERROR",
                message="租户自动开户执行异常",
                will_retry=False,
            )
            raise CustomException(msg="租户自动开户执行异常", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR) from None
        return {
            "status": "succeeded",
            "provision_id": payload.provision_id,
            "target_tenant_uuid": result.target_tenant_uuid,
            "target_tenant_code": result.target_tenant_code,
        }

    async def _task_retry_available(self) -> bool:
        """Keep the domain ledger aligned with the runtime's actual retry budget."""
        async with self.context.session_factory() as db:
            task = await db.get(BusinessTaskModel, self.context.task_id)
            return bool(task is not None and task.attempt <= task.max_retries)

    async def _start_attempt(
        self,
        provision_id: int,
    ) -> tuple[_TargetProvisionEndpoint, str, str, int, int] | None:
        async with self.context.session_factory() as db:
            provision = (
                await db.execute(select(ControlTenantProvisionModel).where(ControlTenantProvisionModel.id == provision_id, ControlTenantProvisionModel.is_deleted.is_(False)).with_for_update())
            ).scalar_one_or_none()
            if provision is None:
                raise CustomException(msg="开通记录不存在", status_code=status.HTTP_404_NOT_FOUND)
            if self.context.auth.site_id is not None and provision.site_id != self.context.auth.site_id:
                raise CustomException(msg="开通记录站点与任务上下文不一致", code=10403, status_code=status.HTTP_403_FORBIDDEN)
            if provision.status == "succeeded":
                return None
            if provision.attempt_count >= provision.max_attempts:
                provision.status = "failed"
                provision.active_execution_token = None
                provision.last_error_code = "ATTEMPTS_EXHAUSTED"
                provision.last_error_message = "当前开通执行周期已达到最大尝试次数"
                provision.next_retry_at = None
                provision.completed_at = datetime.now(UTC)
                await db.commit()
                raise CustomException(msg="当前开通执行周期已达到最大尝试次数", status_code=status.HTTP_409_CONFLICT)
            application = await db.get(ControlApplicationModel, provision.application_id)
            provision.attempt_count += 1
            provision.status = "processing"
            provision.active_execution_token = self.context.execution_token
            provision.started_at = datetime.now(UTC)
            provision.completed_at = None
            provision.next_retry_at = None
            provision.last_error_code = None
            provision.last_error_message = None
            if (
                application is None
                or application.is_deleted
                or application.site_id != provision.site_id
                or application.status != 0
                or not application.provisioning_enabled
                or not application.provisioning_url
            ):
                await db.commit()
                raise _TargetProvisionError("TARGET_CONFIG_INVALID", "目标应用自动开户配置不可用", retryable=False)
            code = await ControlProvisionTicketService(db).issue(provision.id)
            endpoint = _TargetProvisionEndpoint(
                url=application.provisioning_url,
                timeout_seconds=application.provisioning_timeout_seconds,
            )
            await db.commit()
            return endpoint, code, provision.desired_target_tenant_code, provision.attempt_count, provision.max_attempts

    @staticmethod
    def _validate_target_result(
        expected_tenant_code: str,
        result: ControlTargetProvisionResult,
    ) -> None:
        if result.target_tenant_code != expected_tenant_code:
            raise _TargetProvisionError(
                "TARGET_TENANT_CODE_MISMATCH",
                "目标产品返回的租户编码与开户注册请求不一致",
                retryable=False,
            )

    async def _call_target(self, endpoint: _TargetProvisionEndpoint, code: str) -> ControlTargetProvisionResult:
        timeout_seconds = endpoint.timeout_seconds
        timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                response = await client.post(endpoint.url, json={"code": code})
        except httpx.TimeoutException as exc:
            raise _TargetProvisionError("TARGET_TIMEOUT", "目标产品开户请求超时", retryable=True) from exc
        except httpx.ConnectError as exc:
            raise _TargetProvisionError("TARGET_CONNECT_ERROR", "无法连接目标产品开户服务", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise _TargetProvisionError("TARGET_HTTP_ERROR", "目标产品开户请求失败", retryable=False) from exc
        if response.status_code >= 400:
            raise _TargetProvisionError(
                f"TARGET_HTTP_{response.status_code}",
                f"目标产品开户返回 HTTP {response.status_code}",
                retryable=response.status_code in self._temporary_statuses,
            )
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            return ControlTargetProvisionResult.model_validate(payload)
        except (ValueError, ValidationError, TypeError) as exc:
            raise _TargetProvisionError("TARGET_RESPONSE_INVALID", "目标产品开户响应格式无效", retryable=False) from exc

    async def _record_failure(self, provision_id: int, *, code: str, message: str, will_retry: bool) -> None:
        now = datetime.now(UTC)
        async with self.context.session_factory() as db:
            await db.execute(
                update(ControlTenantProvisionModel)
                .where(
                    ControlTenantProvisionModel.id == provision_id,
                    ControlTenantProvisionModel.is_deleted.is_(False),
                    ControlTenantProvisionModel.status == "processing",
                    ControlTenantProvisionModel.active_execution_token == self.context.execution_token,
                )
                .values(
                    status="pending" if will_retry else "failed",
                    active_execution_token=None,
                    last_error_code=code,
                    last_error_message=message[:500],
                    next_retry_at=now + timedelta(seconds=10) if will_retry else None,
                    completed_at=None if will_retry else now,
                )
            )
            await db.commit()

    async def _record_success(self, provision_id: int, result: ControlTargetProvisionResult) -> None:
        db = self.context.db
        provision = (
            await db.execute(
                select(ControlTenantProvisionModel)
                .where(
                    ControlTenantProvisionModel.id == provision_id,
                    ControlTenantProvisionModel.is_deleted.is_(False),
                    ControlTenantProvisionModel.status == "processing",
                    ControlTenantProvisionModel.active_execution_token == self.context.execution_token,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if provision is None:
            raise CustomException(msg="开通执行代际已失效", status_code=status.HTTP_409_CONFLICT)
        if self.context.auth.site_id is not None and provision.site_id != self.context.auth.site_id:
            raise CustomException(msg="开通记录站点与任务上下文不一致", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        owner_membership = (
            await db.execute(
                select(TenantUserModel)
                .where(
                    TenantUserModel.tenant_id == provision.tenant_id,
                    TenantUserModel.user_id == provision.owner_user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        opening = (
            await db.execute(
                select(ControlTenantApplicationModel)
                .where(
                    ControlTenantApplicationModel.tenant_id == provision.tenant_id,
                    ControlTenantApplicationModel.application_id == provision.application_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        actor_id = self.context.actor_user_id
        if opening is None:
            opening = ControlTenantApplicationModel(
                site_id=provision.site_id,
                tenant_id=provision.tenant_id,
                application_id=provision.application_id,
                target_tenant_code=result.target_tenant_code,
                status=0,
                opened_at=now,
                created_id=actor_id,
                updated_id=actor_id,
            )
            db.add(opening)
            await db.flush()
        else:
            opening.target_tenant_code = result.target_tenant_code
            opening.status = 0
            opening.opened_at = now
            opening.is_deleted = False
            opening.deleted_time = None
            opening.deleted_id = None
            opening.updated_id = actor_id

        if owner_membership is None:
            # 目标产品开户可能已经成功，但中央成员关系在回写前
            # 已删除。保留 opening 事实，严禁再创建 active owner grant。
            provision.target_tenant_code = result.target_tenant_code
            provision.target_tenant_uuid = result.target_tenant_uuid
            provision.status = "succeeded"
            provision.active_execution_token = None
            provision.last_error_code = None
            provision.last_error_message = None
            provision.next_retry_at = None
            provision.completed_at = now
            provision.updated_id = actor_id
            await db.flush()
            return

        grant = (
            await db.execute(
                select(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                    ControlUserApplicationGrantModel.user_id == provision.owner_user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if grant is None:
            grant = ControlUserApplicationGrantModel(
                site_id=provision.site_id,
                tenant_application_id=opening.id,
                tenant_id=provision.tenant_id,
                user_id=provision.owner_user_id,
                status=0,
                desired_state="active",
                sync_status="succeeded",
                sync_version=1,
                last_event_id=provision.provision_request_uuid,
                last_synced_at=now,
                granted_at=now,
                created_id=actor_id,
                updated_id=actor_id,
            )
            db.add(grant)
        else:
            same_bootstrap = grant.last_event_id == provision.provision_request_uuid
            initial_state_shape = (grant.status == 1 and grant.desired_state == "inactive" and grant.sync_version == 0 and grant.sync_status in {"pending", "succeeded"}) or (
                grant.sync_version == 1
                and (
                    (grant.status == 0 and grant.desired_state == "active" and grant.sync_status == "pending")
                    or (grant.status == 1 and grant.desired_state == "inactive" and grant.sync_status == "succeeded")
                )
            )
            uninitialized_shape = (
                grant.last_event_id is None
                and initial_state_shape
                and grant.active_execution_token is None
                and grant.retry_count == 0
                and grant.last_attempt_at is None
                and grant.last_synced_at is None
            )
            has_entitlement_task = False
            if uninitialized_shape:
                has_entitlement_task = (
                    await db.execute(
                        select(BusinessTaskModel.id)
                        .where(
                            BusinessTaskModel.biz_type == "user_entitlement",
                            BusinessTaskModel.biz_id == str(grant.id),
                            BusinessTaskModel.is_deleted.is_(False),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none() is not None

            # Provision bootstrap must never resurrect a later user command.
            # Only the original bootstrap generation (or an untouched legacy
            # ledger row) owns the right to initialize the owner grant.
            if same_bootstrap or (uninitialized_shape and not has_entitlement_task):
                grant.status = 0
                grant.desired_state = "active"
                grant.sync_status = "succeeded"
                grant.sync_version = max(grant.sync_version, 1)
                grant.last_event_id = provision.provision_request_uuid
                grant.last_synced_at = now
                grant.active_execution_token = None
                grant.next_retry_at = None
                grant.last_error_code = None
                grant.last_error_message = None
                grant.granted_at = grant.granted_at or now
                grant.is_deleted = False
                grant.deleted_time = None
                grant.deleted_id = None
                grant.updated_id = actor_id

        provision.target_tenant_code = result.target_tenant_code
        provision.target_tenant_uuid = result.target_tenant_uuid
        provision.status = "succeeded"
        provision.active_execution_token = None
        provision.last_error_code = None
        provision.last_error_message = None
        provision.next_retry_at = None
        provision.completed_at = now
        provision.updated_id = actor_id
        await db.flush()
