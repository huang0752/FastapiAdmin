"""
安全底座回归测试。

这些测试覆盖认证、公开账号入口、token 轮换和 RBAC 的关键安全边界。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from starlette.requests import Request

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackagePluginModel
from app.api.v1.module_platform.package.schema import PackageMenuSetSchema, PackagePluginSetSchema
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_platform.plugin.model import PluginModel, TenantPluginModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_platform.tenant.schema import (
    TenantCreateSchema,
    TenantOutSchema,
    TenantQueryParam,
    TenantUpdateSchema,
)
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.config.setting import settings
from app.core.base_schema import AuthSchema, JWTPayloadSchema
from app.core.database import async_db_session
from app.core.dependencies import AuthPermission
from app.core.discover import validate_dynamic_plugin_access
from app.core.exceptions import CustomException
from app.core.security import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix[:12]}_{time.time_ns() % 1_000_000_000_000}"


def _login(test_client: TestClient, username: str, password: str) -> dict:
    resp = test_client.post(
        "/system/auth/login",
        data={"username": username, "password": password, "login_type": "PC端"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _get_membership(username: str, tenant_id: int) -> TenantUserModel | None:
    async with async_db_session() as db:
        user = (
            await db.execute(
                select(UserModel).where(UserModel.username == username).limit(1)
            )
        ).scalar_one_or_none()
        if not user:
            return None
        return (
            await db.execute(
                select(TenantUserModel)
                .where(TenantUserModel.user_id == user.id, TenantUserModel.tenant_id == tenant_id)
                .limit(1)
            )
        ).scalar_one_or_none()


_OWNER_REQUIRED_PERMISSIONS = {
    "module_platform:workspace:query",
    "module_platform:workspace:update",
    "module_system:dept:create",
    "module_system:dept:delete",
    "module_system:dept:query",
    "module_system:dept:update",
    "module_system:position:create",
    "module_system:position:delete",
    "module_system:position:detail",
    "module_system:position:patch",
    "module_system:position:query",
    "module_system:position:update",
    "module_system:role:create",
    "module_system:role:delete",
    "module_system:role:permission",
    "module_system:role:query",
    "module_system:role:update",
    "module_system:user:create",
    "module_system:user:delete",
    "module_system:user:query",
    "module_system:user:update",
}


async def _get_owner_access(username: str, tenant_id: int) -> tuple[TenantUserModel | None, list[RoleModel], set[str]]:
    async with async_db_session() as db:
        user = (
            await db.execute(select(UserModel).where(UserModel.username == username).limit(1))
        ).scalar_one()
        membership = (
            await db.execute(
                select(TenantUserModel)
                .where(TenantUserModel.user_id == user.id, TenantUserModel.tenant_id == tenant_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        owner_roles = (
            await db.execute(
                select(RoleModel)
                .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                .where(
                    UserRolesModel.user_id == user.id,
                    RoleModel.tenant_id == tenant_id,
                    RoleModel.code == "owner",
                )
            )
        ).scalars().all()
        if not owner_roles:
            return membership, [], set()
        menu_rows = (
            await db.execute(
                select(MenuModel.permission, MenuModel.scope)
                .join(RoleMenusModel, RoleMenusModel.menu_id == MenuModel.id)
                .where(RoleMenusModel.role_id == owner_roles[0].id)
            )
        ).all()
        assert all(scope == "tenant" for _, scope in menu_rows)
        return membership, owner_roles, {permission for permission, _ in menu_rows if permission}


def _assert_owner_access(username: str, tenant_id: int) -> None:
    membership, owner_roles, permissions = asyncio.run(_get_owner_access(username, tenant_id))
    assert membership is not None
    assert membership.role == "owner"
    assert membership.is_default == 1
    assert len(owner_roles) == 1
    assert _OWNER_REQUIRED_PERMISSIONS <= permissions


def _create_user(test_client: TestClient, auth_headers: dict[str, str], username: str, password: str, mobile: str | None = None) -> None:
    payload = {
        "username": username,
        "password": password,
        "name": username,
    }
    if mobile:
        payload["mobile"] = mobile
    resp = test_client.post("/system/user/create", headers=auth_headers, json=payload)
    assert resp.status_code == 200, resp.text


def _request_for_path(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )


def _route_dependency_names(app, path: str, method: str = "GET") -> list[str]:
    for route in app.routes:
        if getattr(route, "path", None) != path or method not in (getattr(route, "methods", None) or set()):
            continue
        return [
            getattr(dep.call, "__name__", dep.call.__class__.__name__)
            for dep in route.dependant.dependencies
        ]
    return []


def _route_auth_permissions(app, path: str, method: str) -> list[str]:
    for route in app.routes:
        if getattr(route, "path", None) != path or method not in (getattr(route, "methods", None) or set()):
            continue
        permissions = [
            dependency.call.permissions
            for dependency in route.dependant.dependencies
            if isinstance(dependency.call, AuthPermission)
        ]
        assert len(permissions) == 1, f"{method} {path} 应且只应声明一个 AuthPermission"
        return permissions[0]
    raise AssertionError(f"未找到路由: {method} {path}")


@pytest.mark.parametrize(
    ("method", "path", "permissions"),
    [
        ("GET", "/generator/gencode/db/list", ["module_generator:dblist:query"]),
        ("POST", "/generator/gencode/import", ["module_generator:gencode:import"]),
        ("PATCH", "/generator/gencode/batch/output", ["module_generator:gencode:operate"]),
        ("GET", "/task/cronjob/job/scheduler/status", ["module_task:cronjob:job:query"]),
        ("GET", "/task/cronjob/job/scheduler/jobs", ["module_task:cronjob:job:query"]),
        ("GET", "/task/cronjob/job/scheduler/console", ["module_task:cronjob:job:query"]),
        ("POST", "/task/cronjob/node/create", ["module_task:cronjob:node:create"]),
        ("PUT", "/task/cronjob/node/update/{id}", ["module_task:cronjob:node:update"]),
        ("DELETE", "/task/cronjob/node/delete", ["module_task:cronjob:node:delete"]),
        ("PATCH", "/task/cronjob/node/status/batch", ["module_task:cronjob:node:update"]),
    ],
)
def test_delegable_framework_routes_keep_granular_permissions(
    test_client: TestClient,
    method: str,
    path: str,
    permissions: list[str],
) -> None:
    assert _route_auth_permissions(test_client.app, path, method) == permissions


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/generator/gencode/create"),
        ("POST", "/generator/gencode/output/{table_name}"),
        ("POST", "/task/cronjob/job/scheduler/start"),
        ("POST", "/task/cronjob/job/scheduler/pause"),
        ("POST", "/task/cronjob/job/scheduler/resume"),
        ("POST", "/task/cronjob/job/scheduler/shutdown"),
        ("DELETE", "/task/cronjob/job/scheduler/jobs/clear"),
        ("POST", "/task/cronjob/job/scheduler/sync"),
        ("POST", "/task/cronjob/job/task/pause/{job_id}"),
        ("POST", "/task/cronjob/job/task/resume/{job_id}"),
        ("POST", "/task/cronjob/job/task/run/{job_id}"),
        ("DELETE", "/task/cronjob/job/task/remove/{job_id}"),
        ("DELETE", "/task/cronjob/node/clear"),
        ("POST", "/task/cronjob/node/execute/{id}"),
    ],
)
def test_high_risk_framework_routes_remain_superuser_only(
    test_client: TestClient,
    method: str,
    path: str,
) -> None:
    assert _route_auth_permissions(test_client.app, path, method) == ["*:*:*"]


def test_public_user_register_requires_authentication(test_client: TestClient) -> None:
    resp = test_client.post(
        "/system/user/register",
        json={"username": _unique("public_reg"), "password": "pass123", "name": "public"},
    )

    assert resp.status_code == 401


def test_current_user_info_requires_authentication(test_client: TestClient) -> None:
    resp = test_client.get("/system/user/current/info")

    assert resp.status_code == 401
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == 10401


def test_current_user_info_rejects_invalid_token(test_client: TestClient) -> None:
    resp = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": "Bearer invalid.token.value"},
    )

    assert resp.status_code == 401
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == 10401


def test_forget_password_requires_matching_mobile(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    username = _unique("forgot")
    _create_user(test_client, auth_headers, username=username, password="oldpass123", mobile="13800000000")

    old_enabled = settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE
    old_mode = settings.AUTH_PASSWORD_RESET_MODE
    settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = True
    settings.AUTH_PASSWORD_RESET_MODE = "legacy_mobile"
    try:
        resp = test_client.post(
            "/system/user/password/forget",
            json={"username": username, "new_password": "newpass123"},
        )
    finally:
        settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = old_enabled
        settings.AUTH_PASSWORD_RESET_MODE = old_mode

    assert resp.status_code in (400, 422)


def test_non_superuser_cannot_create_auto_login_token(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    username = _unique("normal")
    password = "normal123"
    _create_user(test_client, auth_headers, username=username, password=password)
    token = _login(test_client, username=username, password=password)["access_token"]

    resp = test_client.post(
        "/system/auth/auto-login/token?user_id=1",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


def test_non_superuser_can_read_own_current_info(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    username = _unique("normal_info")
    password = "normal123"
    _create_user(test_client, auth_headers, username=username, password=password)
    token = _login(test_client, username=username, password=password)["access_token"]

    resp = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["username"] == username


def test_superuser_can_read_current_info_after_switching_tenant(test_client: TestClient) -> None:
    login = _login(test_client, "admin", "admin123")
    original_headers = {"Authorization": f"Bearer {login['access_token']}"}
    tenants_resp = test_client.get("/system/auth/tenants", headers=original_headers)
    assert tenants_resp.status_code == 200, tenants_resp.text
    target_tenant = next(
        tenant for tenant in tenants_resp.json()["data"] if tenant["id"] != 1
    )

    switch_resp = test_client.post(
        "/system/auth/select-tenant",
        headers=original_headers,
        json={"tenant_id": target_tenant["id"]},
    )
    assert switch_resp.status_code == 200, switch_resp.text
    switched_headers = {
        "Authorization": f"Bearer {switch_resp.json()['data']['access_token']}"
    }

    current_resp = test_client.get(
        "/system/user/current/info",
        headers=switched_headers,
    )

    assert current_resp.status_code == 200, current_resp.text
    assert current_resp.json()["data"]["username"] == "admin"


def test_non_superuser_without_permission_cannot_query_users(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    username = _unique("normal_no_perm")
    password = "normal123"
    _create_user(test_client, auth_headers, username=username, password=password)
    token = _login(test_client, username=username, password=password)["access_token"]

    resp = test_client.get(
        "/system/user/list",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == 10403


def test_superuser_can_query_users(test_client: TestClient) -> None:
    token = _login(test_client, username="admin", password="admin123")["access_token"]

    resp = test_client.get(
        "/system/user/list",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert "items" in body["data"]
    assert "total" in body["data"]


def test_wildcard_permission_does_not_allow_non_superuser(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    username = _unique("normal_wildcard")
    password = "normal123"
    _create_user(test_client, auth_headers, username=username, password=password)
    token = _login(test_client, username=username, password=password)["access_token"]

    resp = test_client.get(
        "/platform/invoice/list",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


def test_old_access_token_is_rejected_after_refresh(test_client: TestClient) -> None:
    login_data = _login(test_client, username="admin", password="admin123")
    old_access = login_data["access_token"]
    refresh_token = login_data["refresh_token"]

    refresh_resp = test_client.post(
        "/system/auth/token/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_resp.status_code == 200, refresh_resp.text

    old_access_resp = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": f"Bearer {old_access}"},
    )

    assert old_access_resp.status_code == 401


def test_old_refresh_token_is_rejected_after_rotation(test_client: TestClient) -> None:
    login_data = _login(test_client, username="admin", password="admin123")
    old_refresh = login_data["refresh_token"]

    first_refresh = test_client.post(
        "/system/auth/token/refresh",
        json={"refresh_token": old_refresh},
    )
    assert first_refresh.status_code == 200, first_refresh.text

    second_refresh = test_client.post(
        "/system/auth/token/refresh",
        json={"refresh_token": old_refresh},
    )

    assert second_refresh.status_code == 401


def test_token_creation_adds_unique_jti_for_same_payload() -> None:
    exp = datetime.now() + timedelta(minutes=5)
    payload = JWTPayloadSchema(sub="same-session", is_refresh=True, exp=exp, iat=1)

    first = create_access_token(payload)
    second = create_access_token(payload)

    assert first != second


def test_tenant_register_creates_membership_and_allows_login(test_client: TestClient) -> None:
    username = _unique("tenantreg")
    password = "tenant123"
    email = f"{username}@example.com"

    old_register = settings.AUTH_LOGIN_REGISTER_ENABLE
    settings.AUTH_LOGIN_REGISTER_ENABLE = True
    try:
        register_resp = test_client.post(
            "/system/auth/tenant/register",
            json={"username": username, "password": password, "email": email},
        )
        assert register_resp.status_code == 200, register_resp.text
    finally:
        settings.AUTH_LOGIN_REGISTER_ENABLE = old_register

    login_data = _login(test_client, username=username, password=password)
    info_resp = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": f"Bearer {login_data['access_token']}"},
    )

    assert info_resp.status_code == 200, info_resp.text
    assert info_resp.json()["data"]["username"] == username
    _assert_owner_access(username, register_resp.json()["data"]["tenant_id"])


def test_platform_tenant_create_adds_initial_admin_membership(
    test_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_platform.tenant import service as tenant_service_module

    log_messages: list[str] = []
    monkeypatch.setattr(
        tenant_service_module.logger,
        "info",
        lambda message, *args, **kwargs: log_messages.append(str(message)),
    )
    suffix = str(time.time_ns() % 1_000_000_000_000)
    code = f"T{suffix}"

    resp = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={"name": f"租户{suffix}", "code": code},
    )
    assert resp.status_code == 200, resp.text
    response_data = resp.json()["data"]
    tenant_id = response_data["id"]
    credentials = response_data["initial_admin"]

    assert credentials["username"] == f"{code}_admin"
    assert len(credentials["password"]) == 12
    assert credentials["password"] not in "\n".join(log_messages)
    _login(test_client, credentials["username"], credentials["password"])

    _assert_owner_access(f"{code}_admin", tenant_id)


async def _verify_package_mutations_require_superadmin() -> None:
    async with async_db_session() as db:
        auth = AuthSchema(db=db, tenant_id=2, check_data_scope=False)
        auth.user = SimpleNamespace(is_superuser=False)
        calls = (
            PackageService(auth).set_menus(999999, PackageMenuSetSchema(menu_ids=[])),
            PackageService(auth).set_plugins(999999, PackagePluginSetSchema(plugin_ids=[])),
        )
        statuses: list[int | None] = []
        for call in calls:
            try:
                await call
            except CustomException as exc:
                statuses.append(exc.status_code)
            else:
                statuses.append(None)
        assert statuses == [403, 403]
        await db.rollback()


def test_package_mutations_require_superadmin_at_service_boundary() -> None:
    asyncio.run(_verify_package_mutations_require_superadmin())


async def _set_tenant_status(tenant_id: int, status: int) -> None:
    async with async_db_session() as db:
        await db.execute(
            update(TenantModel)
            .where(TenantModel.id == tenant_id)
            .values(status=status)
        )
        await db.commit()


def test_grace_tenant_can_login_refresh_and_use_session_but_expired_cannot(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = str(time.time_ns() % 1_000_000_000_000)
    code = f"G{suffix}"
    create_resp = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={"name": f"宽限期租户{suffix}", "code": code},
    )
    assert create_resp.status_code == 200, create_resp.text
    response_data = create_resp.json()["data"]
    tenant_id = response_data["id"]
    credentials = response_data["initial_admin"]

    asyncio.run(_set_tenant_status(tenant_id, 1))
    grace_login = _login(test_client, credentials["username"], credentials["password"])
    current_resp = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": f"Bearer {grace_login['access_token']}"},
    )
    refresh_resp = test_client.post(
        "/system/auth/token/refresh",
        json={"refresh_token": grace_login["refresh_token"]},
    )
    assert current_resp.status_code == 200, current_resp.text
    assert refresh_resp.status_code == 200, refresh_resp.text
    refreshed = refresh_resp.json()["data"]

    asyncio.run(_set_tenant_status(tenant_id, 2))
    denied_login = test_client.post(
        "/system/auth/login",
        data={
            "username": credentials["username"],
            "password": credentials["password"],
            "login_type": "PC端",
        },
    )
    denied_session = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": f"Bearer {refreshed['access_token']}"},
    )
    denied_refresh = test_client.post(
        "/system/auth/token/refresh",
        json={"refresh_token": refreshed["refresh_token"]},
    )
    assert denied_login.status_code == 401
    assert denied_session.status_code == 401
    assert denied_refresh.status_code == 401


async def _verify_ownerless_membership_is_not_promoted() -> None:
    from app.scripts.initialize import InitializeData

    suffix = str(time.time_ns() % 1_000_000_000_000)
    async with async_db_session() as db:
        tenant = TenantModel(name=f"无 owner 租户{suffix}", code=f"O{suffix}", status=0)
        db.add(tenant)
        await db.flush()
        user = UserModel(
            username=f"ownerless_{suffix}",
            password="not-a-plaintext-password",
            name="普通成员",
            tenant_id=tenant.id,
            is_superuser=False,
            status=0,
        )
        db.add(user)
        await db.flush()
        membership = TenantUserModel(
            user_id=user.id,
            tenant_id=tenant.id,
            role="member",
            is_default=1,
        )
        db.add(membership)
        await db.flush()

        await InitializeData()._InitializeData__backfill_tenant_memberships(db)
        await db.refresh(membership)
        owner_role = (
            await db.execute(
                select(RoleModel)
                .where(RoleModel.tenant_id == tenant.id, RoleModel.code == "owner")
                .limit(1)
            )
        ).scalar_one_or_none()
        assert membership.role == "member"
        assert owner_role is None
        await db.rollback()


def test_initializer_does_not_promote_first_user_for_ownerless_tenant() -> None:
    asyncio.run(_verify_ownerless_membership_is_not_promoted())


def test_tenant_create_schema_starts_only_in_active_status() -> None:
    TenantCreateSchema(name="正常租户", code="NormalTenant", status=0)
    with pytest.raises(ValueError):
        TenantCreateSchema(name="非法初始状态", code="BadTenant", status=1)


def test_tenant_update_rejects_lifecycle_status_and_query_accepts_all() -> None:
    for status in range(6):
        with pytest.raises(ValueError, match="状态迁移"):
            TenantUpdateSchema(status=status)
        query = TenantQueryParam(status=status)
        assert query.status is not None
        assert query.status[-1] == status

    with pytest.raises(ValueError):
        TenantQueryParam(status=6)


def test_tenant_out_serializes_all_lifecycle_statuses() -> None:
    for status in range(6):
        payload = TenantOutSchema(name="生命周期租户", code="Lifecycle", status=status)
        assert payload.model_dump()["status"] == status


def test_tenant_batch_status_accepts_only_active_or_suspended() -> None:
    from app.api.v1.module_platform.tenant.schema import TenantBatchStatusSchema

    assert TenantBatchStatusSchema(ids=[2], status=0).status == 0
    assert TenantBatchStatusSchema(ids=[2], status=2).status == 2
    with pytest.raises(ValueError):
        TenantBatchStatusSchema(ids=[2], status=1)


async def _verify_tenant_manual_status_toggle() -> None:
    suffix = str(time.time_ns() % 1_000_000_000_000)
    async with async_db_session() as db:
        tenant = TenantModel(name=f"手工暂停租户{suffix}", code=f"M{suffix}", status=0)
        db.add(tenant)
        await db.flush()
        auth = AuthSchema(db=db, tenant_id=1, check_data_scope=False)
        auth.user = SimpleNamespace(is_superuser=True)
        service = TenantService(auth)

        await service.toggle_status(tenant.id)
        await db.refresh(tenant)
        assert tenant.status == 2

        await service.toggle_status(tenant.id)
        await db.refresh(tenant)
        assert tenant.status == 0
        await db.rollback()


def test_tenant_manual_toggle_uses_suspended_not_grace(test_client: TestClient) -> None:
    asyncio.run(_verify_tenant_manual_status_toggle())


async def _resolve_package_menu_seed() -> None:
    from app.scripts.initialize import InitializeData

    async with async_db_session() as db:
        dept = (
            await db.execute(select(MenuModel).where(MenuModel.route_name == "Dept").limit(1))
        ).scalar_one()
        assert dept.parent_id is not None

        rows = await InitializeData().resolve_package_menu_seed(
            db,
            [{"package_code": "basic", "menus": [{"route_name": "Dept"}]}],
        )
        resolved_ids = {row["menu_id"] for row in rows}
        assert dept.id in resolved_ids
        assert dept.parent_id in resolved_ids

        platform_menu = (
            await db.execute(
                select(MenuModel)
                .where(
                    MenuModel.scope == "platform",
                    MenuModel.route_name.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one()
        with pytest.raises(ValueError, match="platform"):
            await InitializeData().resolve_package_menu_seed(
                db,
                [{"package_code": "basic", "menus": [{"route_name": platform_menu.route_name}]}],
            )


def test_package_menu_seed_uses_stable_identities_and_resolves_parents() -> None:
    seed_path = Path(__file__).parents[1] / "app/scripts/data/platform_package_menu.json"
    seed_rows = json.loads(seed_path.read_text(encoding="utf-8"))

    assert seed_rows
    assert all("package_code" in row and row.get("menus") for row in seed_rows)
    assert all("menu_id" not in row and "package_id" not in row for row in seed_rows)
    asyncio.run(_resolve_package_menu_seed())


async def _verify_package_menu_sync() -> None:
    from app.core.dependencies import _package_menu_cache

    async with async_db_session() as db:
        auth = AuthSchema(db=db, tenant_id=1, check_data_scope=False)
        auth.user = SimpleNamespace(is_superuser=True)
        owner_role = (
            await db.execute(
                select(RoleModel)
                .where(RoleModel.tenant_id == 2, RoleModel.code == "owner")
                .limit(1)
            )
        ).scalar_one()
        staff_role = (
            await db.execute(
                select(RoleModel)
                .where(RoleModel.tenant_id == 2, RoleModel.code == "TEST_STAFF")
                .limit(1)
            )
        ).scalar_one()
        optional_menu = (
            await db.execute(select(MenuModel).where(MenuModel.route_name == "Dict").limit(1))
        ).scalar_one()
        assert optional_menu.parent_id is not None

        await db.execute(
            delete(RoleMenusModel).where(
                RoleMenusModel.role_id.in_([owner_role.id, staff_role.id]),
                RoleMenusModel.menu_id == optional_menu.id,
            )
        )
        _package_menu_cache[2] = (time.time(), [999999])
        await PackageService(auth).set_menus(2, PackageMenuSetSchema(menu_ids=[optional_menu.id]))

        owner_ids = set(
            (
                await db.execute(
                    select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == owner_role.id)
                )
            ).scalars().all()
        )
        assert {optional_menu.id, optional_menu.parent_id} <= owner_ids
        assert 2 not in _package_menu_cache

        if optional_menu.id not in set(
            (
                await db.execute(
                    select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == staff_role.id)
                )
            ).scalars().all()
        ):
            db.add(RoleMenusModel(role_id=staff_role.id, menu_id=optional_menu.id))
            await db.flush()

        _package_menu_cache[2] = (time.time(), [optional_menu.id])
        await PackageService(auth).set_menus(2, PackageMenuSetSchema(menu_ids=[]))

        owner_ids = set(
            (
                await db.execute(
                    select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == owner_role.id)
                )
            ).scalars().all()
        )
        owner_permissions = set(
            (
                await db.execute(
                    select(MenuModel.permission)
                    .join(RoleMenusModel, RoleMenusModel.menu_id == MenuModel.id)
                    .where(RoleMenusModel.role_id == owner_role.id)
                )
            ).scalars().all()
        )
        staff_ids = set(
            (
                await db.execute(
                    select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == staff_role.id)
                )
            ).scalars().all()
        )
        assert optional_menu.id not in owner_ids
        assert optional_menu.id not in staff_ids
        assert _OWNER_REQUIRED_PERMISSIONS <= owner_permissions
        assert 2 not in _package_menu_cache
        await db.rollback()


def test_package_menu_changes_sync_owner_roles_and_invalidate_cache() -> None:
    asyncio.run(_verify_package_menu_sync())


async def _verify_expiry_transitions() -> None:
    now = datetime.now()
    suffix = str(time.time_ns() % 1_000_000_000)
    expected_by_code = {
        f"E{suffix}A": (2, 1),
        f"E{suffix}B": (8, 2),
        f"E{suffix}C": (15, 3),
        f"E{suffix}D": (31, 4),
    }
    async with async_db_session() as db:
        for index, (code, (days_past, expected_status)) in enumerate(expected_by_code.items()):
            db.add(
                TenantModel(
                    name=f"到期状态测试{suffix}{index}",
                    code=code,
                    end_time=now - timedelta(days=days_past),
                    status=max(0, expected_status - 1),
                )
            )
        await db.commit()

    try:
        await TenantService.check_tenant_expiry()
        async with async_db_session() as db:
            rows = (
                await db.execute(
                    select(TenantModel.code, TenantModel.status).where(
                        TenantModel.code.in_(expected_by_code)
                    )
                )
            ).all()
            assert dict(rows) == {
                code: expected_status
                for code, (_, expected_status) in expected_by_code.items()
            }
    finally:
        async with async_db_session() as db:
            await db.execute(delete(TenantModel).where(TenantModel.code.in_(expected_by_code)))
            await db.commit()


def test_expiry_state_machine_advances_transitional_tenants() -> None:
    asyncio.run(_verify_expiry_transitions())


def test_oauth_unsupported_provider_does_not_redirect_to_untrusted_uri(test_client: TestClient) -> None:
    old = settings.OAUTH_ENABLE
    settings.OAUTH_ENABLE = True
    try:
        resp = test_client.get(
            "/system/auth/oauth/notreal/login",
            params={"redirect_uri": "https://evil.example/callback"},
            follow_redirects=False,
        )
    finally:
        settings.OAUTH_ENABLE = old

    assert resp.status_code == 302
    assert resp.headers["location"].startswith(settings.OAUTH_FRONTEND_FALLBACK)
    assert "evil.example" not in resp.headers["location"]


def test_logout_rejects_non_current_session_token(test_client: TestClient) -> None:
    first = _login(test_client, username="admin", password="admin123")
    second = _login(test_client, username="admin", password="admin123")

    logout_resp = test_client.post(
        "/system/auth/logout",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json={"token": first["access_token"]},
    )
    assert logout_resp.status_code == 401

    first_session_resp = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    assert first_session_resp.status_code == 200, first_session_resp.text


async def _set_tenant_plugin_enabled(plugin_code: str, tenant_id: int, enabled: bool) -> None:
    async with async_db_session() as db:
        plugin = (
            await db.execute(select(PluginModel).where(PluginModel.code == plugin_code).limit(1))
        ).scalar_one()
        tenant_plugin = (
            await db.execute(
                select(TenantPluginModel)
                .where(
                    TenantPluginModel.tenant_id == tenant_id,
                    TenantPluginModel.plugin_id == plugin.id,
                )
                .limit(1)
            )
        ).scalar_one()
        tenant_plugin.enabled = enabled
        await db.commit()


async def _validate_generator_plugin_for_tenant(tenant_id: int) -> None:
    async with async_db_session() as db:
        auth = AuthSchema(db=db, tenant_id=tenant_id)
        auth.user = SimpleNamespace(is_superuser=False)
        await validate_dynamic_plugin_access(_request_for_path("/generator/gencode/list"), auth)


async def _validate_path_for_tenant(path: str, tenant_id: int) -> None:
    async with async_db_session() as db:
        auth = AuthSchema(db=db, tenant_id=tenant_id)
        auth.user = SimpleNamespace(is_superuser=False)
        await validate_dynamic_plugin_access(_request_for_path(path), auth)


async def _replace_package_plugins(package_id: int, plugin_codes: list[str]) -> None:
    async with async_db_session() as db:
        plugin_ids = []
        if plugin_codes:
            plugin_ids = (
                await db.execute(select(PluginModel.id).where(PluginModel.code.in_(plugin_codes)))
            ).scalars().all()
        existing = (
            await db.execute(select(PackagePluginModel).where(PackagePluginModel.package_id == package_id))
        ).scalars().all()
        for item in existing:
            await db.delete(item)
        for plugin_id in plugin_ids:
            db.add(PackagePluginModel(package_id=package_id, plugin_id=plugin_id))
        await db.commit()


def test_dynamic_plugin_access_requires_enabled_tenant_plugin() -> None:
    import asyncio

    asyncio.run(_set_tenant_plugin_enabled("code_generator", tenant_id=2, enabled=True))
    asyncio.run(_validate_generator_plugin_for_tenant(tenant_id=2))

    asyncio.run(_set_tenant_plugin_enabled("code_generator", tenant_id=2, enabled=False))
    try:
        with pytest.raises(CustomException):
            asyncio.run(_validate_generator_plugin_for_tenant(tenant_id=2))
    finally:
        asyncio.run(_set_tenant_plugin_enabled("code_generator", tenant_id=2, enabled=True))


def test_dynamic_plugin_access_rejects_empty_package_plugin_list() -> None:
    import asyncio

    asyncio.run(_replace_package_plugins(2, []))
    try:
        with pytest.raises(CustomException):
            asyncio.run(_validate_generator_plugin_for_tenant(tenant_id=2))
    finally:
        asyncio.run(_replace_package_plugins(2, ["code_generator", "ai_assistant"]))


def test_ai_websocket_path_uses_dynamic_plugin_guard() -> None:
    import asyncio

    asyncio.run(_set_tenant_plugin_enabled("ai_assistant", tenant_id=2, enabled=False))
    try:
        with pytest.raises(CustomException):
            asyncio.run(_validate_path_for_tenant("/api/v1/ai/chat/ws", tenant_id=2))
    finally:
        asyncio.run(_set_tenant_plugin_enabled("ai_assistant", tenant_id=2, enabled=True))


def test_plugin_reload_preserves_dynamic_route_dependencies(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    reload_resp = test_client.post("/platform/plugin/reload", headers=auth_headers)
    assert reload_resp.status_code == 200, reload_resp.text

    dependency_names = _route_dependency_names(test_client.app, "/ai/chat/list")

    assert "RateLimiter" in dependency_names
    assert "validate_dynamic_plugin_access" in dependency_names
