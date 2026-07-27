"""
安全底座回归测试。

这些测试覆盖认证、公开账号入口、token 轮换和 RBAC 的关键安全边界。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from app.api.v1.module_platform.package.model import PackagePluginModel
from app.api.v1.module_platform.plugin.model import PluginModel, TenantPluginModel
from app.api.v1.module_platform.tenant.model import TenantUserModel
from app.api.v1.module_system.user.model import UserModel
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


def test_platform_tenant_create_adds_initial_admin_membership(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = str(time.time_ns() % 1_000_000_000_000)
    code = f"T{suffix}"

    resp = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={"name": f"租户{suffix}", "code": code},
    )
    assert resp.status_code == 200, resp.text
    tenant_id = resp.json()["data"]["id"]

    import asyncio

    membership = asyncio.run(_get_membership(f"{code}_admin", tenant_id))

    assert membership is not None
    assert membership.role == "owner"
    assert membership.is_default == 1


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
