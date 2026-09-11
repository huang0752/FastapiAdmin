"""Control user-entitlement state machine, outbox publication and worker."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal
from uuid import uuid4

import httpx
from fastapi import status
from pydantic import ValidationError
from sqlalchemy import and_, select, update

from app.api.v1.module_control.model import (
    ControlApplicationModel,
    ControlTenantApplicationModel,
    ControlUserApplicationGrantModel,
)
from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.role.model import CONTROL_PORTAL_USER_ROLE_CODE, RoleModel
from app.api.v1.module_system.user.model import UserModel
from app.api.v1.module_system.user.schema import UserOutSchema
from app.api.v1.module_system.user.service import UserService
from app.core.base_schema import AuthSchema
from app.core.dependencies import resolve_effective_permissions
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.plugin.module_task.business.task.model import BusinessTaskModel
from app.plugin.module_task.runtime.context import (
    BusinessTaskContext,
    DomainClosureDisposition,
)
from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
from app.plugin.module_task.runtime.exceptions import (
    DomainClosureRetryableBusinessTaskError,
    PreHandlerRetryableBusinessTaskError,
    RetryableBusinessTaskError,
)
from app.plugin.module_task.runtime.registry import business_task_registry

from .schema import ControlUserCreateIn
from .task_schema import ControlTargetUserEntitlementResult, ControlUserEntitlementTaskPayload
from .ticket_service import ControlUserEntitlementTicketService

CONTROL_ORDINARY_ROLE_CODE = CONTROL_PORTAL_USER_ROLE_CODE
CONTROL_ORDINARY_ROLE_NAME = "普通用户"
CONTROL_ORDINARY_ROLE_PERMISSIONS = {
    "module_control:portal:query",
    "module_control:portal:launch",
}


@dataclass(slots=True)
class ControlUserCreateOutcome:
    user: UserOutSchema
    grants: list[ControlUserApplicationGrantModel]
    task_ids: list[int]


class ControlUserCreateService:
    """Create an ordinary user and prepare every selected entitlement atomically."""

    def __init__(self, auth: AuthSchema) -> None:
        if auth.db is None:
            raise RuntimeError("中控组合建用户缺少数据库会话")
        self.auth = auth

    async def _ensure_ordinary_user_role(self) -> RoleModel:
        """Reconcile the protected tenant-local portal role without exposing role choice."""
        tenant = await self.auth.db.scalar(
            select(TenantModel)
            .where(TenantModel.id == self.auth.tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if tenant is None:
            raise CustomException(msg="当前租户不存在", status_code=404)

        role = await self.auth.db.scalar(
            select(RoleModel)
            .where(
                RoleModel.tenant_id == self.auth.tenant_id,
                RoleModel.code == CONTROL_ORDINARY_ROLE_CODE,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if role is None:
            role = RoleModel(
                tenant_id=self.auth.tenant_id,
                name=CONTROL_ORDINARY_ROLE_NAME,
                code=CONTROL_ORDINARY_ROLE_CODE,
                order=999,
                status=0,
                data_scope=1,
                description="中控内置普通用户角色",
                menus=[],
            )
            self.auth.db.add(role)
            await self.auth.db.flush()
        else:
            role.name = CONTROL_ORDINARY_ROLE_NAME
            role.status = 0
            role.data_scope = 1
            role.is_deleted = False
            role.deleted_id = None
            role.deleted_time = None

        portal_menu = await self.auth.db.scalar(
            select(MenuModel).where(
                MenuModel.route_name == "ControlPortal",
                MenuModel.scope == "tenant",
                MenuModel.status == 0,
                MenuModel.is_deleted.is_(False),
            )
        )
        if portal_menu is None:
            raise CustomException(msg="普通用户应用中心权限未初始化")
        portal_menus = list(
            (
                await self.auth.db.scalars(
                    select(MenuModel).where(
                        (MenuModel.id == portal_menu.id)
                        | (MenuModel.parent_id == portal_menu.id),
                        MenuModel.status == 0,
                        MenuModel.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        if {menu.permission for menu in portal_menus if menu.permission} != CONTROL_ORDINARY_ROLE_PERMISSIONS:
            raise CustomException(msg="普通用户应用中心权限未初始化")
        parent = await self.auth.db.scalar(
            select(MenuModel).where(
                MenuModel.id == portal_menu.parent_id,
                MenuModel.route_name == "Control",
                MenuModel.scope == "tenant",
                MenuModel.status == 0,
                MenuModel.is_deleted.is_(False),
            )
        )
        if parent is None:
            raise CustomException(msg="普通用户应用中心父目录未初始化")
        role.menus[:] = sorted([parent, *portal_menus], key=lambda menu: menu.id)
        await self.auth.db.flush()
        return role

    async def create(self, data: ControlUserCreateIn) -> ControlUserCreateOutcome:
        # Import here to keep the provider service independent from the worker
        # module while reusing the same scoped grant-ledger rules.
        from app.api.v1.module_control.service import ControlUserApplicationGrantService

        ordinary_role = await self._ensure_ordinary_user_role()
        ordinary_user = data.user.model_copy(
            update={"is_superuser": False, "role_ids": [], "tenant_id": None}
        )
        user_service = UserService(self.auth)
        user = await user_service.create(ordinary_user)
        user_model = await self.auth.db.get(UserModel, user.id)
        if user_model is None:
            raise CustomException(msg="新建用户不存在")
        user_model.roles.clear()
        user_model.roles.append(ordinary_role)
        await self.auth.db.flush()
        user = await user_service.detail(user.id)
        grants: list[ControlUserApplicationGrantModel] = []
        task_ids: list[int] = []
        command = ControlUserEntitlementCommandService(self.auth)
        grant_service = ControlUserApplicationGrantService(self.auth)
        # Every batch acquires shared opening rows in the same order so two
        # administrators cannot deadlock by selecting products in reverse.
        for tenant_application_id in sorted(data.tenant_application_ids):
            grant = await grant_service.ensure_grant(tenant_application_id, user.id)
            task = await command.set_desired_state(grant.id, "active", mode="grant")
            grants.append(grant)
            task_ids.append(task.id)
        return ControlUserCreateOutcome(user=user, grants=grants, task_ids=task_ids)


async def publish_entitlement_tasks(
    task_ids: list[int],
    *,
    dispatcher: BusinessTaskDispatcher | None = None,
) -> None:
    """Publish committed outbox rows; failed publication remains scanner-recoverable."""
    task_dispatcher = dispatcher or BusinessTaskDispatcher()
    for task_id in task_ids:
        try:
            await task_dispatcher.publish_existing(task_id)
        except Exception as exc:
            logger.error("用户授权同步任务发布失败 task_id={} error={}", task_id, type(exc).__name__)


class ControlUserEntitlementCommandService:
    """Serialize desired-state transitions and prepare their outbox in one transaction."""

    def __init__(self, auth: AuthSchema, *, dispatcher: BusinessTaskDispatcher | None = None) -> None:
        if auth.db is None:
            raise RuntimeError("中控用户授权命令缺少数据库会话")
        self.auth = auth
        self.db = auth.db
        self.dispatcher = dispatcher or BusinessTaskDispatcher()

    def _site_id(self) -> int:
        if self.auth.site_id is None:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.site_id

    async def _lock_grant(self, grant_id: int) -> ControlUserApplicationGrantModel:
        grant = (
            await self.db.execute(
                select(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.id == grant_id,
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if grant is None:
            raise CustomException(msg="用户应用授权不存在", status_code=status.HTTP_404_NOT_FOUND)
        if grant.site_id != self._site_id() or (
            not self.auth.is_platform_global and grant.tenant_id != self.auth.tenant_id
        ):
            raise CustomException(
                msg="禁止访问其他租户或站点的用户授权",
                code=10403,
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return grant

    async def _prepare(self, grant: ControlUserApplicationGrantModel, *, mode: str) -> BusinessTaskModel:
        task_auth = self.auth.model_copy(
            update={"tenant_id": grant.tenant_id, "site_id": grant.site_id}
        )
        task = await self.dispatcher.prepare(
            auth=task_auth,
            request=DispatchRequest(
                handler_code="control.user_entitlement_sync",
                module="control",
                biz_type="user_entitlement",
                biz_id=str(grant.id),
                title=f"同步用户应用授权 {grant.id}",
                payload={
                    "grant_id": grant.id,
                    "event_id": grant.last_event_id,
                    "sync_version": grant.sync_version,
                    "mode": mode,
                },
                max_retries=2,
                idempotency_key=(
                    f"user-entitlement:{grant.id}:{grant.sync_version}:{grant.last_event_id}"
                ),
            ),
        )
        await self.db.flush()
        return task

    async def set_desired_state(
        self,
        grant_id: int,
        desired_state: Literal["active", "inactive"],
        *,
        mode: Literal["grant", "revoke", "backfill", "lifecycle"],
    ) -> BusinessTaskModel:
        grant = await self._lock_grant(grant_id)
        grant.sync_version += 1
        grant.last_event_id = uuid4().hex
        grant.desired_state = desired_state
        grant.status = 0 if desired_state == "active" else 1
        grant.sync_status = "pending"
        grant.active_execution_token = None
        grant.retry_count = 0
        grant.next_retry_at = None
        grant.last_attempt_at = None
        grant.last_error_code = None
        grant.last_error_message = None
        grant.updated_id = self.auth.user.id if self.auth.user else None
        return await self._prepare(grant, mode=mode)

    async def retry(self, grant_id: int) -> BusinessTaskModel:
        grant = await self._lock_grant(grant_id)
        if grant.sync_status != "failed":
            raise CustomException(msg="只有失败的用户授权同步允许重试", status_code=status.HTTP_409_CONFLICT)
        from .lifecycle import TASK_PREFIX, is_lifecycle_task
        previous = (await self.db.scalars(select(BusinessTaskModel).where(
            BusinessTaskModel.handler_code == "control.user_entitlement_sync",
            BusinessTaskModel.biz_id == str(grant.id),
            BusinessTaskModel.idempotency_key == f"{TASK_PREFIX}{grant.id}:{grant.sync_version}:{grant.last_event_id}",
        ))).one_or_none()
        lifecycle_retry = previous is not None and is_lifecycle_task(previous) and grant.desired_state == "inactive"
        # Products reject a different event at the same version. A manual retry
        # is therefore a new immutable synchronization generation.
        grant.sync_version += 1
        grant.last_event_id = uuid4().hex
        grant.sync_status = "pending"
        grant.active_execution_token = None
        grant.retry_count = 0
        grant.next_retry_at = None
        grant.last_attempt_at = None
        grant.last_error_code = None
        grant.last_error_message = None
        grant.updated_id = self.auth.user.id if self.auth.user else None
        task = await self._prepare(grant, mode="lifecycle" if lifecycle_retry else "retry")
        if lifecycle_retry:
            task.idempotency_key = f"{TASK_PREFIX}{grant.id}:{grant.sync_version}:{grant.last_event_id}"
            await self.db.flush()
        return task


@dataclass(frozen=True, slots=True)
class _TargetEndpoint:
    url: str
    timeout_seconds: int


class _TargetEntitlementError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ControlUserEntitlementWorkerService:
    """Synchronize one immutable grant generation to its configured product."""

    transport: ClassVar[httpx.AsyncBaseTransport | None] = None
    _temporary_statuses: ClassVar[set[int]] = {429, 502, 503, 504}
    _max_response_bytes: ClassVar[int] = 64 * 1024

    def __init__(self, context: BusinessTaskContext) -> None:
        self.context = context

    async def execute(self, payload: ControlUserEntitlementTaskPayload) -> dict:
        try:
            try:
                await self._require_actor_permission(payload.mode)
            finally:
                await self._release_context_db()
        except CustomException:
            written = await self._guard_domain_operation(
                self._record_start_failure(
                    payload,
                    code="ACTOR_INVALID",
                    message="用户授权同步任务执行人权限已失效",
                )
            )
            if not written:
                return {"status": "superseded", "grant_id": payload.grant_id}
            raise
        except Exception:
            raise PreHandlerRetryableBusinessTaskError(
                "用户授权同步任务执行人权限复核暂时失败"
            ) from None
        try:
            attempt = await self._start_attempt(payload)
        except _TargetEntitlementError as exc:
            written = await self._guard_domain_operation(
                self._record_start_failure(payload, code=exc.code, message=exc.message)
            )
            if not written:
                return {"status": "superseded", "grant_id": payload.grant_id}
            raise CustomException(msg=exc.message, status_code=status.HTTP_409_CONFLICT) from exc
        except CustomException:
            raise
        except Exception:
            written = await self._guard_domain_operation(
                self._record_start_failure(
                    payload,
                    code="UNEXPECTED_ERROR",
                    message="用户授权同步执行异常",
                )
            )
            if not written:
                return {"status": "superseded", "grant_id": payload.grant_id}
            raise CustomException(msg="用户授权同步执行异常", status_code=500) from None
        if attempt is None:
            return {"status": "superseded", "grant_id": payload.grant_id}

        endpoint, ticket_code, desired_state = attempt
        attempt = None
        try:
            try:
                result = await self._call_target(endpoint, ticket_code)
            finally:
                ticket_code = "<redacted>"
            self._validate_target_result(payload, desired_state, result)
        except _TargetEntitlementError as exc:
            retry_delay = (
                await self._guard_domain_operation(self._task_retry_delay())
                if exc.retryable
                else None
            )
            will_retry = retry_delay is not None
            written = await self._guard_domain_operation(
                self._record_failure(
                    payload,
                    code=exc.code,
                    message=exc.message,
                    will_retry=will_retry,
                    retry_delay_seconds=retry_delay,
                )
            )
            if not written:
                return {"status": "superseded", "grant_id": payload.grant_id}
            if will_retry:
                raise RetryableBusinessTaskError(exc.message) from exc
            raise CustomException(msg=exc.message, status_code=status.HTTP_409_CONFLICT) from exc
        except Exception:
            written = await self._guard_domain_operation(
                self._record_failure(
                    payload,
                    code="UNEXPECTED_ERROR",
                    message="用户授权同步执行异常",
                    will_retry=False,
                    retry_delay_seconds=None,
                )
            )
            if not written:
                return {"status": "superseded", "grant_id": payload.grant_id}
            raise CustomException(msg="用户授权同步执行异常", status_code=500) from None

        if result.disposition == "superseded":
            current_generation = await self._guard_domain_operation(
                self._record_failure(
                    payload,
                    code="TARGET_VERSION_AHEAD",
                    message="目标产品授权版本高于中控当前版本",
                    will_retry=False,
                    retry_delay_seconds=None,
                )
            )
            if current_generation:
                raise CustomException(msg="目标产品授权版本高于中控当前版本", status_code=status.HTTP_409_CONFLICT)
            return {"status": "superseded", "grant_id": payload.grant_id}
        written = await self._guard_domain_operation(self._record_success(payload))
        return {
            "status": "succeeded" if written else "superseded",
            "grant_id": payload.grant_id,
            "applied_version": result.applied_version,
            "disposition": result.disposition,
        }

    @staticmethod
    async def _guard_domain_operation(operation):
        try:
            return await operation
        except DomainClosureRetryableBusinessTaskError:
            raise
        except Exception:
            raise DomainClosureRetryableBusinessTaskError(
                "用户授权同步业务状态收口暂时失败"
            ) from None

    async def _require_actor_permission(self, mode: str) -> None:
        if mode == "lifecycle":
            if not getattr(self.context, "lifecycle_revocation_verified", False):
                raise CustomException(msg="生命周期撤权来源未经验证", status_code=403)
            return
        user = self.context.auth.user
        if user is None:
            raise CustomException(msg="用户授权同步任务缺少执行人", code=10403, status_code=403)
        if user.is_superuser:
            return
        required = {
            "revoke": "module_control:user_grant:delete",
            "retry": "module_control:user_grant:retry",
        }.get(mode, "module_control:user_grant:update")
        permissions = await resolve_effective_permissions(
            self.context.auth,
            bypass_role_grants=False,
            require_active_package=True,
        )
        if required not in permissions:
            raise CustomException(msg="用户授权同步任务执行人权限不足", code=10403, status_code=403)

    async def _release_context_db(self) -> None:
        db = self.context.auth.db
        if db is None:
            return
        try:
            await db.rollback()
        except Exception:
            with suppress(Exception):
                await db.close()
            raise
        await db.close()

    async def _start_attempt(
        self,
        payload: ControlUserEntitlementTaskPayload,
    ) -> tuple[_TargetEndpoint, str, str] | None:
        async with self.context.session_factory() as db:
            task = (
                await db.execute(
                    select(BusinessTaskModel).where(
                        BusinessTaskModel.id == self.context.task_id,
                        BusinessTaskModel.handler_code == "control.user_entitlement_sync",
                        BusinessTaskModel.biz_type == "user_entitlement",
                        BusinessTaskModel.biz_id == str(payload.grant_id),
                        BusinessTaskModel.status == "running",
                        BusinessTaskModel.execution_token == self.context.execution_token,
                        BusinessTaskModel.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if task is None or task.payload != payload.model_dump(mode="json"):
                return None
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel)
                    .where(
                        ControlUserApplicationGrantModel.id == payload.grant_id,
                        ControlUserApplicationGrantModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if grant is None:
                raise CustomException(msg="用户应用授权不存在", status_code=404)
            if grant.tenant_id != self.context.tenant_id:
                raise CustomException(msg="用户授权租户与任务上下文不一致", code=10403, status_code=403)
            if self.context.auth.site_id is not None and grant.site_id != self.context.auth.site_id:
                raise CustomException(msg="用户授权站点与任务上下文不一致", code=10403, status_code=403)
            self.context.auth.site_id = grant.site_id
            if grant.last_event_id != payload.event_id or grant.sync_version != payload.sync_version:
                return None

            state = await self._load_target(db, grant)
            grant.sync_status = "processing"
            grant.active_execution_token = self.context.execution_token
            grant.last_attempt_at = datetime.now(UTC)
            grant.retry_count = max(0, task.attempt - 1)
            grant.next_retry_at = None
            grant.last_error_code = None
            grant.last_error_message = None
            if state is None:
                await db.commit()
                raise _TargetEntitlementError("TARGET_CONFIG_INVALID", "目标应用授权同步配置不可用", retryable=False)
            application, opening = state
            if (
                not application.entitlement_sync_url
                or (grant.desired_state == "active" and (
                    application.is_deleted
                    or application.status != 0
                    or not application.entitlement_sync_enabled
                    or opening.is_deleted
                    or opening.status != 0
                ))
            ):
                await db.commit()
                raise _TargetEntitlementError("TARGET_CONFIG_INVALID", "目标应用授权同步配置不可用", retryable=False)
            endpoint = _TargetEndpoint(
                url=application.entitlement_sync_url,
                timeout_seconds=application.entitlement_sync_timeout_seconds,
            )
            desired_state = grant.desired_state
            # Persist the execution generation before signing. If ticket
            # persistence fails, guarded failure write-back can still close the
            # ledger instead of leaving an eternal pending state.
            await db.commit()

            current = (
                await db.execute(
                    select(ControlUserApplicationGrantModel)
                    .where(
                        ControlUserApplicationGrantModel.id == payload.grant_id,
                        ControlUserApplicationGrantModel.last_event_id == payload.event_id,
                        ControlUserApplicationGrantModel.sync_version == payload.sync_version,
                        ControlUserApplicationGrantModel.sync_status == "processing",
                        ControlUserApplicationGrantModel.active_execution_token == self.context.execution_token,
                        ControlUserApplicationGrantModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if current is None:
                return None
            code = await ControlUserEntitlementTicketService(db).issue(
                current.id,
                event_id=payload.event_id,
                sync_version=payload.sync_version,
            )
            await db.commit()
            return endpoint, code, desired_state

    @staticmethod
    async def _load_target(db, grant):
        return (
            await db.execute(
                select(ControlApplicationModel, ControlTenantApplicationModel)
                .join(
                    ControlTenantApplicationModel,
                    and_(
                        ControlTenantApplicationModel.application_id == ControlApplicationModel.id,
                        ControlTenantApplicationModel.site_id == ControlApplicationModel.site_id,
                    ),
                )
                .where(
                    ControlTenantApplicationModel.id == grant.tenant_application_id,
                    ControlTenantApplicationModel.tenant_id == grant.tenant_id,
                    ControlTenantApplicationModel.site_id == grant.site_id,
                )
            )
        ).one_or_none()

    async def _task_retry_delay(self) -> int | None:
        async with self.context.session_factory() as db:
            task = await db.get(BusinessTaskModel, self.context.task_id)
            if task is None or task.attempt > task.max_retries:
                return None
            definition = business_task_registry.get(task.handler_code or "")
            return definition.retry_backoff_seconds * (2 ** max(0, task.attempt - 1))

    async def _call_target(
        self,
        endpoint: _TargetEndpoint,
        code: str,
    ) -> ControlTargetUserEntitlementResult:
        timeout = httpx.Timeout(endpoint.timeout_seconds, connect=endpoint.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                async with client.stream(
                    "POST",
                    endpoint.url,
                    json={"code": code},
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    response_status = response.status_code
                    if response_status >= 400:
                        raise _TargetEntitlementError(
                            f"TARGET_HTTP_{response_status}",
                            f"目标产品授权同步返回 HTTP {response_status}",
                            retryable=response_status in self._temporary_statuses,
                        )
                    result = await self._read_target_result(response)
        except httpx.TimeoutException:
            raise _TargetEntitlementError("TARGET_TIMEOUT", "目标产品授权同步请求超时", retryable=True) from None
        except (httpx.LocalProtocolError, httpx.UnsupportedProtocol):
            raise _TargetEntitlementError("TARGET_PROTOCOL_ERROR", "目标产品授权同步协议异常", retryable=False) from None
        except httpx.RemoteProtocolError:
            raise _TargetEntitlementError("TARGET_REMOTE_PROTOCOL_ERROR", "目标产品授权同步远端协议异常", retryable=True) from None
        except httpx.TransportError:
            raise _TargetEntitlementError("TARGET_TRANSPORT_ERROR", "目标产品授权同步网络异常", retryable=True) from None
        except httpx.HTTPError:
            raise _TargetEntitlementError("TARGET_HTTP_ERROR", "目标产品授权同步请求失败", retryable=False) from None
        finally:
            code = "<redacted>"
        return result

    @classmethod
    async def _read_target_result(
        cls,
        response: httpx.Response,
    ) -> ControlTargetUserEntitlementResult:
        """Read a bounded response and erase untrusted content before any error escapes."""
        content_encoding = response.headers.get("content-encoding", "").strip().lower()
        if content_encoding and content_encoding != "identity":
            raise _TargetEntitlementError(
                "TARGET_RESPONSE_ENCODING_UNSUPPORTED",
                "目标产品授权同步响应使用了不支持的压缩编码",
                retryable=False,
            ) from None
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                raise _TargetEntitlementError(
                    "TARGET_RESPONSE_INVALID",
                    "目标产品授权同步响应长度无效",
                    retryable=False,
                ) from None
            if declared_length < 0:
                raise _TargetEntitlementError(
                    "TARGET_RESPONSE_INVALID",
                    "目标产品授权同步响应长度无效",
                    retryable=False,
                ) from None
            if declared_length > cls._max_response_bytes:
                raise _TargetEntitlementError(
                    "TARGET_RESPONSE_TOO_LARGE",
                    "目标产品授权同步响应超过大小限制",
                    retryable=False,
                )

        raw_body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(raw_body) + len(chunk) > cls._max_response_bytes:
                raw_body = bytearray(b"<redacted>")
                chunk = b"<redacted>"
                raise _TargetEntitlementError(
                    "TARGET_RESPONSE_TOO_LARGE",
                    "目标产品授权同步响应超过大小限制",
                    retryable=False,
                ) from None
            raw_body.extend(chunk)

        try:
            body = json.loads(raw_body)
            if isinstance(body, dict) and isinstance(body.get("data"), dict):
                body = body["data"]
            result = ControlTargetUserEntitlementResult.model_validate(body)
        except (ValueError, TypeError, ValidationError):
            raw_body = bytearray(b"<redacted>")
            body = "<redacted>"
            raise _TargetEntitlementError(
                "TARGET_RESPONSE_INVALID",
                "目标产品授权同步响应格式无效",
                retryable=False,
            ) from None
        raw_body = bytearray(b"<redacted>")
        body = "<redacted>"
        return result

    @staticmethod
    def _validate_target_result(
        payload: ControlUserEntitlementTaskPayload,
        desired_state: str,
        result: ControlTargetUserEntitlementResult,
    ) -> None:
        if result.disposition == "superseded":
            if result.applied_version <= payload.sync_version:
                raise _TargetEntitlementError("TARGET_RESPONSE_INVALID", "目标产品返回了无效的超前版本", retryable=False)
            return
        if result.applied_version != payload.sync_version or result.status != desired_state:
            raise _TargetEntitlementError("TARGET_STATE_MISMATCH", "目标产品授权同步状态与请求不一致", retryable=False)
        if desired_state == "inactive" and result.session_cleanup_pending:
            raise _TargetEntitlementError("TARGET_SESSION_CLEANUP_PENDING", "目标产品会话清理尚未完成", retryable=True)
        # Qualification and local business permissions are separate contracts.
        # Manual-role products can acknowledge the entitlement while awaiting a local role.

    async def _record_failure(
        self,
        payload: ControlUserEntitlementTaskPayload,
        *,
        code: str,
        message: str,
        will_retry: bool,
        retry_delay_seconds: int | None,
    ) -> bool:
        now = datetime.now(UTC)
        async with self.context.session_factory() as db:
            result = await db.execute(
                update(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.id == payload.grant_id,
                    ControlUserApplicationGrantModel.tenant_id == self.context.tenant_id,
                    ControlUserApplicationGrantModel.last_event_id == payload.event_id,
                    ControlUserApplicationGrantModel.sync_version == payload.sync_version,
                    ControlUserApplicationGrantModel.sync_status == "processing",
                    ControlUserApplicationGrantModel.active_execution_token == self.context.execution_token,
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
                .values(
                    sync_status="pending" if will_retry else "failed",
                    active_execution_token=None,
                    last_error_code=code,
                    last_error_message=message[:500],
                    next_retry_at=(
                        now + timedelta(seconds=retry_delay_seconds)
                        if will_retry and retry_delay_seconds is not None
                        else None
                    ),
                )
            )
            await db.commit()
            return bool(result.rowcount)

    async def _record_start_failure(
        self,
        payload: ControlUserEntitlementTaskPayload,
        *,
        code: str,
        message: str,
    ) -> bool | DomainClosureDisposition:
        if getattr(self.context, "reconciliation", False):
            return await self._reconcile_start_failure(
                payload,
                code=code,
                message=message,
            )
        if await self._record_failure(
            payload,
            code=code,
            message=message,
            will_retry=False,
            retry_delay_seconds=None,
        ):
            return True
        async with self.context.session_factory() as db:
            task_state = (
                and_(
                    BusinessTaskModel.status == "failed",
                    BusinessTaskModel.error_code == "DOMAIN_CLOSURE_PENDING",
                )
                if getattr(self.context, "reconciliation", False)
                else and_(
                    BusinessTaskModel.status == "running",
                    BusinessTaskModel.execution_token == self.context.execution_token,
                )
            )
            task = (
                await db.execute(
                    select(BusinessTaskModel).where(
                        BusinessTaskModel.id == self.context.task_id,
                        task_state,
                        BusinessTaskModel.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                return False
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel)
                    .where(
                        ControlUserApplicationGrantModel.id == payload.grant_id,
                        ControlUserApplicationGrantModel.tenant_id == self.context.tenant_id,
                        ControlUserApplicationGrantModel.last_event_id == payload.event_id,
                        ControlUserApplicationGrantModel.sync_version == payload.sync_version,
                        ControlUserApplicationGrantModel.sync_status == "pending",
                        ControlUserApplicationGrantModel.active_execution_token.is_(None),
                        ControlUserApplicationGrantModel.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if grant is None:
                return False
            grant.sync_status = "failed"
            grant.last_error_code = code
            grant.last_error_message = message[:500]
            grant.next_retry_at = None
            await db.commit()
            return True

    async def _reconcile_start_failure(
        self,
        payload: ControlUserEntitlementTaskPayload,
        *,
        code: str,
        message: str,
    ) -> DomainClosureDisposition:
        db = getattr(self.context, "db", None)
        if db is None:
            raise RuntimeError("显式收口缺少原子事务会话")
        grant = (
            await db.execute(
                select(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.id == payload.grant_id,
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if grant is None:
            return DomainClosureDisposition.SUPERSEDED
        if grant.tenant_id != self.context.tenant_id:
            return DomainClosureDisposition.SUPERSEDED
        expected_site_id = getattr(self.context, "site_id", None)
        if expected_site_id is not None and grant.site_id != expected_site_id:
            raise CustomException(
                msg="禁止跨站点收口用户授权任务",
                code=10403,
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if grant.last_event_id != payload.event_id or grant.sync_version != payload.sync_version:
            return DomainClosureDisposition.SUPERSEDED
        if grant.sync_status == "failed":
            grant.active_execution_token = None
            return DomainClosureDisposition.ALREADY_CLOSED
        if grant.sync_status not in {"pending", "processing"}:
            return DomainClosureDisposition.SUPERSEDED
        grant.sync_status = "failed"
        grant.active_execution_token = None
        grant.last_error_code = code
        grant.last_error_message = message[:500]
        grant.next_retry_at = None
        return DomainClosureDisposition.CLOSED

    async def _record_success(self, payload: ControlUserEntitlementTaskPayload) -> bool:
        now = datetime.now(UTC)
        async with self.context.session_factory() as db:
            result = await db.execute(
                update(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.id == payload.grant_id,
                    ControlUserApplicationGrantModel.tenant_id == self.context.tenant_id,
                    ControlUserApplicationGrantModel.last_event_id == payload.event_id,
                    ControlUserApplicationGrantModel.sync_version == payload.sync_version,
                    ControlUserApplicationGrantModel.sync_status == "processing",
                    ControlUserApplicationGrantModel.active_execution_token == self.context.execution_token,
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
                .values(
                    sync_status="succeeded",
                    active_execution_token=None,
                    last_error_code=None,
                    last_error_message=None,
                    next_retry_at=None,
                    last_synced_at=now,
                )
            )
            await db.commit()
            return bool(result.rowcount)
