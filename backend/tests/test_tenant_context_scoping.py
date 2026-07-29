import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.module_platform.tenant import service as tenant_service_module
from app.api.v1.module_platform.tenant.service import TenantService
from app.core import dependencies
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException


def test_auth_schema_distinguishes_platform_global_from_tenant_impersonation() -> None:
    user = SimpleNamespace(is_superuser=True)

    platform_auth = AuthSchema(user=user, tenant_id=1)
    tenant_auth = AuthSchema(user=user, tenant_id=2)

    assert hasattr(platform_auth, "is_platform_global")
    assert platform_auth.is_platform_global is True
    assert tenant_auth.is_platform_global is False


def test_active_tenant_filters_roles_positions_and_department() -> None:
    assert hasattr(dependencies, "_scope_user_org_context")
    user = SimpleNamespace(
        is_superuser=False,
        roles=[
            SimpleNamespace(status=0, tenant_id=1),
            SimpleNamespace(status=0, tenant_id=2),
            SimpleNamespace(status=1, tenant_id=2),
        ],
        positions=[
            SimpleNamespace(status=0, tenant_id=1),
            SimpleNamespace(status=0, tenant_id=2),
            SimpleNamespace(status=1, tenant_id=2),
        ],
        dept=SimpleNamespace(tenant_id=1),
        dept_id=10,
    )

    dependencies._scope_user_org_context(user, tenant_id=2)

    assert [(role.status, role.tenant_id) for role in user.roles] == [(0, 2)]
    assert [(position.status, position.tenant_id) for position in user.positions] == [(0, 2)]
    assert user.dept is None
    assert user.dept_id is None


def test_permission_check_does_not_trust_process_local_package_cache(monkeypatch) -> None:
    from app.api.v1.module_platform.package.service import PackageService

    async def current_menu_ids(self, tenant_id: int) -> list[int]:
        assert tenant_id == 2
        return [22]

    monkeypatch.setattr(PackageService, "get_tenant_available_menu_ids", current_menu_ids)
    dependencies._package_menu_cache[2] = (time.time(), [999])
    auth = AuthSchema(tenant_id=2)

    result = asyncio.run(dependencies._get_cached_tenant_menu_ids(auth, 2))

    assert result == [22]


def test_tenant_superuser_cannot_list_platform_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock(return_value={"items": [], "total": 0})
    monkeypatch.setattr(
        tenant_service_module,
        "TenantCRUD",
        lambda _auth: SimpleNamespace(page=page),
    )
    auth = AuthSchema(
        tenant_id=2,
        user=SimpleNamespace(
            id=2,
            is_superuser=True,
            roles=[SimpleNamespace(code="owner")],
        ),
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="仅平台管理员可操作") as exc_info:
        asyncio.run(TenantService(auth).page(page_no=1, page_size=10))

    assert exc_info.value.status_code == 403
    page.assert_not_awaited()


def test_site_platform_admin_can_list_platform_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"items": [], "total": 0}
    page = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        tenant_service_module,
        "TenantCRUD",
        lambda _auth: SimpleNamespace(page=page),
    )
    auth = AuthSchema(
        tenant_id=2,
        user=SimpleNamespace(
            id=2,
            is_superuser=True,
            roles=[SimpleNamespace(code="SUPER_ADMIN")],
        ),
        check_data_scope=False,
    )

    result = asyncio.run(TenantService(auth).page(page_no=1, page_size=10))

    assert result == expected
    page.assert_awaited_once()
