import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
from app.api.v1.module_control.tenant_provision.task_schema import ControlTenantProvisionTaskPayload, ControlTenantWithProvisionsCreateSchema
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException


def test_provision_requires_central_package():
    with pytest.raises(ValidationError, match="中控套餐"):
        ControlTenantWithProvisionsCreateSchema.model_validate({
            "tenant": {"name": "企业", "code": "demo", "site_id": 3},
            "applications": [{"application_id": 4, "application_package_id": 4}],
        })


@pytest.mark.parametrize("role_tenant,role_status,allowed", [(4, 0, True), (6, 0, False), (4, 1, False)])
def test_worker_checks_site_platform_role(role_tenant, role_status, allowed):
    user = SimpleNamespace(is_superuser=True, roles=[SimpleNamespace(code="SUPER_ADMIN", tenant_id=role_tenant, status=role_status, is_deleted=False)])
    auth = AuthSchema(user=user, tenant_id=4)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(site_id=3)))
    context = SimpleNamespace(tenant_id=4, auth=auth, db=db)
    service = ControlTenantProvisionWorkerService(context)
    service._start_attempt = AsyncMock(return_value=None)
    payload = ControlTenantProvisionTaskPayload(provision_id=4, mode="initial")
    if allowed:
        result = asyncio.run(service.execute(payload))
        assert result["status"] == "already_succeeded"
        assert auth.site_id == 3
    else:
        with pytest.raises(CustomException):
            asyncio.run(service.execute(payload))
        service._start_attempt.assert_not_called()
