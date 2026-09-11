"""Control Provider persistence and application-management contracts."""

from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_control.model import (
    ControlApplicationModel,
    ControlSSOLaunchTicketModel,
    ControlTenantApplicationModel,
    ControlUserApplicationGrantModel,
)
from app.api.v1.module_system.user.model import UserModel
from app.common.enums import EnvironmentEnum
from app.config.setting import settings
from app.core.assembly import reset_assembly_cache
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter, get_current_user
from app.utils.hash_bcrpy_util import PwdUtil

pytestmark = pytest.mark.usefixtures("control_provider_context")


def _unique_columns(model: type) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_contract(model: type, column_name: str) -> tuple[str, str | None, str | None]:
    foreign_keys = list(model.__table__.c[column_name].foreign_keys)
    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    return foreign_key.target_fullname, foreign_key.ondelete, foreign_key.onupdate


def test_control_provider_models_define_site_scoped_unique_constraints() -> None:
    assert _unique_columns(ControlApplicationModel) >= {("site_id", "code")}
    assert _unique_columns(ControlTenantApplicationModel) >= {("tenant_id", "application_id")}
    assert _unique_columns(ControlUserApplicationGrantModel) >= {("tenant_application_id", "user_id")}
    assert ControlSSOLaunchTicketModel.__table__.c.code_hash.unique


def test_control_provider_models_define_required_fields_and_soft_delete() -> None:
    required_columns = {
        ControlApplicationModel: {
            "site_id",
            "code",
            "name",
            "description",
            "icon",
            "base_url",
            "callback_url",
            "client_id",
            "client_secret_hash",
            "status",
            "sort",
        },
        ControlTenantApplicationModel: {
            "site_id",
            "tenant_id",
            "application_id",
            "target_tenant_code",
            "status",
            "opened_at",
        },
        ControlUserApplicationGrantModel: {
            "site_id",
            "tenant_application_id",
            "tenant_id",
            "user_id",
            "status",
            "granted_at",
        },
        ControlSSOLaunchTicketModel: {
            "code_hash",
            "application_id",
            "site_id",
            "tenant_id",
            "user_id",
            "target_tenant_code",
            "status",
            "issued_at",
            "expires_at",
            "redeemed_at",
            "redeemed_ip",
        },
    }

    for model, expected_columns in required_columns.items():
        columns = set(model.__table__.c.keys())
        assert expected_columns <= columns
        assert {"uuid", "is_deleted", "deleted_time"} <= columns

    for model in (
        ControlApplicationModel,
        ControlTenantApplicationModel,
        ControlUserApplicationGrantModel,
    ):
        assert {"created_id", "updated_id", "deleted_id"} <= set(model.__table__.c.keys())


def test_control_provider_models_restrict_referenced_business_records() -> None:
    expected_foreign_keys = {
        (ControlApplicationModel, "site_id"): "platform_site.id",
        (ControlTenantApplicationModel, "site_id"): "platform_site.id",
        (ControlTenantApplicationModel, "tenant_id"): "platform_tenant.id",
        (ControlTenantApplicationModel, "application_id"): "control_application.id",
        (ControlUserApplicationGrantModel, "site_id"): "platform_site.id",
        (ControlUserApplicationGrantModel, "tenant_application_id"): "control_tenant_application.id",
        (ControlUserApplicationGrantModel, "tenant_id"): "platform_tenant.id",
        (ControlUserApplicationGrantModel, "user_id"): "sys_user.id",
        (ControlSSOLaunchTicketModel, "application_id"): "control_application.id",
        (ControlSSOLaunchTicketModel, "site_id"): "platform_site.id",
        (ControlSSOLaunchTicketModel, "tenant_id"): "platform_tenant.id",
        (ControlSSOLaunchTicketModel, "user_id"): "sys_user.id",
    }

    for (model, column_name), target in expected_foreign_keys.items():
        assert _foreign_key_contract(model, column_name) == (target, "RESTRICT", "CASCADE")


def _application_payload(code: str) -> dict[str, object]:
    return {
        "code": code,
        "name": f"应用 {code}",
        "description": "中控 Provider 测试应用",
        "icon": "https://assets.example.com/app.svg",
        "base_url": "http://target.example.test:8100",
        "callback_url": "http://target.example.test:8100/#/auth/control-sso-callback",
        "status": 0,
        "sort": 10,
    }


