import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.module_platform.tenant.controller import TenantRouter
from app.api.v1.module_platform.tenant.service import TenantService
from app.core.dependencies import AuthPermission
from app.core.exceptions import CustomException


def test_current_brand_route_requires_authentication_without_management_permission():
    route = next((r for r in TenantRouter.routes if r.path == '/tenant/current/brand-config'), None)
    assert route is not None
    guards = [d.call for d in route.dependant.dependencies if isinstance(d.call, AuthPermission)]
    assert len(guards) == 1
    assert not guards[0].permissions


def test_current_brand_reads_only_session_tenant_and_whitelisted_fields():
    service = TenantService(SimpleNamespace(tenant_id=17))
    service.get_config = AsyncMock(return_value={'name': '本租户', 'logo_url': '/logo.png', 'max_users': 99, 'contact_email': 'private@example.com'})
    items = asyncio.run(service.get_self_brand_config_items())
    service.get_config.assert_awaited_once_with(17)
    values = {item.config_key: item.config_value for item in items}
    assert values['name'] == '本租户'
    assert 'max_users' not in values
    assert 'contact_email' not in values


def test_current_brand_rejects_missing_session_tenant():
    service = TenantService(SimpleNamespace(tenant_id=None))
    with pytest.raises(CustomException):
        asyncio.run(service.get_self_brand_config_items())
