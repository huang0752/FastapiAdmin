import asyncio
import time
from types import SimpleNamespace

from app.core import dependencies
from app.core.base_schema import AuthSchema


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
