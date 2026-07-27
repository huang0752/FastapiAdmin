from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_platform.tenant.schema import TenantUpdateSchema, TenantUserAddSchema
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.role.model import RoleModel
from app.api.v1.module_system.role.schema import RolePermissionSettingSchema, RoleUpdateSchema
from app.api.v1.module_system.role.service import RoleService
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.core.base_schema import AuthSchema, BatchSetAvailable
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.scripts.initialize import InitializeData


def _route_permissions(app, path: str, method: str) -> list[str]:
    from app.core.dependencies import AuthPermission

    for route in app.routes:
        if getattr(route, "path", None) != path or method not in (getattr(route, "methods", None) or set()):
            continue
        permissions = [
            dependency.call.permissions
            for dependency in route.dependant.dependencies
            if isinstance(dependency.call, AuthPermission)
        ]
        assert len(permissions) == 1
        return permissions[0]
    raise AssertionError(f"未找到路由: {method} {path}")


def test_tenant_workspace_and_brand_routes_require_explicit_permissions(test_client: TestClient) -> None:
    assert _route_permissions(test_client.app, "/platform/tenant/workspace", "GET") == [
        "module_platform:workspace:query"
    ]
    assert _route_permissions(test_client.app, "/platform/tenant/brand/config", "GET") == [
        "module_platform:workspace:query"
    ]
    assert _route_permissions(test_client.app, "/platform/tenant/brand/config", "PUT") == [
        "module_platform:workspace:update"
    ]


def test_workspace_update_permission_is_seeded_under_tenant_workspace() -> None:
    seed_path = Path(__file__).parents[1] / "app/scripts/data/platform_menu.json"
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    parents: list[str] = []

    def walk(items: list[dict], parent_name: str = "") -> None:
        for item in items:
            if item.get("permission") == "module_platform:workspace:update":
                parents.append(parent_name)
            walk(item.get("children") or [], item.get("name") or "")

    walk(payload)
    assert parents == ["租户工作台"]


async def _verify_existing_menu_tree_is_incrementally_repaired() -> None:
    async with async_db_session() as db:
        workspace = (
            await db.execute(
                select(MenuModel).where(MenuModel.route_name == "PlatformWorkspace").limit(1)
            )
        ).scalar_one()
        query_button = (
            await db.execute(
                select(MenuModel)
                .where(
                    MenuModel.parent_id == workspace.id,
                    MenuModel.type == 3,
                    MenuModel.permission == "module_platform:workspace:query",
                )
                .limit(1)
            )
        ).scalar_one()
        workspace.scope = "platform"
        query_button.scope = "platform"
        await db.execute(
            delete(MenuModel).where(MenuModel.permission == "module_platform:workspace:update")
        )
        await db.flush()

        await InitializeData()._InitializeData__init_data(db)

        await db.refresh(workspace)
        await db.refresh(query_button)
        update_buttons = (
            await db.execute(
                select(MenuModel).where(
                    MenuModel.parent_id == workspace.id,
                    MenuModel.permission == "module_platform:workspace:update",
                )
            )
        ).scalars().all()
        assert workspace.scope == "tenant"
        assert query_button.scope == "tenant"
        assert len(update_buttons) == 1
        assert update_buttons[0].scope == "tenant"
        await db.rollback()


def test_existing_database_gets_workspace_permission_incrementally(test_client: TestClient) -> None:
    asyncio.run(_verify_existing_menu_tree_is_incrementally_repaired())


@pytest.mark.parametrize("status", range(6))
def test_generic_tenant_update_rejects_lifecycle_status(status: int) -> None:
    with pytest.raises(ValueError, match="状态迁移"):
        TenantUpdateSchema(status=status)


