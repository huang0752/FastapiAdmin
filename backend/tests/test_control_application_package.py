"""Control application-package management contracts."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_control.application_package.model import ControlApplicationPackageModel
from app.api.v1.module_control.model import ControlApplicationModel
from app.api.v1.module_control.tenant_provision.model import ControlTenantProvisionModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter, get_current_user
from app.utils.hash_bcrpy_util import PwdUtil


@pytest.fixture
def control_client(test_client: TestClient) -> TestClient:
    from app.api.v1.module_control import control_router

    original_routes = list(test_client.app.router.routes)
    test_client.app.include_router(control_router)
    try:
        yield test_client
    finally:
        test_client.app.router.routes[:] = original_routes


def _application_payload(code: str, *, enabled: bool = True) -> dict[str, object]:
    return {
        "code": code,
        "name": f"应用 {code}",
        "base_url": "http://target.example.test",
        "callback_url": "http://target.example.test/web#/auth/control-sso-callback",
        "provisioning_url": "http://target.example.test/system/auth/control/tenant/provision" if enabled else None,
        "provisioning_enabled": enabled,
        "status": 0,
    }


def _create_application(client: TestClient, headers: dict[str, str], *, enabled: bool = True) -> dict[str, object]:
    response = client.post(
        "/control/applications",
        headers=headers,
        json=_application_payload(f"pkg{uuid4().hex[:10]}", enabled=enabled),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _package_payload(application_id: int, code: str, *, target_code: str | None = None, is_default: bool = False) -> dict[str, object]:
    return {
        "application_id": application_id,
        "code": code,
        "name": f"套餐 {code}",
        "target_package_code": target_code or code,
        "is_default": is_default,
        "status": 0,
        "sort": 10,
    }


def _permission_for_route(method: str, path: str) -> list[str]:
    from app.api.v1.module_control import control_router

    route = next(item for item in control_router.routes if isinstance(item, APIRoute) and item.path == path and method in item.methods)
    permissions = [dependency.call.permissions for dependency in route.dependant.dependencies if isinstance(dependency.call, AuthPermission)]
    assert len(permissions) == 1
    return permissions[0]


def test_application_package_routes_declare_platform_permissions() -> None:
    assert _permission_for_route("GET", "/control/application-packages") == ["module_control:application_package:query"]
    assert _permission_for_route("POST", "/control/application-packages") == ["module_control:application_package:create"]
    assert _permission_for_route("PUT", "/control/application-packages/{package_id}") == ["module_control:application_package:update"]
    assert _permission_for_route("DELETE", "/control/application-packages/{package_id}") == ["module_control:application_package:delete"]


def test_application_package_crud_enforces_enabled_application_and_single_default(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = _create_application(control_client, auth_headers)
    disabled_application = _create_application(control_client, auth_headers, enabled=False)

    rejected = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(disabled_application["id"]), "basic"),
    )
    assert rejected.status_code == 409, rejected.text

    first = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "basic", is_default=True),
    )
    second = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "pro", is_default=True),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    listing = control_client.get(
        f"/control/application-packages?application_id={application['id']}",
        headers=auth_headers,
    )
    assert listing.status_code == 200, listing.text
    items = listing.json()["data"]["items"]
    assert {item["code"] for item in items} == {"basic", "pro"}
    assert [item["code"] for item in items if item["is_default"]] == ["pro"]

    updated = control_client.put(
        f"/control/application-packages/{first.json()['data']['id']}",
        headers=auth_headers,
        json={"is_default": True, "name": "基础版"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["is_default"] is True

    listing = control_client.get(
        f"/control/application-packages?application_id={application['id']}",
        headers=auth_headers,
    )
    assert [item["code"] for item in listing.json()["data"]["items"] if item["is_default"]] == ["basic"]


def test_application_package_rejects_duplicate_codes_and_cross_site_access(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = _create_application(control_client, auth_headers)
    created = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "basic", target_code="targetbasic"),
    )
    assert created.status_code == 200, created.text

    duplicate_code = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "basic", target_code="another"),
    )
    duplicate_target = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "another", target_code="targetbasic"),
    )
    assert duplicate_code.status_code == 409, duplicate_code.text
    assert duplicate_target.status_code == 409, duplicate_target.text

    async def create_cross_site_package() -> int:
        from app.api.v1.module_platform.site.model import SiteModel
        from app.core.database import async_db_session

        suffix = uuid4().hex[:10]
        async with async_db_session() as db:
            site = SiteModel(code=f"pkg_{suffix}", name=f"套餐隔离站点{suffix}", status=0)
            db.add(site)
            await db.flush()
            cross_application = ControlApplicationModel(
                site_id=site.id,
                code=f"cross{suffix}",
                name="跨站应用",
                base_url="http://cross.example.test",
                callback_url="http://cross.example.test/web#/auth/control-sso-callback",
                client_id=f"cross-{suffix}",
                client_secret_hash=PwdUtil.hash_password("cross-secret"),
                provisioning_url="http://cross.example.test/system/auth/control/tenant/provision",
                provisioning_enabled=True,
                status=0,
            )
            db.add(cross_application)
            await db.flush()
            package = ControlApplicationPackageModel(
                site_id=site.id,
                application_id=cross_application.id,
                code="basic",
                name="基础版",
                target_package_code="basic",
                status=0,
            )
            db.add(package)
            await db.commit()
            return package.id

    cross_package_id = asyncio.run(create_cross_site_package())
    update = control_client.put(
        f"/control/application-packages/{cross_package_id}",
        headers=auth_headers,
        json={"name": "越权"},
    )
    delete = control_client.delete(f"/control/application-packages/{cross_package_id}", headers=auth_headers)
    assert update.status_code == 403, update.text
    assert delete.status_code == 403, delete.text


def test_concurrent_default_package_creation_allows_one_safe_winner(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_control.application_package.service import ControlApplicationPackageService

    application = _create_application(control_client, auth_headers)
    barrier = threading.Barrier(2)

    async def synchronize_empty_default(
        self: ControlApplicationPackageService,
        application_id: int,
        package_id: int | None = None,
    ) -> None:
        _ = self, application_id, package_id
        await asyncio.to_thread(barrier.wait, 5)

    monkeypatch.setattr(ControlApplicationPackageService, "_make_default", synchronize_empty_default)

    def create(code: str):
        return control_client.post(
            "/control/application-packages",
            headers=auth_headers,
            json=_package_payload(int(application["id"]), code, is_default=True),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(create, ("basic", "pro")))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["data"] is None
    assert "sql" not in conflict.text.lower()
    listing = control_client.get(
        f"/control/application-packages?application_id={application['id']}",
        headers=auth_headers,
    )
    assert sum(item["is_default"] for item in listing.json()["data"]["items"]) == 1


def test_deleted_package_codes_remain_stable_safe_conflicts(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = _create_application(control_client, auth_headers)
    created = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "archived", target_code="targetarchived"),
    )
    assert created.status_code == 200, created.text
    deleted = control_client.delete(
        f"/control/application-packages/{created.json()['data']['id']}",
        headers=auth_headers,
    )
    assert deleted.status_code == 200, deleted.text

    recreated = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "archived", target_code="targetarchived"),
    )

    assert recreated.status_code == 409, recreated.text
    assert recreated.json()["data"] is None
    assert "constraint" not in recreated.text.lower()
    assert "sql" not in recreated.text.lower()


def test_concurrent_duplicate_package_code_returns_safe_conflict(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_control.application_package.service import ControlApplicationPackageService

    application = _create_application(control_client, auth_headers)
    barrier = threading.Barrier(2)
    original_ensure_unique = ControlApplicationPackageService._ensure_unique

    async def synchronize_unique_check(self: ControlApplicationPackageService, *args, **kwargs) -> None:
        await original_ensure_unique(self, *args, **kwargs)
        await asyncio.to_thread(barrier.wait, 5)

    monkeypatch.setattr(ControlApplicationPackageService, "_ensure_unique", synchronize_unique_check)

    def create(target_code: str):
        return control_client.post(
            "/control/application-packages",
            headers=auth_headers,
            json=_package_payload(int(application["id"]), "duplicate", target_code=target_code),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(create, ("targetone", "targettwo")))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["data"] is None
    assert "constraint" not in conflict.text.lower()
    assert "sql" not in conflict.text.lower()


def test_referenced_package_delete_only_deactivates_and_cannot_be_selected(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = _create_application(control_client, auth_headers)
    package_response = control_client.post(
        "/control/application-packages",
        headers=auth_headers,
        json=_package_payload(int(application["id"]), "enterprise", is_default=True),
    )
    assert package_response.status_code == 200, package_response.text
    package_id = package_response.json()["data"]["id"]

    async def create_successful_reference() -> None:
        from app.core.database import async_db_session

        suffix = uuid4().hex[:10]
        async with async_db_session() as db:
            tenant = TenantModel(name=f"引用租户{suffix}", code=f"ref{suffix}", site_id=1, status=0)
            owner = UserModel(
                tenant_id=1,
                username=f"owner{suffix}",
                password=PwdUtil.hash_password("test-password"),
                name="引用管理员",
                status=0,
            )
            db.add_all([tenant, owner])
            await db.flush()
            db.add(
                ControlTenantProvisionModel(
                    site_id=1,
                    tenant_id=tenant.id,
                    application_id=int(application["id"]),
                    application_package_id=package_id,
                    owner_user_id=owner.id,
                    provision_request_uuid=str(uuid4()),
                    desired_target_tenant_code=tenant.code,
                    status="succeeded",
                )
            )
            await db.commit()

    asyncio.run(create_successful_reference())
    deleted = control_client.delete(f"/control/application-packages/{package_id}", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text

    async def assert_deactivated() -> None:
        from app.api.v1.module_control.application_package.service import ControlApplicationPackageService
        from app.core.database import async_db_session
        from app.core.exceptions import CustomException

        async with async_db_session() as db:
            package = await db.get(ControlApplicationPackageModel, package_id)
            assert package is not None
            assert package.status == 1
            assert package.is_deleted is False
            auth = AuthSchema(db=db, user=type("Actor", (), {"id": 1, "is_superuser": True})(), tenant_id=1, site_id=1)
            with pytest.raises(CustomException, match="套餐.*停用"):
                await ControlApplicationPackageService(auth).require_selectable(package_id, application_id=int(application["id"]))

    asyncio.run(assert_deactivated())


def test_tenant_admin_cannot_manage_application_packages(control_client: TestClient) -> None:
    async def tenant_auth(db: Annotated[AsyncSession, Depends(db_getter)]) -> AuthSchema:
        return AuthSchema(
            db=db,
            user=type("Actor", (), {"id": 999999, "is_superuser": False, "roles": []})(),
            tenant_id=2,
            site_id=1,
        )

    original = control_client.app.dependency_overrides.get(get_current_user)
    control_client.app.dependency_overrides[get_current_user] = tenant_auth
    try:
        response = control_client.get("/control/application-packages")
    finally:
        if original is None:
            control_client.app.dependency_overrides.pop(get_current_user, None)
        else:
            control_client.app.dependency_overrides[get_current_user] = original
    assert response.status_code == 403, response.text
