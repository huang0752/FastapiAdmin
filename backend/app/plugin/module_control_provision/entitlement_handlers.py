"""Registered worker handler for Control user-entitlement synchronization."""

from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementWorkerService
from app.api.v1.module_control.user_entitlement.task_schema import ControlUserEntitlementTaskPayload
from app.plugin.module_task.runtime.context import (
    BusinessTaskContext,
    BusinessTaskFailureContext,
    DomainClosureDisposition,
)
from app.plugin.module_task.runtime.registry import register_business_task


async def close_user_entitlement_before_handler(
    context: BusinessTaskFailureContext,
    payload: ControlUserEntitlementTaskPayload,
    exc: Exception,
) -> bool | DomainClosureDisposition:
    error_code = getattr(exc, "error_code", "PREFLIGHT_FAILED")
    return await ControlUserEntitlementWorkerService(context)._record_start_failure(
        payload,
        code=error_code,
        message=(
            "用户授权同步任务执行人或租户权限已失效"
            if error_code == "ACTOR_INVALID"
            else "用户授权同步任务前置复核失败"
        ),
    )


@register_business_task(
    handler_code="control.user_entitlement_sync",
    module="control",
    max_retries=2,
    retry_backoff_seconds=10,
    payload_schema=ControlUserEntitlementTaskPayload,
    release_context_db_before_handler=True,
    pre_handler_failure=close_user_entitlement_before_handler,
)
async def sync_user_entitlement(
    context: BusinessTaskContext,
    payload: ControlUserEntitlementTaskPayload,
) -> dict:
    return await ControlUserEntitlementWorkerService(context).execute(payload)
