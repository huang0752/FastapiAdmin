import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.v1.module_platform.menu.crud import MenuCRUD
from app.api.v1.module_system.role.service import RoleService
from app.api.v1.module_system.user.authorization import UserAuthorizationResolver


def test_tenant_assignment_tree_uses_effective_tenant_scope(monkeypatch):
    resolve = AsyncMock(return_value={11, 12})
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(UserAuthorizationResolver, "effective_tenant_menu_ids", resolve)
    monkeypatch.setattr(MenuCRUD, "tree_list", query)
    auth = SimpleNamespace(is_platform_global=False, tenant_id=8, db=object())
    assert asyncio.run(RoleService(auth).assignable_menus()) == []
    resolve.assert_awaited_once()
    assert set(query.call_args.kwargs["search"]["id"][1]) == {11, 12}


def test_no_available_menus_does_not_fall_back_to_full_catalog(monkeypatch):
    monkeypatch.setattr(UserAuthorizationResolver, "effective_tenant_menu_ids", AsyncMock(return_value=set()))
    query = AsyncMock()
    monkeypatch.setattr(MenuCRUD, "tree_list", query)
    auth = SimpleNamespace(is_platform_global=False, tenant_id=8, db=object())
    assert asyncio.run(RoleService(auth).assignable_menus()) == []
    query.assert_not_called()
