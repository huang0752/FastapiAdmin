"""Registered worker handler for Control tenant provisioning."""

from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
from app.api.v1.module_control.tenant_provision.task_schema import ControlTenantProvisionTaskPayload
from app.plugin.module_task.runtime.context import BusinessTaskContext
from app.plugin.module_task.runtime.registry import register_business_task


@register_business_task(
    handler_code="control.tenant_provision",
    module="control",
    max_retries=1,
    retry_backoff_seconds=10,
    payload_schema=ControlTenantProvisionTaskPayload,
)
async def provision_tenant(context: BusinessTaskContext, payload: ControlTenantProvisionTaskPayload) -> dict:
    return await ControlTenantProvisionWorkerService(context).execute(payload)