@pytest.fixture
def control_client(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Temporarily mount product routes without changing the default test Assembly."""
    from app.api.v1.module_control import control_router

    async def no_publish(_task_ids: list[int]) -> None:
        return None

    app = test_client.app
    original_routes = list(app.router.routes)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    monkeypatch.setattr("app.api.v1.module_control.controller.publish_entitlement_tasks", no_publish)
    app.include_router(control_router)
    try:
        yield test_client
    finally:
        app.router.routes[:] = original_routes


def _permission_for_route(method: str, path: str) -> list[str]:
    from app.api.v1.module_control import control_router

    route = next(
        item
        for item in control_router.routes
        if isinstance(item, APIRoute) and item.path == path and method in item.methods
    )
    permissions = [
        dependency.call.permissions
        for dependency in route.dependant.dependencies
        if isinstance(dependency.call, AuthPermission)
    ]
    assert len(permissions) == 1
    return permissions[0]


def test_application_routes_declare_exact_management_permissions() -> None:
    assert _permission_for_route("GET", "/control/applications") == ["module_control:application:query"]
    assert _permission_for_route("GET", "/control/applications/{application_id}") == ["module_control:application:query"]
    assert _permission_for_route("POST", "/control/applications") == ["module_control:application:create"]
    assert _permission_for_route("PUT", "/control/applications/{application_id}") == ["module_control:application:update"]
    assert _permission_for_route("DELETE", "/control/applications/{application_id}") == ["module_control:application:delete"]
    assert _permission_for_route("POST", "/control/applications/{application_id}/reset-secret") == ["module_control:application:reset_secret"]


def test_application_url_schema_allows_dev_http_but_requires_prod_https(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from app.api.v1.module_control.schema import ControlApplicationCreateSchema

    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.DEV)
    assert ControlApplicationCreateSchema(**_application_payload("dev-http")).base_url.startswith("http://")

    invalid_payload = _application_payload("invalid-relative")
    invalid_payload["callback_url"] = "/auth/callback"
    with pytest.raises(ValidationError):
        ControlApplicationCreateSchema(**invalid_payload)

    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.PROD)
    with pytest.raises(ValidationError):
        ControlApplicationCreateSchema(**_application_payload("prod-http"))

    production_payload = _application_payload("prod-https")
    production_payload["base_url"] = "https://target.example.test"
    production_payload["callback_url"] = "https://target.example.test/#/auth/control-sso-callback"
    schema = ControlApplicationCreateSchema(**production_payload)
    assert schema.callback_url == production_payload["callback_url"]


def test_application_api_secret_is_one_time_and_queries_never_leak(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    code = f"app-{uuid4().hex[:10]}"
    create_response = control_client.post("/control/applications", json=_application_payload(code), headers=auth_headers)
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()["data"]
    application_id = created["id"]
    plain_secret = created["client_secret"]
    assert plain_secret
    assert created["client_id"].startswith("ctrl_")
    assert "client_secret_hash" not in create_response.text

    detail_response = control_client.get(f"/control/applications/{application_id}", headers=auth_headers)
    list_response = control_client.get("/control/applications", headers=auth_headers)
    assert detail_response.status_code == 200, detail_response.text
    assert list_response.status_code == 200, list_response.text
    assert "client_secret" not in detail_response.text
    assert "client_secret_hash" not in detail_response.text
    assert "client_secret" not in list_response.text
    assert "client_secret_hash" not in list_response.text
    assert plain_secret not in detail_response.text
    assert plain_secret not in list_response.text
    assert any(item["id"] == application_id for item in list_response.json()["data"]["items"])

    update_response = control_client.put(
        f"/control/applications/{application_id}",
        json={"name": "已更新应用"},
        headers=auth_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["data"]["name"] == "已更新应用"
    assert "client_secret" not in update_response.text

    reset_response = control_client.post(f"/control/applications/{application_id}/reset-secret", headers=auth_headers)
    assert reset_response.status_code == 200, reset_response.text
    reset_data = reset_response.json()["data"]
    assert reset_data["client_secret"]
    assert reset_data["client_secret"] != plain_secret
    assert reset_data["client_id"] == created["client_id"]
    assert "client_secret_hash" not in reset_response.text

    async def verify_hashes() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            application = await db.get(ControlApplicationModel, application_id)
            assert application is not None
            assert application.client_secret_hash not in {plain_secret, reset_data["client_secret"]}
            assert not PwdUtil.verify_password(plain_secret, application.client_secret_hash)
            assert PwdUtil.verify_password(reset_data["client_secret"], application.client_secret_hash)

    asyncio.run(verify_hashes())


def test_application_api_rejects_duplicate_code_and_cross_site_access(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    code = f"dup-{uuid4().hex[:10]}"
    first = control_client.post("/control/applications", json=_application_payload(code), headers=auth_headers)
    duplicate = control_client.post("/control/applications", json=_application_payload(code), headers=auth_headers)
    assert first.status_code == 200, first.text
    assert duplicate.status_code == 400, duplicate.text

    async def create_cross_site_application() -> int:
        from app.api.v1.module_platform.site.model import SiteModel
        from app.core.database import async_db_session

        async with async_db_session() as db:
            site = SiteModel(code=f"site_{uuid4().hex[:10]}", name="隔离站点", status=0)
            db.add(site)
            await db.flush()
            application = ControlApplicationModel(
                site_id=site.id,
                code=f"cross-{uuid4().hex[:8]}",
                name="跨站应用",
                base_url="http://cross.example.test",
                callback_url="http://cross.example.test/#/auth/control-sso-callback",
                client_id=f"ctrl_{uuid4().hex}",
                client_secret_hash=PwdUtil.hash_password("cross-site-secret"),
                status=0,
                sort=0,
            )
            db.add(application)
            await db.commit()
            return application.id

    cross_site_id = asyncio.run(create_cross_site_application())
    detail = control_client.get(f"/control/applications/{cross_site_id}", headers=auth_headers)
    update = control_client.put(
        f"/control/applications/{cross_site_id}",
        json={"name": "越权修改"},
        headers=auth_headers,
    )
    assert detail.status_code == 403, detail.text
    assert update.status_code == 403, update.text


def test_application_delete_rejects_active_tenant_opening(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    create_response = control_client.post(
        "/control/applications",
        json=_application_payload(f"bound-{uuid4().hex[:10]}"),
        headers=auth_headers,
    )
    assert create_response.status_code == 200, create_response.text
    application_id = create_response.json()["data"]["id"]

    async def bind_application() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            application = await db.get(ControlApplicationModel, application_id)
            assert application is not None
            db.add(
                ControlTenantApplicationModel(
                    site_id=application.site_id,
                    tenant_id=1,
                    application_id=application.id,
                    target_tenant_code="platform",
                    status=0,
                )
            )
            await db.commit()

    asyncio.run(bind_application())
    response = control_client.delete(f"/control/applications/{application_id}", headers=auth_headers)
    assert response.status_code == 409, response.text

    unbound = control_client.post(
        "/control/applications",
        json=_application_payload(f"unbound-{uuid4().hex[:10]}"),
        headers=auth_headers,
    )
    assert unbound.status_code == 200, unbound.text
    unbound_id = unbound.json()["data"]["id"]
    deleted = control_client.delete(f"/control/applications/{unbound_id}", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text

    async def verify_soft_delete() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            application = await db.get(ControlApplicationModel, unbound_id)
            assert application is not None
            assert application.is_deleted is True

    asyncio.run(verify_soft_delete())


def test_tenant_admin_cannot_manage_applications(control_client: TestClient) -> None:
    async def tenant_auth(db: Annotated[AsyncSession, Depends(db_getter)]) -> AuthSchema:
        return AuthSchema(
            db=db,
            user=SimpleNamespace(id=999_999, is_superuser=False, roles=[]),
            tenant_id=2,
            site_id=1,
        )

    app = control_client.app
    original_override = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = tenant_auth
    try:
        response = control_client.post(
            "/control/applications",
            json=_application_payload(f"forbidden-{uuid4().hex[:8]}"),
        )
    finally:
        if original_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = original_override
    assert response.status_code == 403, response.text

    async def tenant_scoped_superuser(db: Annotated[AsyncSession, Depends(db_getter)]) -> AuthSchema:
        return AuthSchema(
            db=db,
            user=SimpleNamespace(id=999_998, is_superuser=True, roles=[]),
            tenant_id=2,
            site_id=1,
        )

    app.dependency_overrides[get_current_user] = tenant_scoped_superuser
    try:
        platform_identity_response = control_client.post(
            "/control/applications",
            json=_application_payload(f"tenant-super-{uuid4().hex[:8]}"),
        )
    finally:
        if original_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = original_override
    assert platform_identity_response.status_code == 403, platform_identity_response.text


def test_control_routes_are_registered_only_for_control_assembly(tmp_path: Path) -> None:
    from app.core.http_limit import TenantPackageRateLimiter
    from app.init_app import register_routers

    original_file = settings.APP_ASSEMBLY_FILE
    original_name = settings.APP_ASSEMBLY

    def app_for(name: str, provider: bool = False) -> FastAPI:
        config = tmp_path / f"{name}.toml"
        config.write_text(f'[assembly]\nname = "{name}"\n[features]\nflags = {{ sso_provider = {str(provider).lower()} }}\n', encoding="utf-8")
        settings.APP_ASSEMBLY_FILE = str(config)
        settings.APP_ASSEMBLY = name
        reset_assembly_cache()
        app = FastAPI()
        register_routers(app)
        return app

    try:
        control_app = app_for("renamed-provider", provider=True)
        control_paths = {route.path for route in control_app.routes}
        default_paths = {route.path for route in app_for("default").routes}
        assert {"/control/applications", "/control/sso/exchange"} <= control_paths
        assert {"/control/applications", "/control/sso/exchange"}.isdisjoint(default_paths)
        exchange_route = next(route for route in control_app.routes if isinstance(route, APIRoute) and route.path == "/control/sso/exchange")
        assert any(isinstance(dependency.call, TenantPackageRateLimiter) for dependency in exchange_route.dependant.dependencies)
    finally:
        settings.APP_ASSEMBLY_FILE = original_file
        settings.APP_ASSEMBLY = original_name
        reset_assembly_cache()


def _opening_payload(tenant_id: int, application_id: int, target_tenant_code: str = "targettenant") -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "application_id": application_id,
        "target_tenant_code": target_tenant_code,
        "status": 0,
    }


async def _create_tenant(*, site_id: int = 1) -> int:
    from app.api.v1.module_platform.tenant.model import TenantModel
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    async with async_db_session() as db:
        tenant = TenantModel(name=f"中控测试租户{suffix}", code=f"tenant{suffix}", site_id=site_id, status=0)
        db.add(tenant)
        await db.commit()
        return tenant.id


async def _create_site() -> int:
    from app.api.v1.module_platform.site.model import SiteModel
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    async with async_db_session() as db:
        site = SiteModel(code=f"site_{suffix}", name=f"测试站点{suffix}", status=0)
        db.add(site)
        await db.commit()
        return site.id


async def _create_user(*, tenant_id: int = 1, member_tenant_id: int | None = None, is_superuser: bool = False) -> int:
    from app.api.v1.module_platform.tenant.model import TenantUserModel
    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    async with async_db_session() as db:
        user = UserModel(
            tenant_id=tenant_id,
            username=f"grant_{suffix}",
            password=PwdUtil.hash_password("test-password"),
            name=f"授权用户{suffix}",
            email=f"{suffix}@example.test",
            is_superuser=is_superuser,
            status=0,
        )
        db.add(user)
        await db.flush()
        if member_tenant_id is not None:
            db.add(TenantUserModel(user_id=user.id, tenant_id=member_tenant_id, role="member"))
        await db.commit()
        return user.id


async def _set_membership_role(user_id: int, tenant_id: int, role: str) -> None:
    from app.api.v1.module_platform.tenant.model import TenantUserModel
    from app.core.database import async_db_session

    async with async_db_session() as db:
        membership = (
            await db.execute(
                select(TenantUserModel).where(
                    TenantUserModel.user_id == user_id,
                    TenantUserModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        membership.role = role
        await db.commit()


async def _tenant_actor(permission: str | list[str], *, tenant_id: int = 1, site_id: int = 1) -> SimpleNamespace:
    from app.api.v1.module_platform.menu.model import MenuModel
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    permissions = [permission] if isinstance(permission, str) else permission
    async with async_db_session() as db:
        menus = [
            MenuModel(
                name=f"授权测试{suffix}-{index}",
                type=3,
                order=999,
                permission=item,
                scope="tenant",
                status=0,
            )
            for index, item in enumerate(permissions)
        ]
        db.add_all(menus)
        await db.commit()
        menu_ids = [menu.id for menu in menus]
    actor_id = await _create_user(tenant_id=tenant_id, member_tenant_id=tenant_id)
    role = SimpleNamespace(
        code=f"GRANT_{suffix}",
        tenant_id=tenant_id,
        status=0,
        menus=[SimpleNamespace(id=menu_id, permission=item, status=0) for menu_id, item in zip(menu_ids, permissions, strict=True)],
    )
    return SimpleNamespace(id=actor_id, is_superuser=False, roles=[role])


@contextmanager
def _as_tenant_actor(client: TestClient, actor: SimpleNamespace, *, tenant_id: int = 1, site_id: int = 1):
    async def tenant_auth(db: Annotated[AsyncSession, Depends(db_getter)]) -> AuthSchema:
        return AuthSchema(db=db, user=actor, tenant_id=tenant_id, site_id=site_id)

    app = client.app
    original_override = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = tenant_auth
    try:
        yield
    finally:
        if original_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = original_override


def _create_application(client: TestClient, headers: dict[str, str]) -> int:
    response = client.post(
        "/control/applications",
        json=_application_payload(f"opening-{uuid4().hex[:10]}"),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _open_application(client: TestClient, headers: dict[str, str], *, tenant_id: int, application_id: int) -> int:
    response = client.post(
        "/control/tenant-applications",
        json=_opening_payload(tenant_id, application_id),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_tenant_application_routes_declare_exact_permissions() -> None:
    assert _permission_for_route("GET", "/control/tenant-applications") == ["module_control:tenant_application:query"]
    assert _permission_for_route("GET", "/control/tenant-applications/available") == ["module_control:user_grant:query"]
    assert _permission_for_route("POST", "/control/tenant-applications") == ["module_control:tenant_application:create"]
    assert _permission_for_route("PUT", "/control/tenant-applications/{tenant_application_id}") == ["module_control:tenant_application:update"]
    assert _permission_for_route("DELETE", "/control/tenant-applications/{tenant_application_id}") == ["module_control:tenant_application:delete"]
    assert _permission_for_route("GET", "/control/tenant-applications/{tenant_application_id}/grants") == ["module_control:user_grant:query"]
    assert _permission_for_route("PUT", "/control/tenant-applications/{tenant_application_id}/grants/{user_id}") == ["module_control:user_grant:update"]
    assert _permission_for_route("DELETE", "/control/tenant-applications/{tenant_application_id}/grants/{user_id}") == ["module_control:user_grant:delete"]
    assert _permission_for_route("POST", "/control/tenant-applications/{tenant_application_id}/grants/{user_id}/retry") == [
        "module_control:user_grant:retry"
    ]
    from app.api.v1.module_control import control_router

    grant_route = next(
        item
        for item in control_router.routes
        if isinstance(item, APIRoute)
        and item.path == "/control/tenant-applications/{tenant_application_id}/grants/{user_id}"
        and "PUT" in item.methods
    )
    assert grant_route.dependant.body_params == []


def test_tenant_admin_lists_only_current_tenant_available_applications(
    control_client: TestClient,
) -> None:
    async def seed_visibility_cases() -> tuple[int, int, int, str, set[int]]:
        from app.core.database import async_db_session

        other_tenant_id = await _create_tenant()
        other_site_id = await _create_site()
        other_site_tenant_id = await _create_tenant(site_id=other_site_id)
        suffix = uuid4().hex[:10]
        async with async_db_session() as db:
            applications = [
                ControlApplicationModel(
                    site_id=site_id,
                    code=f"available-{suffix}-{index}",
                    name=f"可用应用场景{index}",
                    base_url=f"https://target-{index}.example.test",
                    callback_url=f"https://target-{index}.example.test/#/auth/control-sso-callback",
                    client_id=f"available-client-{suffix}-{index}",
                    client_secret_hash="not-used-by-this-test",
                    status=application_status,
                    is_deleted=application_deleted,
                )
                for index, (site_id, application_status, application_deleted) in enumerate(
                    [
                        (1, 0, False),
                        (1, 0, False),
                        (1, 1, False),
                        (1, 0, True),
                        (1, 0, False),
                        (1, 0, False),
                        (other_site_id, 0, False),
                    ]
                )
            ]
            db.add_all(applications)
            await db.flush()
            openings = [
                ControlTenantApplicationModel(
                    site_id=site_id,
                    tenant_id=tenant_id,
                    application_id=application.id,
                    target_tenant_code=f"target{index}",
                    status=opening_status,
                    is_deleted=opening_deleted,
                )
                for index, (site_id, tenant_id, application, opening_status, opening_deleted) in enumerate(
                    [
                        (1, 1, applications[0], 0, False),
                        (1, 1, applications[1], 1, False),
                        (1, 1, applications[2], 0, False),
                        (1, 1, applications[3], 0, False),
                        (1, 1, applications[4], 0, True),
                        (1, other_tenant_id, applications[5], 0, False),
                        (other_site_id, other_site_tenant_id, applications[6], 0, False),
                    ]
                )
            ]
            db.add_all(openings)
            await db.flush()
            active_opening_id = openings[0].id
            active_application_id = applications[0].id
            excluded_opening_ids = {opening.id for opening in openings[1:]}
            await db.commit()
        return active_opening_id, active_application_id, other_tenant_id, f"available-{suffix}-0", excluded_opening_ids

    active_opening_id, active_application_id, other_tenant_id, active_application_code, excluded_opening_ids = asyncio.run(seed_visibility_cases())
    actor = asyncio.run(_tenant_actor("module_control:user_grant:query"))
    with _as_tenant_actor(control_client, actor):
        response = control_client.request(
            "GET",
            "/control/tenant-applications/available",
            params={"tenant_id": other_tenant_id, "site_id": 999_999},
            json={"tenant_id": other_tenant_id, "site_id": 999_999},
        )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert {
        "tenant_application_id": active_opening_id,
        "application_id": active_application_id,
        "application_code": active_application_code,
        "application_name": "可用应用场景0",
        "target_tenant_code": "target0",
        "status": 0,
    } in data
    assert excluded_opening_ids.isdisjoint({item["tenant_application_id"] for item in data})


def test_available_tenant_applications_requires_canonical_query_permission(
    control_client: TestClient,
) -> None:
    actor = asyncio.run(_tenant_actor("module_control:user_application_grant:query"))
    with _as_tenant_actor(control_client, actor):
        response = control_client.get("/control/tenant-applications/available")
    assert response.status_code == 403, response.text


def test_platform_admin_opens_application_for_same_site_tenant(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application_id = _create_application(control_client, auth_headers)
    tenant_id = asyncio.run(_create_tenant())
    response = control_client.post(
        "/control/tenant-applications",
        json=_opening_payload(tenant_id, application_id, "mappedtenant"),
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] | {"tenant_id": tenant_id, "application_id": application_id, "site_id": 1, "target_tenant_code": "mappedtenant", "status": 0} == response.json()["data"]


def test_opening_rejects_cross_site_tenant(control_client: TestClient, auth_headers: dict[str, str]) -> None:
    application_id = _create_application(control_client, auth_headers)
    other_site_id = asyncio.run(_create_site())
    other_tenant_id = asyncio.run(_create_tenant(site_id=other_site_id))
    response = control_client.post(
        "/control/tenant-applications",
        json=_opening_payload(other_tenant_id, application_id),
        headers=auth_headers,
    )
    assert response.status_code == 403, response.text


def test_opening_is_idempotent(control_client: TestClient, auth_headers: dict[str, str]) -> None:
    application_id = _create_application(control_client, auth_headers)
    tenant_id = asyncio.run(_create_tenant())
    first = control_client.post("/control/tenant-applications", json=_opening_payload(tenant_id, application_id), headers=auth_headers)
    second = control_client.post("/control/tenant-applications", json=_opening_payload(tenant_id, application_id), headers=auth_headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    tenant_application_id = first.json()["data"]["id"]
    assert control_client.delete(f"/control/tenant-applications/{tenant_application_id}", headers=auth_headers).status_code == 200
    assert control_client.delete(f"/control/tenant-applications/{tenant_application_id}", headers=auth_headers).status_code == 200
    reopened = control_client.post("/control/tenant-applications", json=_opening_payload(tenant_id, application_id), headers=auth_headers)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["id"] == tenant_application_id
    assert reopened.json()["data"]["status"] == 0
    assert reopened.json()["data"]["is_deleted"] is False


def test_tenant_admin_grants_current_tenant_member(control_client: TestClient, auth_headers: dict[str, str]) -> None:
    application_id = _create_application(control_client, auth_headers)
    tenant_application_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application_id)
    user_id = asyncio.run(_create_user(member_tenant_id=1))
    actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, actor):
        response = control_client.put(f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["tenant_id"] == 1
    assert response.json()["data"]["user_id"] == user_id

    query_actor = asyncio.run(_tenant_actor("module_control:user_grant:query"))
    with _as_tenant_actor(control_client, query_actor):
        grants = control_client.get(f"/control/tenant-applications/{tenant_application_id}/grants")
    assert grants.status_code == 200, grants.text
    granted_member = next(item for item in grants.json()["data"] if item["user_id"] == user_id)
    assert granted_member["granted"] is True
    assert granted_member["username"].startswith("grant_")


def test_grant_rejects_non_member(control_client: TestClient, auth_headers: dict[str, str]) -> None:
    application_id = _create_application(control_client, auth_headers)
    tenant_application_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application_id)
    user_id = asyncio.run(_create_user(member_tenant_id=None))
    actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, actor):
        response = control_client.put(f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}")
    assert response.status_code == 403, response.text


def test_grant_rejects_other_tenant(control_client: TestClient, auth_headers: dict[str, str]) -> None:
    application_id = _create_application(control_client, auth_headers)
    other_tenant_id = asyncio.run(_create_tenant())
    tenant_application_id = _open_application(control_client, auth_headers, tenant_id=other_tenant_id, application_id=application_id)
    user_id = asyncio.run(_create_user(member_tenant_id=other_tenant_id))
    actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, actor, tenant_id=1):
        response = control_client.put(
            f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}",
            json={"tenant_id": other_tenant_id},
        )
    assert response.status_code == 403, response.text


def test_grant_rejects_legacy_permission_without_canonical_update(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application_id = _create_application(control_client, auth_headers)
    tenant_application_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application_id)
    user_id = asyncio.run(_create_user(member_tenant_id=1))
    actor = asyncio.run(_tenant_actor("module_control:user_application_grant:update"))
    with _as_tenant_actor(control_client, actor):
        response = control_client.put(f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}")
    assert response.status_code == 403, response.text


def test_revoke_is_idempotent(control_client: TestClient, auth_headers: dict[str, str]) -> None:
    application_id = _create_application(control_client, auth_headers)
    tenant_application_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application_id)
    user_id = asyncio.run(_create_user(member_tenant_id=1))
    update_actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, update_actor):
        first_grant = control_client.put(f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}")
        second_grant = control_client.put(f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}")
    assert first_grant.status_code == second_grant.status_code == 200
    assert first_grant.json()["data"]["id"] == second_grant.json()["data"]["id"]

    delete_actor = asyncio.run(_tenant_actor("module_control:user_grant:delete"))
    with _as_tenant_actor(control_client, delete_actor):
        first_revoke = control_client.delete(f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}")
        second_revoke = control_client.delete(f"/control/tenant-applications/{tenant_application_id}/grants/{user_id}")
    assert first_revoke.status_code == second_revoke.status_code == 200

    async def verify_no_ticket_or_target_role() -> None:
        from sqlalchemy import func, select

        from app.api.v1.module_system.user.model import UserRolesModel
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket_count = (
                await db.execute(select(func.count()).select_from(ControlSSOLaunchTicketModel).where(ControlSSOLaunchTicketModel.user_id == user_id))
            ).scalar_one()
            role_count = (
                await db.execute(select(func.count()).select_from(UserRolesModel).where(UserRolesModel.user_id == user_id))
            ).scalar_one()
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel).where(
                        ControlUserApplicationGrantModel.tenant_application_id == tenant_application_id,
                        ControlUserApplicationGrantModel.user_id == user_id,
                    )
                )
            ).scalar_one()
            assert grant.status == 1
            assert grant.desired_state == "inactive"
            assert grant.sync_status == "pending"
            assert grant.is_deleted is False
            assert ticket_count == 0
            assert role_count == 0

    asyncio.run(verify_no_ticket_or_target_role())


def _prepare_portal_access(control_client: TestClient, auth_headers: dict[str, str], *, callback_url: str | None = None):
    code = f"portal-{uuid4().hex[:10]}"
    payload = _application_payload(code)
    if callback_url is not None:
        payload["callback_url"] = callback_url
    created = control_client.post("/control/applications", json=payload, headers=auth_headers)
    assert created.status_code == 200, created.text
    application = created.json()["data"]
    opening_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])
    portal_actor = asyncio.run(_tenant_actor(["module_control:portal:query", "module_control:portal:launch"]))
    grant_actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, grant_actor):
        granted = control_client.put(f"/control/tenant-applications/{opening_id}/grants/{portal_actor.id}")
    assert granted.status_code == 200, granted.text

    async def mark_target_sync_succeeded() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel).where(
                        ControlUserApplicationGrantModel.tenant_application_id == opening_id,
                        ControlUserApplicationGrantModel.user_id == portal_actor.id,
                    )
                )
            ).scalar_one()
            grant.sync_status = "succeeded"
            grant.last_synced_at = datetime.now(UTC)
            await db.commit()

    asyncio.run(mark_target_sync_succeeded())
    return application, opening_id, portal_actor


def _launch(control_client: TestClient, actor: SimpleNamespace, application_code: str):
    with _as_tenant_actor(control_client, actor):
        return control_client.post(f"/control/portal/applications/{application_code}/launch")


def _launch_code(response) -> str:
    redirect = urlsplit(response.json()["data"]["redirect_url"])
    fragment = urlsplit(redirect.fragment)
    query = fragment.query if fragment.path.startswith("/") else redirect.query
    values = parse_qs(query, keep_blank_values=True)
    assert set(values) == {"code"}
    assert len(values["code"]) == 1
    return values["code"][0]


def _exchange(control_client: TestClient, application: dict[str, object], plain_code: str, *, secret: str | None = None):
    return control_client.post(
        "/control/sso/exchange",
        json={"code": plain_code},
        auth=(str(application["client_id"]), secret or str(application["client_secret"])),
    )


def test_portal_routes_declare_permissions_and_public_exchange_has_no_bearer_dependency() -> None:
    assert _permission_for_route("GET", "/control/portal/my-applications") == ["module_control:portal:query"]
    assert _permission_for_route("POST", "/control/portal/applications/{code}/launch") == ["module_control:portal:launch"]

    from app.api.v1.module_control import control_router

    exchange_route = next(
        route
        for route in control_router.routes
        if isinstance(route, APIRoute) and route.path == "/control/sso/exchange" and "POST" in route.methods
    )
    assert all(not isinstance(dependency.call, AuthPermission) for dependency in exchange_route.dependant.dependencies)


def test_exchange_issuer_keeps_the_full_api_prefix() -> None:
    from app.api.v1.module_control.controller import _issuer_from_exchange_url

    assert _issuer_from_exchange_url("http://testserver/control/sso/exchange") == "http://testserver"
    assert _issuer_from_exchange_url("https://control.example.com/api/v1/control/sso/exchange") == "https://control.example.com/api/v1"


def test_sso_exchange_uses_shared_control_client_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.module_control import service as control_service

    calls: list[tuple[str, str]] = []

    async def fake_authenticate(db, *, client_id: str, client_secret: str):
        calls.append((client_id, client_secret))
        raise control_service.CustomException(msg="认证探针", status_code=418)

    monkeypatch.setattr(control_service, "authenticate_control_client", fake_authenticate)

    async def execute_probe() -> None:
        with pytest.raises(control_service.CustomException, match="认证探针"):
            await control_service.ControlSSOExchangeService.exchange(
                object(),
                client_id="shared-client",
                client_secret="shared-secret",
                plain_code="unused-code",
                issuer="https://control.example.com",
                request_ip=None,
            )

    asyncio.run(execute_probe())
    assert calls == [("shared-client", "shared-secret")]


def test_my_applications_is_current_site_tenant_user_active_intersection(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application, opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)

    with _as_tenant_actor(control_client, portal_actor):
        response = control_client.get("/control/portal/my-applications")
    assert response.status_code == 200, response.text
    assert [item["code"] for item in response.json()["data"]] == [application["code"]]

    switched_tenant_id = asyncio.run(_create_tenant())

    async def add_switched_membership() -> None:
        from app.api.v1.module_platform.tenant.model import TenantUserModel
        from app.core.database import async_db_session

        async with async_db_session() as db:
            db.add(TenantUserModel(user_id=portal_actor.id, tenant_id=switched_tenant_id, role="member"))
            await db.commit()

    asyncio.run(add_switched_membership())
    portal_actor.is_superuser = True
    try:
        with _as_tenant_actor(control_client, portal_actor, tenant_id=switched_tenant_id):
            switched = control_client.get("/control/portal/my-applications")
    finally:
        portal_actor.is_superuser = False
    assert switched.status_code == 200, switched.text
    assert switched.json()["data"] == []

    switched_site_id = asyncio.run(_create_site())
    portal_actor.is_superuser = True
    try:
        with _as_tenant_actor(control_client, portal_actor, site_id=switched_site_id):
            switched_site = control_client.get("/control/portal/my-applications")
    finally:
        portal_actor.is_superuser = False
    assert switched_site.status_code == 200, switched_site.text
    assert switched_site.json()["data"] == []

    async def assert_each_inactive_state_hides_application() -> None:
        from app.core.database import async_db_session

        cases = [
            (ControlApplicationModel, application["id"], "status", 1),
            (ControlApplicationModel, application["id"], "is_deleted", True),
            (ControlTenantApplicationModel, opening_id, "status", 1),
            (ControlTenantApplicationModel, opening_id, "is_deleted", True),
            (ControlUserApplicationGrantModel, None, "is_deleted", True),
            (UserModel, portal_actor.id, "status", 1),
            (UserModel, portal_actor.id, "is_deleted", True),
        ]
        async with async_db_session() as db:
            grant_id = (
                await db.execute(
                    select(ControlUserApplicationGrantModel.id).where(
                        ControlUserApplicationGrantModel.tenant_application_id == opening_id,
                        ControlUserApplicationGrantModel.user_id == portal_actor.id,
                    )
                )
            ).scalar_one()
        for model, object_id, field, inactive_value in cases:
            target_id = grant_id if model is ControlUserApplicationGrantModel else object_id
            async with async_db_session() as db:
                obj = await db.get(model, target_id)
                original = getattr(obj, field)
                setattr(obj, field, inactive_value)
                await db.commit()
            with _as_tenant_actor(control_client, portal_actor):
                hidden = control_client.get("/control/portal/my-applications")
            assert hidden.status_code == 200, hidden.text
            assert hidden.json()["data"] == [], (model.__name__, field, hidden.text)
            async with async_db_session() as db:
                obj = await db.get(model, target_id)
                setattr(obj, field, original)
                await db.commit()

    asyncio.run(assert_each_inactive_state_hides_application())


def test_platform_superuser_cannot_launch_opened_application_without_personal_grant(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application_code = f"delegate-{uuid4().hex[:10]}"
    created = control_client.post("/control/applications", json=_application_payload(application_code), headers=auth_headers)
    assert created.status_code == 200, created.text
    application_id = created.json()["data"]["id"]
    tenant_id = asyncio.run(_create_tenant())
    _open_application(control_client, auth_headers, tenant_id=tenant_id, application_id=application_id)
    other_tenant_application_code = f"other-tenant-{uuid4().hex[:10]}"
    other_created = control_client.post(
        "/control/applications",
        json=_application_payload(other_tenant_application_code),
        headers=auth_headers,
    )
    assert other_created.status_code == 200, other_created.text
    other_tenant_id = asyncio.run(_create_tenant())
    _open_application(
        control_client,
        auth_headers,
        tenant_id=other_tenant_id,
        application_id=other_created.json()["data"]["id"],
    )
    platform_user_id = asyncio.run(_create_user(tenant_id=1, is_superuser=True))
    platform_actor = SimpleNamespace(id=platform_user_id, tenant_id=1, is_superuser=True, roles=[])

    with _as_tenant_actor(control_client, platform_actor, tenant_id=tenant_id):
        applications = control_client.get("/control/portal/my-applications")

    assert applications.status_code == 200, applications.text
    assert applications.json()["data"] == []
    with _as_tenant_actor(control_client, platform_actor, tenant_id=tenant_id):
        launched = control_client.post(f"/control/portal/applications/{application_code}/launch")
    assert launched.status_code == 403, launched.text


def test_tenant_scoped_superuser_still_requires_personal_application_grant(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application_id = _create_application(control_client, auth_headers)
    tenant_id = asyncio.run(_create_tenant())
    _open_application(control_client, auth_headers, tenant_id=tenant_id, application_id=application_id)
    tenant_superuser_id = asyncio.run(
        _create_user(tenant_id=tenant_id, member_tenant_id=tenant_id, is_superuser=True)
    )
    tenant_superuser = SimpleNamespace(
        id=tenant_superuser_id,
        tenant_id=tenant_id,
        is_superuser=True,
        roles=[],
    )

    with _as_tenant_actor(control_client, tenant_superuser, tenant_id=tenant_id):
        response = control_client.get("/control/portal/my-applications")
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


def test_launch_issues_60_second_hash_only_ticket_and_preserves_callback_query(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    callback = "http://target.example.test:8100/callback?from=control#section"
    application, _opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers, callback_url=callback)
    launched = _launch(control_client, portal_actor, str(application["code"]))
    assert launched.status_code == 200, launched.text
    redirect_url = launched.json()["data"]["redirect_url"]
    parsed = urlsplit(redirect_url)
    query = parse_qs(parsed.query)
    assert query["from"] == ["control"]
    assert parsed.fragment == "section"
    assert len(query["code"]) == 1
    plain_code = query["code"][0]

    async def verify_ticket() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket = (
                await db.execute(select(ControlSSOLaunchTicketModel).where(ControlSSOLaunchTicketModel.application_id == application["id"]))
            ).scalar_one()
            assert ticket.code_hash == hashlib.sha256(plain_code.encode()).hexdigest()
            assert plain_code not in ticket.code_hash
            assert 59 <= (ticket.expires_at - ticket.issued_at).total_seconds() <= 60

    asyncio.run(verify_ticket())


def test_launch_places_code_in_vue_hash_callback_query(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    callback = "http://target.example.test:8100/#/auth/control/callback"
    application, _opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers, callback_url=callback)
    launched = _launch(control_client, portal_actor, str(application["code"]))
    assert launched.status_code == 200, launched.text

    redirect_url = launched.json()["data"]["redirect_url"]
    redirect = urlsplit(redirect_url)
    fragment = urlsplit(redirect.fragment)
    fragment_query = parse_qs(fragment.query, keep_blank_values=True)
    assert redirect.query == ""
    assert fragment.path == "/auth/control/callback"
    assert set(fragment_query) == {"code"}
    assert redirect_url == f"{callback}?code={fragment_query['code'][0]}"


def test_launch_preserves_existing_vue_hash_query_and_rejects_hash_code(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    callback = "http://target.example.test/#/auth/control/callback?from=portal&blank="
    application, _opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers, callback_url=callback)
    launched = _launch(control_client, portal_actor, str(application["code"]))
    assert launched.status_code == 200, launched.text

    redirect_url = launched.json()["data"]["redirect_url"]
    redirect = urlsplit(redirect_url)
    fragment_query = parse_qs(urlsplit(redirect.fragment).query, keep_blank_values=True)
    assert redirect.query == ""
    assert fragment_query["from"] == ["portal"]
    assert fragment_query["blank"] == [""]
    assert len(fragment_query["code"]) == 1
    assert redirect_url == f"{callback}&code={fragment_query['code'][0]}"

    poisoned_application, _poisoned_opening_id, poisoned_actor = _prepare_portal_access(
        control_client,
        auth_headers,
        callback_url="http://target.example.test/#/auth/control/callback?code=attacker&from=portal",
    )
    rejected = _launch(control_client, poisoned_actor, str(poisoned_application["code"]))
    assert rejected.status_code == 409, rejected.text


def test_launch_rejects_callback_with_existing_code(control_client: TestClient, auth_headers: dict[str, str]) -> None:
    application, _opening_id, portal_actor = _prepare_portal_access(
        control_client,
        auth_headers,
        callback_url="http://target.example.test/callback?code=attacker&from=x",
    )
    response = _launch(control_client, portal_actor, str(application["code"]))
    assert response.status_code == 409, response.text


def test_exchange_validates_client_ticket_owner_and_returns_complete_claims(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application, _opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)
    asyncio.run(_set_membership_role(portal_actor.id, 1, "owner"))
    other_application, _other_opening_id, _other_actor = _prepare_portal_access(control_client, auth_headers)
    launch = _launch(control_client, portal_actor, str(application["code"]))
    plain_code = _launch_code(launch)

    wrong_secret = _exchange(control_client, application, plain_code, secret="wrong-secret")
    wrong_application = _exchange(control_client, other_application, plain_code)
    assert wrong_secret.status_code == 401, wrong_secret.text
    assert wrong_application.status_code in {400, 401}, wrong_application.text

    exchanged = _exchange(control_client, application, plain_code)
    assert exchanged.status_code == 200, exchanged.text
    claims = exchanged.json()["data"]
    assert claims == {
        "issuer": "http://testserver",
        "central_user_uuid": claims["central_user_uuid"],
        "name": claims["name"],
        "mobile": claims["mobile"],
        "email": claims["email"],
        "avatar": claims["avatar"],
        "status": 0,
        "site_code": "default",
        "central_tenant_code": "system",
        "central_tenant_role": "owner",
        "central_is_superuser": False,
        "target_tenant_code": "targettenant",
    }
    assert claims["central_user_uuid"]
    assert claims["name"].startswith("授权用户")


def test_exchange_rejects_expired_ticket_and_revoked_current_state(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application, opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)
    launch = _launch(control_client, portal_actor, str(application["code"]))
    plain_code = _launch_code(launch)

    async def expire_ticket() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket = (
                await db.execute(select(ControlSSOLaunchTicketModel).where(ControlSSOLaunchTicketModel.code_hash == hashlib.sha256(plain_code.encode()).hexdigest()))
            ).scalar_one()
            ticket.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

    asyncio.run(expire_ticket())
    assert _exchange(control_client, application, plain_code).status_code == 400

    launch = _launch(control_client, portal_actor, str(application["code"]))
    fresh_code = _launch_code(launch)

    async def revoke_after_launch() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel).where(
                        ControlUserApplicationGrantModel.tenant_application_id == opening_id,
                        ControlUserApplicationGrantModel.user_id == portal_actor.id,
                    )
                )
            ).scalar_one()
            grant.desired_state = "inactive"
            grant.sync_status = "pending"
            await db.commit()

    asyncio.run(revoke_after_launch())
    assert _exchange(control_client, application, fresh_code).status_code == 403


def test_concurrent_exchange_atomically_allows_exactly_one_redemption(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application, _opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)
    launch = _launch(control_client, portal_actor, str(application["code"]))
    plain_code = _launch_code(launch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: _exchange(control_client, application, plain_code), range(2)))
    statuses = sorted(response.status_code for response in responses)
    assert statuses[0] == 200, [(response.status_code, response.text) for response in responses]
    assert statuses[1] == 400, [(response.status_code, response.text) for response in responses]


@pytest.mark.parametrize("target", ["application", "opening"])
def test_disabling_target_persists_revocation_before_publication(control_client, auth_headers, monkeypatch, target):
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel

    async def no_publish(_ids):
        return None
    monkeypatch.setattr("app.api.v1.module_control.user_entitlement.lifecycle.publish_entitlement_tasks", no_publish)
    application, opening_id, _actor = _prepare_portal_access(control_client, auth_headers)
    url = f"/control/applications/{application['id']}" if target == "application" else f"/control/tenant-applications/{opening_id}"
    response = control_client.put(url, json={"status": 1}, headers=auth_headers)
    assert response.status_code == 200, response.text

    async def verify():
        async with async_db_session() as db:
            grant = await db.scalar(select(ControlUserApplicationGrantModel).where(ControlUserApplicationGrantModel.tenant_application_id == opening_id))
            assert grant.desired_state == "inactive"
            assert grant.sync_status == "pending"
            task = await db.scalar(select(BusinessTaskModel).where(BusinessTaskModel.biz_id == str(grant.id), BusinessTaskModel.idempotency_key.like("control-lifecycle:%")))
            assert task is not None and task.payload["mode"] == "lifecycle"
    asyncio.run(verify())


@pytest.mark.parametrize("target", ["application", "opening"])
def test_live_grants_cannot_be_remapped_to_another_target(control_client, auth_headers, target):
    application, opening_id, _actor = _prepare_portal_access(control_client, auth_headers)
    if target == "application":
        url, payload = f"/control/applications/{application['id']}", {"code": "replacement-product"}
    else:
        url, payload = f"/control/tenant-applications/{opening_id}", {"target_tenant_code": "replacementtenant"}
    response = control_client.put(url, json=payload, headers=auth_headers)
    assert response.status_code == 409, response.text


def test_application_entitlement_configuration_round_trips_and_validates(control_client, auth_headers):
    payload = _application_payload(f"sync-{uuid4().hex[:8]}")
    payload.update(entitlement_sync_enabled=True, entitlement_sync_url="https://target.example.test/api/v1/system/auth/control/access/sync", entitlement_sync_timeout_seconds=7)
    response = control_client.post("/control/applications", json=payload, headers=auth_headers)
    assert response.status_code == 200, response.text
    app = response.json()["data"]
    assert app["entitlement_sync_enabled"] is True
    assert app["entitlement_sync_timeout_seconds"] == 7
    assert app["entitlement_sync_url"] == payload["entitlement_sync_url"]
    url = f"/control/applications/{app['id']}"
    assert control_client.put(url, json={"entitlement_sync_url": None}, headers=auth_headers).status_code == 400
    assert control_client.put(url, json={"entitlement_sync_url": "https://target.example.test/sync?secret=x"}, headers=auth_headers).status_code == 422
    assert control_client.put(url, json={"entitlement_sync_timeout_seconds": 0}, headers=auth_headers).status_code == 422
    changed = control_client.put(url, json={"entitlement_sync_timeout_seconds": 8}, headers=auth_headers)
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["entitlement_sync_timeout_seconds"] == 8