async def _exercise_membership_role_sync() -> None:
    suffix = str(time.time_ns() % 1_000_000_000_000)
    async with async_db_session() as db:
        tenant = TenantModel(name=f"成员同步租户{suffix}", code=f"M{suffix}")
        other_tenant = TenantModel(name=f"成员同步对照{suffix}", code=f"O{suffix}")
        db.add_all([tenant, other_tenant])
        await db.flush()

        user = UserModel(
            username=f"member_{suffix}",
            password="unused",
            name="成员同步用户",
            tenant_id=tenant.id,
            status=0,
            is_superuser=False,
        )
        db.add(user)
        await db.flush()

        owner_role = RoleModel(name="租户管理员", code="owner", tenant_id=tenant.id, status=0, data_scope=4)
        custom_role = RoleModel(name="业务角色", code=f"custom_{suffix}", tenant_id=tenant.id, status=0, data_scope=1)
        other_owner_role = RoleModel(name="对照管理员", code="owner", tenant_id=other_tenant.id, status=0, data_scope=4)
        db.add_all([owner_role, custom_role, other_owner_role])
        await db.flush()
        db.add_all(
            [
                UserRolesModel(user_id=user.id, role_id=owner_role.id),
                UserRolesModel(user_id=user.id, role_id=custom_role.id),
                UserRolesModel(user_id=user.id, role_id=other_owner_role.id),
            ]
        )
        await db.flush()

        auth = AuthSchema(
            db=db,
            user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
            tenant_id=1,
            check_data_scope=False,
        )
        service = TenantService(auth)

        # 模拟历史数据：成员关系已删除，但租户 owner RBAC 绑定仍残留。
        await service.add_tenant_user(
            tenant_id=tenant.id,
            data=TenantUserAddSchema(user_id=user.id, role="member"),
        )
        role_codes = set(
            (
                await db.execute(
                    select(RoleModel.code)
                    .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                    .where(UserRolesModel.user_id == user.id, RoleModel.tenant_id == tenant.id)
                )
            ).scalars().all()
        )
        assert role_codes == {"member"}

        membership = (
            await db.execute(
                select(TenantUserModel).where(
                    TenantUserModel.tenant_id == tenant.id,
                    TenantUserModel.user_id == user.id,
                )
            )
        ).scalar_one()
        assert membership.role == "member"

        await service.remove_tenant_user(tenant_id=tenant.id, user_id=user.id)
        remaining_codes = set(
            (
                await db.execute(
                    select(RoleModel.code)
                    .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                    .where(UserRolesModel.user_id == user.id)
                )
            ).scalars().all()
        )
        assert remaining_codes == {"owner"}
        await db.rollback()


def test_membership_role_and_rbac_bindings_stay_in_sync(test_client: TestClient) -> None:
    asyncio.run(_exercise_membership_role_sync())


async def _verify_governance_roles_are_reserved() -> None:
    suffix = str(time.time_ns() % 1_000_000_000_000)
    async with async_db_session() as db:
        tenant = TenantModel(name=f"治理角色租户{suffix}", code=f"G{suffix}")
        db.add(tenant)
        await db.flush()
        owner_role = RoleModel(
            name="租户管理员",
            code="owner",
            tenant_id=tenant.id,
            status=0,
            data_scope=4,
        )
        db.add(owner_role)
        await db.flush()
        auth = AuthSchema(
            db=db,
            user=SimpleNamespace(id=999, is_superuser=False, roles=[]),
            tenant_id=tenant.id,
            check_data_scope=False,
        )
        service = RoleService(auth)

        operations = [
            service.update(
                owner_role.id,
                RoleUpdateSchema(
                    name="改名管理员",
                    code="owner",
                    status=0,
                    data_scope=4,
                ),
            ),
            service.delete([owner_role.id]),
            service.set_permission(
                RolePermissionSettingSchema(
                    role_ids=[owner_role.id],
                    menu_ids=[],
                    data_scope=1,
                )
            ),
            service.set_available(BatchSetAvailable(ids=[owner_role.id], status=1)),
        ]
        for operation in operations:
            with pytest.raises(CustomException, match="治理角色"):
                await operation
        await db.rollback()


def test_governance_roles_cannot_be_mutated_through_role_crud(test_client: TestClient) -> None:
    asyncio.run(_verify_governance_roles_are_reserved())
