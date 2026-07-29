"""套餐变更收口回归测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.v1.module_platform.order.model import OrderModel
from app.api.v1.module_platform.order.service import PaymentService
from app.api.v1.module_platform.package.model import PackageModel, PackagePluginModel
from app.api.v1.module_platform.package.schema import PackagePluginSetSchema
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_platform.plugin.model import TenantPluginModel
from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.crud import TenantCRUD
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_platform.tenant.schema import TenantCreateSchema, TenantUpdateSchema
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.user.crud import UserCRUD
from app.core.base_schema import AuthSchema


def _tenant(*, package_id: int | None = 10) -> SimpleNamespace:
    return SimpleNamespace(
        id=2,
        name="测试租户",
        code="tenant2",
        site_id=1,
        package_id=package_id,
        status=0,
        start_time=None,
        end_time=datetime.now() + timedelta(days=10),
        grace_start_time=None,
        contact_email=None,
    )


def _package(package_id: int = 20) -> SimpleNamespace:
    return SimpleNamespace(
        id=package_id,
        name="目标套餐",
        site_id=1,
        status=0,
        max_users=20,
        max_roles=10,
        max_depts=10,
    )


def _scalar_result(items: list) -> SimpleNamespace:
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: items),
    )


def test_package_change_plan_is_set_safe_and_keeps_owner_minimum() -> None:
    plan = TenantService._plan_package_change(
        current_menu_ids=[1, 2],
        package_menu_ids=[2, 3],
        owner_minimum_menu_ids={4},
    )

    assert plan.final_menu_ids == {2, 3, 4}
    assert plan.removed_menu_ids == {1}
    assert plan.added_menu_ids == {3, 4}


def test_package_change_rejects_cross_site_package() -> None:
    tenant = _tenant()
    tenant.site_id = 10
    package = _package()
    package.site_id = 20
    db = SimpleNamespace(get=AsyncMock())

    async def fake_get(model, object_id):
        if model is TenantModel and object_id == tenant.id:
            return tenant
        if model is PackageModel and object_id == package.id:
            return package
        return None

    db.get.side_effect = fake_get
    auth = AuthSchema.model_construct(db=db, tenant_id=1, check_data_scope=False)

    with pytest.raises(Exception, match="套餐.*站点|跨站点"):
        asyncio.run(TenantService(auth).plan_package_change(tenant.id, package.id))


def test_apply_package_change_updates_package_syncs_roles_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = _tenant()
    package = _package()
    db = SimpleNamespace(get=AsyncMock(), flush=AsyncMock())

    async def fake_get(model, object_id):
        if model is TenantModel and object_id == tenant.id:
            return tenant
        if model is PackageModel and object_id == package.id:
            return package
        return None

    db.get.side_effect = fake_get
    auth = AuthSchema.model_construct(
        db=db,
        tenant_id=1,
        user=SimpleNamespace(id=1, is_superuser=True),
        check_data_scope=False,
    )
    sync_calls: list[tuple[int, set[int], set[int] | None]] = []
    plugin_sync_calls: list[tuple[int, int]] = []
    invalidations: list[tuple[int, AuthSchema | None]] = []

    async def fake_package_menus(self, package_id: int) -> list[int]:
        assert package_id == package.id
        return [2, 3]

    async def fake_current_menus(self, tenant_id: int) -> list[int]:
        assert tenant_id == tenant.id
        return [1, 2]

    async def fake_owner_menus(_db) -> set[int]:
        return {4}

    async def fake_sync(_db, tenant_id, available_ids, owner_menu_ids=None) -> None:
        sync_calls.append((tenant_id, available_ids, owner_menu_ids))

    async def fake_plugin_sync(_db, tenant_id, package_id) -> None:
        plugin_sync_calls.append((tenant_id, package_id))

    def fake_invalidate(tenant_id, request_auth=None) -> None:
        invalidations.append((tenant_id, request_auth))

    monkeypatch.setattr(PackageService, "get_package_menu_ids", fake_package_menus)
    monkeypatch.setattr(PackageService, "get_tenant_available_menu_ids", fake_current_menus)
    monkeypatch.setattr(PackageService, "get_owner_minimum_menu_ids", fake_owner_menus)
    monkeypatch.setattr(PackageService, "sync_tenant_role_menus", fake_sync)
    monkeypatch.setattr(
        PackageService,
        "sync_tenant_plugins",
        fake_plugin_sync,
        raising=False,
    )
    monkeypatch.setattr(PackageService, "invalidate_tenant_menu_cache", fake_invalidate)

    plan = asyncio.run(TenantService(auth).apply_package_change(tenant.id, package.id))

    assert tenant.package_id == package.id
    assert plan.final_menu_ids == {2, 3, 4}
    assert sync_calls == [(tenant.id, {2, 3, 4}, {2, 3, 4})]
    assert plugin_sync_calls == [(tenant.id, package.id)]
    assert invalidations == [(tenant.id, auth)]
    db.flush.assert_awaited_once()


def test_sync_tenant_plugins_installs_missing_package_plugins() -> None:
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _scalar_result([7, 8]),
                _scalar_result([]),
            ]
        ),
        add=Mock(),
        flush=AsyncMock(),
    )

    asyncio.run(PackageService.sync_tenant_plugins(db, tenant_id=2, package_id=20))

    installed = [call.args[0] for call in db.add.call_args_list]
    assert {item.plugin_id for item in installed} == {7, 8}
    assert all(item.tenant_id == 2 for item in installed)
    assert all(item.enabled is True for item in installed)
    assert all(item.purchased is True for item in installed)
    assert all(isinstance(item.installed_time, datetime) for item in installed)
    db.flush.assert_awaited_once()


def test_sync_tenant_plugins_reenables_existing_package_plugin() -> None:
    installed_time = datetime.now() - timedelta(days=1)
    tenant_plugin = TenantPluginModel(
        tenant_id=2,
        plugin_id=7,
        enabled=False,
        purchased=False,
        installed_time=installed_time,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _scalar_result([7]),
                _scalar_result([tenant_plugin]),
            ]
        ),
        add=Mock(),
        flush=AsyncMock(),
    )

    asyncio.run(PackageService.sync_tenant_plugins(db, tenant_id=2, package_id=20))

    db.add.assert_not_called()
    assert tenant_plugin.enabled is True
    assert tenant_plugin.purchased is True
    assert tenant_plugin.installed_time == installed_time
    db.flush.assert_awaited_once()


def test_set_plugins_syncs_tenants_using_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(),
                _scalar_result([2, 3]),
            ]
        ),
        add=Mock(),
        flush=AsyncMock(),
    )
    auth = AuthSchema.model_construct(
        db=db,
        tenant_id=1,
        user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
        check_data_scope=False,
    )
    sync_calls: list[tuple[int, int]] = []

    async def fake_sync(_db, tenant_id: int, package_id: int) -> None:
        sync_calls.append((tenant_id, package_id))

    monkeypatch.setattr(
        PackageService,
        "sync_tenant_plugins",
        fake_sync,
        raising=False,
    )

    asyncio.run(
        PackageService(auth).set_plugins(
            package_id=20,
            data=PackagePluginSetSchema(plugin_ids=[7, 8]),
        )
    )

    assert sync_calls == [(2, 20), (3, 20)]
    assert [
        (call.args[0].package_id, call.args[0].plugin_id)
        for call in db.add.call_args_list
        if isinstance(call.args[0], PackagePluginModel)
    ] == [(20, 7), (20, 8)]


def test_create_tenant_syncs_package_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = _tenant(package_id=20)
    package = _package()
    package.is_deleted = False
    site = SimpleNamespace(id=1, status=0, is_deleted=False)
    db = SimpleNamespace(get=AsyncMock(), refresh=AsyncMock())

    async def fake_db_get(model, object_id):
        if model is SiteModel and object_id == site.id:
            return site
        if model is PackageModel and object_id == package.id:
            return package
        return None

    async def fake_tenant_get(self, **kwargs):
        return None

    async def fake_tenant_create(self, data):
        return tenant

    async def fake_user_get(self, **kwargs):
        return None

    async def fake_user_create(self, data):
        return SimpleNamespace(id=99)

    async def fake_ensure_owner(*args, **kwargs) -> None:
        return None

    sync_calls: list[tuple[int, int]] = []

    async def fake_sync(_db, tenant_id: int, package_id: int) -> None:
        sync_calls.append((tenant_id, package_id))

    db.get.side_effect = fake_db_get
    monkeypatch.setattr(TenantCRUD, "get", fake_tenant_get)
    monkeypatch.setattr(TenantCRUD, "create", fake_tenant_create)
    monkeypatch.setattr(UserCRUD, "get", fake_user_get)
    monkeypatch.setattr(UserCRUD, "create", fake_user_create)
    monkeypatch.setattr(TenantService, "ensure_tenant_owner", fake_ensure_owner)
    monkeypatch.setattr(
        PackageService,
        "sync_tenant_plugins",
        fake_sync,
        raising=False,
    )
    auth = AuthSchema.model_construct(
        db=db,
        tenant_id=1,
        user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
        check_data_scope=False,
    )

    asyncio.run(
        TenantService(auth).create(
            TenantCreateSchema(
                name=tenant.name,
                code=tenant.code,
                site_id=tenant.site_id,
                package_id=tenant.package_id,
            )
        )
    )

    assert sync_calls == [(tenant.id, package.id)]


def test_platform_update_uses_shared_package_application(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    package = _package()
    package.is_deleted = False
    site = SimpleNamespace(id=tenant.site_id, status=0, is_deleted=False)
    db = SimpleNamespace(get=AsyncMock())

    async def fake_db_get(model, object_id):
        if model is SiteModel and object_id == site.id:
            return site
        if model is PackageModel and object_id == package.id:
            return package
        return None

    db.get.side_effect = fake_db_get
    auth = AuthSchema.model_construct(
        db=db,
        tenant_id=1,
        user=SimpleNamespace(id=1, is_superuser=True),
        check_data_scope=False,
    )
    applied: list[tuple[int, int]] = []
    update_payloads: list[dict] = []

    async def fake_get_or_404(self, **kwargs):
        return tenant

    async def fake_update(self, id: int, data):
        update_payloads.append(data)
        return tenant

    async def fake_apply(self, tenant_id: int, package_id: int):
        applied.append((tenant_id, package_id))
        tenant.package_id = package_id

    monkeypatch.setattr(TenantCRUD, "get_or_404", fake_get_or_404)
    monkeypatch.setattr(TenantCRUD, "update", fake_update)
    monkeypatch.setattr(TenantService, "apply_package_change", fake_apply)

    result = asyncio.run(TenantService(auth).update(tenant.id, TenantUpdateSchema(package_id=20)))

    assert result.package_id == 20
    assert applied == [(tenant.id, 20)]
    assert all("package_id" not in payload for payload in update_payloads)


@pytest.mark.parametrize("order_type", ["new", "upgrade", "downgrade"])
def test_paid_package_changes_use_shared_package_application(
    monkeypatch: pytest.MonkeyPatch,
    order_type: str,
) -> None:
    tenant = _tenant()
    package = _package()
    db = SimpleNamespace(get=AsyncMock(), flush=AsyncMock())

    async def fake_get(model, object_id):
        if model is PackageModel:
            return package
        if model is TenantModel:
            return tenant
        return None

    db.get.side_effect = fake_get
    auth = AuthSchema.model_construct(db=db, tenant_id=tenant.id, check_data_scope=False)
    applied: list[tuple[int, int]] = []

    async def fake_apply(self, tenant_id: int, package_id: int):
        applied.append((tenant_id, package_id))
        tenant.package_id = package_id

    async def fake_quota(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(TenantService, "apply_package_change", fake_apply)
    monkeypatch.setattr(PaymentService, "_check_downgrade_quota", fake_quota)
    order = OrderModel(
        tenant_id=tenant.id,
        order_no=f"test-{order_type}",
        package_id=package.id,
        order_type=order_type,
        amount=100,
        period_count=1,
        expire_time=datetime.now() + timedelta(minutes=15),
    )

    asyncio.run(PaymentService._activate_tenant_package(auth, order))

    assert applied == [(tenant.id, package.id)]


def test_renewal_does_not_change_tenant_package(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant(package_id=10)
    package = _package(package_id=20)
    old_end_time = tenant.end_time
    db = SimpleNamespace(get=AsyncMock(), flush=AsyncMock())

    async def fake_get(model, object_id):
        if model is PackageModel:
            return package
        if model is TenantModel:
            return tenant
        return None

    db.get.side_effect = fake_get
    auth = AuthSchema.model_construct(db=db, tenant_id=tenant.id, check_data_scope=False)
    applied: list[tuple[int, int]] = []

    async def fake_apply(self, tenant_id: int, package_id: int):
        applied.append((tenant_id, package_id))

    monkeypatch.setattr(TenantService, "apply_package_change", fake_apply)
    order = OrderModel(
        tenant_id=tenant.id,
        order_no="test-renew",
        package_id=package.id,
        order_type="renew",
        amount=100,
        period_count=1,
        expire_time=datetime.now() + timedelta(minutes=15),
    )

    asyncio.run(PaymentService._activate_tenant_package(auth, order))

    assert applied == []
    assert tenant.package_id == 10
    assert tenant.end_time > old_end_time
