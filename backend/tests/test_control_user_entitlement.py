"""Control API closure for product user entitlements."""

from __future__ import annotations

import asyncio
import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_control_provider import (
    _application_payload,
    _as_tenant_actor,
    _create_tenant,
    _create_user,
    _exchange,
    _launch,
    _launch_code,
    _open_application,
    _prepare_portal_access,
    _tenant_actor,
)

from app.api.v1.module_control.model import ControlSSOLaunchTicketModel, ControlUserApplicationGrantModel
from app.api.v1.module_control.user_entitlement.schema import ControlUserCreateIn
from app.api.v1.module_platform.tenant.model import TenantUserModel
from app.api.v1.module_system.role.model import RoleModel
from app.api.v1.module_system.user.model import UserModel
from app.core.database import async_db_session
from app.plugin.module_task.business.task.model import BusinessTaskModel


@pytest.fixture
def control_client(test_client: TestClient) -> TestClient:
    from app.api.v1.module_control import control_router

    app = test_client.app
    original_routes = list(app.router.routes)
    app.include_router(control_router)
    try:
        yield test_client
    finally:
        app.router.routes[:] = original_routes


@pytest.fixture(autouse=True)
def entitlement_runtime(monkeypatch):
    async def no_publish(_task_ids: list[int]) -> None:
        return None

    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    monkeypatch.setattr("app.api.v1.module_control.controller.publish_entitlement_tasks", no_publish)


pytestmark = pytest.mark.usefixtures("control_provider_context")


def test_grant_returns_pending_sync_contract(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    code = "entitlement-pending"
    application = control_client.post(
        "/control/applications",
        json=_application_payload(code),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])
    user_id = asyncio.run(_create_user(member_tenant_id=1))
    actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    published: list[int] = []

    async def record_publish(task_ids: list[int]) -> None:
        published.extend(task_ids)

    monkeypatch.setattr("app.api.v1.module_control.controller.publish_entitlement_tasks", record_publish)
    with _as_tenant_actor(control_client, actor):
        response = control_client.put(f"/control/tenant-applications/{opening_id}/grants/{user_id}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["grant_id"] == data["id"]
    assert data["desired_state"] == "active"
    assert data["sync_status"] == "pending"
    assert data["sync_version"] >= 1
    assert data["error"] is None
    assert data["launchable"] is False
    assert len(published) == 1

    query_actor = asyncio.run(_tenant_actor("module_control:user_grant:query"))
    with _as_tenant_actor(control_client, query_actor):
        members = control_client.get(f"/control/tenant-applications/{opening_id}/grants")
    member = next(item for item in members.json()["data"] if item["user_id"] == user_id)
    assert member["grant_id"] == data["grant_id"]
    assert member["desired_state"] == "active"
    assert member["sync_status"] == "pending"
    assert member["sync_version"] == data["sync_version"]
    assert member["error"] is None
    assert member["launchable"] is False


def _control_user_payload(username: str, opening_ids: list[int]) -> dict[str, object]:
    return {
        "user": {
            "username": username,
            "password": "Control123",
            "name": "甲方演示用户",
            "status": 0,
        },
        "tenant_application_ids": opening_ids,
    }


def test_normalized_drawer_payload_matches_control_user_create_contract() -> None:
    payload = _control_user_payload("real_payload", [11])
    parsed = ControlUserCreateIn.model_validate(payload)

    assert parsed.user.email is None
    assert parsed.user.mobile is None


def test_create_control_user_requires_create_and_grant_permissions(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = control_client.post(
        "/control/applications",
        json=_application_payload(f"create-perm-{uuid4().hex[:8]}"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])

    for permissions in [
        ["module_system:user:create"],
        ["module_control:user_grant:update"],
    ]:
        actor = asyncio.run(_tenant_actor(permissions))
        with _as_tenant_actor(control_client, actor):
            response = control_client.post(
                "/control/users",
                json=_control_user_payload(f"single_{uuid4().hex[:8]}", [opening_id]),
            )
        assert response.status_code == 403, response.text


def test_create_control_user_with_three_entitlements_is_atomic_outbox_prepare(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opening_ids: list[int] = []
    for index in range(3):
        application = control_client.post(
            "/control/applications",
            json=_application_payload(f"create-three-{index}-{uuid4().hex[:8]}"),
            headers=auth_headers,
        ).json()["data"]
        opening_ids.append(
            _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])
        )

    published: list[int] = []

    async def record_publish(task_ids: list[int]) -> None:
        published.extend(task_ids)

    monkeypatch.setattr(
        "app.api.v1.module_control.user_entitlement.controller.publish_entitlement_tasks",
        record_publish,
        raising=False,
    )
    actor = asyncio.run(
        _tenant_actor(["module_system:user:create", "module_control:user_grant:update"])
    )
    actor.roles[0].data_scope = 4

    username = f"control_{uuid4().hex[:8]}"
    with _as_tenant_actor(control_client, actor):
        response = control_client.post(
            "/control/users",
            json=_control_user_payload(username, list(reversed(opening_ids))),
        )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["user"]["username"] == username
    assert [role["code"] for role in data["user"]["roles"]] == ["CONTROL_PORTAL_USER"]
    assert len(data["entitlements"]) == 3
    assert [item["tenant_application_id"] for item in data["entitlements"]] == sorted(opening_ids)
    assert {item["desired_state"] for item in data["entitlements"]} == {"active"}
    assert {item["sync_status"] for item in data["entitlements"]} == {"pending"}
    assert all(item["launchable"] is False for item in data["entitlements"])
    assert len(published) == 3

    async def assert_user_role_permissions() -> None:
        async with async_db_session() as db:
            role = (
                await db.execute(
                    select(RoleModel).where(
                        RoleModel.tenant_id == 1,
                        RoleModel.code == "CONTROL_PORTAL_USER",
                    )
                )
            ).scalar_one()
            assert role.status == 0
            assert {menu.route_name for menu in role.menus if menu.route_name} == {
                "Control",
                "ControlPortal",
            }
            assert [menu.permission for menu in role.menus].count(
                "module_control:portal:query"
            ) == 2
            assert [menu.permission for menu in role.menus].count(
                "module_control:portal:launch"
            ) == 1
            assert len(role.menus) == 4

    asyncio.run(assert_user_role_permissions())


def test_create_control_user_rejects_empty_duplicate_products_and_role_configuration(
    control_client: TestClient,
) -> None:
    actor = asyncio.run(
        _tenant_actor(["module_system:user:create", "module_control:user_grant:update"])
    )
    invalid_payloads = [
        _control_user_payload(f"empty_{uuid4().hex[:8]}", []),
        _control_user_payload(f"duplicate_{uuid4().hex[:8]}", [11, 11]),
        {
            **_control_user_payload(f"role_{uuid4().hex[:8]}", [11]),
            "user": {
                **_control_user_payload("unused", [11])["user"],
                "username": f"role_{uuid4().hex[:8]}",
                "role_ids": [1],
            },
        },
        {
            **_control_user_payload(f"tenant_{uuid4().hex[:8]}", [11]),
            "user": {
                **_control_user_payload("unused", [11])["user"],
                "username": f"tenant_{uuid4().hex[:8]}",
                "tenant_id": 2,
            },
        },
    ]
    with _as_tenant_actor(control_client, actor):
        responses = [control_client.post("/control/users", json=payload) for payload in invalid_payloads]

    assert [response.status_code for response in responses] == [422, 422, 422, 422]


def test_create_control_user_uses_protected_exact_role_without_mutating_custom_user_role(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = control_client.post(
        "/control/applications",
        json=_application_payload(f"legacy-user-role-{uuid4().hex[:8]}"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(
        control_client, auth_headers, tenant_id=1, application_id=application["id"]
    )

    async def prepare_roles() -> None:
        async with async_db_session() as db:
            custom_role = (
                await db.execute(
                    select(RoleModel).where(RoleModel.tenant_id == 1, RoleModel.code == "USER")
                )
            ).scalar_one()
            custom_role.name = "租户自定义用户"
            custom_role.status = 1
            custom_role_menu_ids = [menu.id for menu in custom_role.menus]
            internal_role = await db.scalar(
                select(RoleModel).where(
                    RoleModel.tenant_id == 1,
                    RoleModel.code == "CONTROL_PORTAL_USER",
                )
            )
            if internal_role is None:
                internal_role = RoleModel(tenant_id=1, code="CONTROL_PORTAL_USER")
                db.add(internal_role)
            internal_role.name = "被篡改的角色"
            internal_role.status = 1
            internal_role.data_scope = 4
            internal_role.menus[:] = list(custom_role.menus)
            await db.commit()
            return custom_role_menu_ids

    custom_role_menu_ids = asyncio.run(prepare_roles())
    actor = asyncio.run(
        _tenant_actor(["module_system:user:create", "module_control:user_grant:update"])
    )
    actor.roles[0].data_scope = 4

    with _as_tenant_actor(control_client, actor):
        response = control_client.post(
            "/control/users",
            json=_control_user_payload(f"legacy_{uuid4().hex[:8]}", [opening_id]),
        )

    assert response.status_code == 200, response.text
    assert [role["code"] for role in response.json()["data"]["user"]["roles"]] == [
        "CONTROL_PORTAL_USER"
    ]

    async def assert_reconciled() -> None:
        async with async_db_session() as db:
            role = (
                await db.execute(
                    select(RoleModel).where(
                        RoleModel.tenant_id == 1,
                        RoleModel.code == "CONTROL_PORTAL_USER",
                    )
                )
            ).scalar_one()
            assert role.status == 0
            assert role.name == "普通用户"
            assert role.data_scope == 1
            assert {menu.route_name for menu in role.menus if menu.route_name} == {
                "Control",
                "ControlPortal",
            }
            assert len(role.menus) == 4
            custom_role = (
                await db.execute(
                    select(RoleModel).where(RoleModel.tenant_id == 1, RoleModel.code == "USER")
                )
            ).scalar_one()
            assert custom_role.name == "租户自定义用户"
            assert custom_role.status == 1
            assert [menu.id for menu in custom_role.menus] == custom_role_menu_ids

    asyncio.run(assert_reconciled())


def test_system_user_api_cannot_add_or_remove_control_portal_role(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = control_client.post(
        "/control/applications",
        json=_application_payload(f"protected-user-role-{uuid4().hex[:8]}"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(
        control_client, auth_headers, tenant_id=1, application_id=application["id"]
    )
    actor = asyncio.run(
        _tenant_actor(
            [
                "module_system:user:create",
                "module_system:user:update",
                "module_control:user_grant:update",
            ]
        )
    )
    actor.roles[0].data_scope = 4

    async def persist_actor_role() -> None:
        async with async_db_session() as db:
            role = RoleModel(
                tenant_id=1,
                name="用户管理员",
                code=actor.roles[0].code,
                status=0,
                data_scope=4,
            )
            db.add(role)
            await db.commit()
            actor.roles[0].id = role.id

    asyncio.run(persist_actor_role())
    with _as_tenant_actor(control_client, actor):
        created = control_client.post(
            "/control/users",
            json=_control_user_payload(f"protected_{uuid4().hex[:8]}", [opening_id]),
        )
    assert created.status_code == 200, created.text
    protected_user = created.json()["data"]["user"]
    protected_role = next(
        role for role in protected_user["roles"] if role["code"] == "CONTROL_PORTAL_USER"
    )

    with _as_tenant_actor(control_client, actor):
        injected_create = control_client.post(
            "/system/user/create",
            json={
                "username": f"inject_{uuid4().hex[:8]}",
                "password": "Control123",
                "name": "越权用户",
                "role_ids": [protected_role["id"]],
            },
        )
    assert injected_create.status_code == 400, injected_create.text
    # Tenant/data-scope filtering may reject an invisible protected role first;
    # both paths must deny injection without disclosing hidden role details.
    assert any(message in injected_create.text for message in ("保留角色", "系统管理角色", "部分角色不存在于当前租户"))

    update_payload = {
        "username": protected_user["username"],
        "name": protected_user["name"],
        "role_ids": [],
    }
    with _as_tenant_actor(control_client, actor):
        preserved = control_client.put(
            f"/system/user/update/{protected_user['id']}", json=update_payload
        )
    assert preserved.status_code == 200, preserved.text
    assert [role["code"] for role in preserved.json()["data"]["roles"]] == [
        "CONTROL_PORTAL_USER"
    ]

    normal_role = actor.roles[0]
    update_payload["role_ids"] = [normal_role.id]
    with _as_tenant_actor(control_client, actor):
        normal_added = control_client.put(
            f"/system/user/update/{protected_user['id']}", json=update_payload
        )
    assert normal_added.status_code == 200, normal_added.text
    assert {role["code"] for role in normal_added.json()["data"]["roles"]} == {
        "CONTROL_PORTAL_USER",
        normal_role.code,
    }

    async def create_cross_tenant_role() -> int:
        tenant_id = await _create_tenant(site_id=1)
        async with async_db_session() as db:
            role = RoleModel(
                tenant_id=tenant_id,
                name="普通用户",
                code="CONTROL_PORTAL_USER",
                status=0,
                data_scope=1,
            )
            db.add(role)
            await db.commit()
            return role.id

    cross_tenant_role_id = asyncio.run(create_cross_tenant_role())
    update_payload["role_ids"] = [cross_tenant_role_id]
    with _as_tenant_actor(control_client, actor):
        cross_tenant = control_client.put(
            f"/system/user/update/{protected_user['id']}", json=update_payload
        )
    assert cross_tenant.status_code == 400, cross_tenant.text

    async def assert_existing_bindings() -> None:
        async with async_db_session() as db:
            user = await db.get(UserModel, protected_user["id"])
            assert user is not None
            assert {role.code for role in user.roles} == {
                "CONTROL_PORTAL_USER",
                normal_role.code,
            }

    asyncio.run(assert_existing_bindings())


@pytest.mark.parametrize("missing_field", ["username", "password", "name"])
def test_create_control_user_requires_identity_fields(
    control_client: TestClient,
    missing_field: str,
) -> None:
    actor = asyncio.run(
        _tenant_actor(["module_system:user:create", "module_control:user_grant:update"])
    )
    payload = _control_user_payload(f"required_{uuid4().hex[:8]}", [11])
    payload["user"].pop(missing_field)  # type: ignore[union-attr]
    with _as_tenant_actor(control_client, actor):
        response = control_client.post("/control/users", json=payload)

    assert response.status_code == 422, response.text


def test_create_control_user_rolls_back_user_and_grants_when_one_product_is_invalid(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = control_client.post(
        "/control/applications",
        json=_application_payload(f"create-rollback-{uuid4().hex[:8]}"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(
        control_client, auth_headers, tenant_id=1, application_id=application["id"]
    )
    published: list[int] = []

    async def record_publish(task_ids: list[int]) -> None:
        published.extend(task_ids)

    monkeypatch.setattr(
        "app.api.v1.module_control.user_entitlement.controller.publish_entitlement_tasks",
        record_publish,
    )
    actor = asyncio.run(
        _tenant_actor(["module_system:user:create", "module_control:user_grant:update"])
    )
    actor.roles[0].data_scope = 4
    username = f"rollback_{uuid4().hex[:8]}"

    async def grant_ids() -> set[int]:
        async with async_db_session() as db:
            return set(
                (
                    await db.execute(
                        select(ControlUserApplicationGrantModel.id).where(
                            ControlUserApplicationGrantModel.tenant_application_id == opening_id
                        )
                    )
                ).scalars()
            )

    grants_before = asyncio.run(grant_ids())
    with _as_tenant_actor(control_client, actor):
        response = control_client.post(
            "/control/users",
            json=_control_user_payload(username, [opening_id, 999_999_999]),
        )

    assert response.status_code == 404, response.text
    assert published == []

    async def assert_no_partial_rows() -> None:
        async with async_db_session() as db:
            assert (
                await db.execute(select(UserModel.id).where(UserModel.username == username))
            ).scalar_one_or_none() is None

    asyncio.run(assert_no_partial_rows())
    assert asyncio.run(grant_ids()) == grants_before


def test_revoke_returns_inactive_pending_contract(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application = control_client.post(
        "/control/applications",
        json=_application_payload("revoke-pending"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])
    user_id = asyncio.run(_create_user(member_tenant_id=1))
    grant_actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, grant_actor):
        granted = control_client.put(f"/control/tenant-applications/{opening_id}/grants/{user_id}")
    revoke_actor = asyncio.run(_tenant_actor("module_control:user_grant:delete"))
    with _as_tenant_actor(control_client, revoke_actor):
        revoked = control_client.delete(f"/control/tenant-applications/{opening_id}/grants/{user_id}")

    assert revoked.status_code == 200, revoked.text
    data = revoked.json()["data"]
    assert data["grant_id"] == granted.json()["data"]["grant_id"]
    assert data["desired_state"] == "inactive"
    assert data["sync_status"] == "pending"
    assert data["sync_version"] == granted.json()["data"]["sync_version"] + 1
    assert data["error"] is None
    assert data["launchable"] is False


def test_pending_and_failed_grants_remain_visible_but_not_launchable(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    application = control_client.post(
        "/control/applications",
        json=_application_payload("pending-card"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])
    portal_actor = asyncio.run(_tenant_actor(["module_control:portal:query", "module_control:portal:launch"]))
    grant_actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, grant_actor):
        granted = control_client.put(f"/control/tenant-applications/{opening_id}/grants/{portal_actor.id}")
    assert granted.status_code == 200, granted.text

    async def no_publish(_task_ids: list[int]) -> None:
        return None

    monkeypatch.setattr("app.api.v1.module_control.controller.publish_entitlement_tasks", no_publish)
    with _as_tenant_actor(control_client, portal_actor):
        pending = control_client.get("/control/portal/my-applications")
        rejected = control_client.post(f"/control/portal/applications/{application['code']}/launch")
    assert pending.status_code == 200, pending.text
    assert pending.json()["data"] == [
        {
            **pending.json()["data"][0],
            "desired_state": "active",
            "sync_status": "pending",
            "launchable": False,
        }
    ]
    assert rejected.status_code == 403, rejected.text

    async def fail_grant() -> None:
        async with async_db_session() as db:
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel).where(
                        ControlUserApplicationGrantModel.tenant_application_id == opening_id,
                        ControlUserApplicationGrantModel.user_id == portal_actor.id,
                    )
                )
            ).scalar_one()
            grant.sync_status = "failed"
            grant.status = 1
            grant.last_error_message = "目标产品暂时不可用"
            await db.commit()

    asyncio.run(fail_grant())
    with _as_tenant_actor(control_client, portal_actor):
        failed = control_client.get("/control/portal/my-applications")
    assert failed.status_code == 200, failed.text
    assert failed.json()["data"][0]["sync_status"] == "failed"
    assert failed.json()["data"][0]["error"] == "目标产品暂时不可用"
    assert failed.json()["data"][0]["launchable"] is False


def test_platform_delegate_requires_explicit_succeeded_grant(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    created = control_client.post(
        "/control/applications",
        json=_application_payload("delegate-needs-grant"),
        headers=auth_headers,
    ).json()["data"]
    tenant_id = asyncio.run(_create_tenant())
    _open_application(control_client, auth_headers, tenant_id=tenant_id, application_id=created["id"])
    platform_user_id = asyncio.run(_create_user(tenant_id=1, is_superuser=True))
    platform_actor = type("Actor", (), {"id": platform_user_id, "tenant_id": 1, "is_superuser": True, "roles": []})()

    with _as_tenant_actor(control_client, platform_actor, tenant_id=tenant_id):
        applications = control_client.get("/control/portal/my-applications")
        launched = control_client.post(f"/control/portal/applications/{created['code']}/launch")
    assert applications.status_code == 200, applications.text
    assert applications.json()["data"] == []
    assert launched.status_code == 403, launched.text


def test_exchange_rechecks_desired_state_and_sync_success(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application, opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)

    async def mark_succeeded() -> None:
        async with async_db_session() as db:
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel).where(
                        ControlUserApplicationGrantModel.tenant_application_id == opening_id,
                        ControlUserApplicationGrantModel.user_id == portal_actor.id,
                    )
                )
            ).scalar_one()
            grant.desired_state = "active"
            grant.sync_status = "succeeded"
            grant.status = 0
            grant.is_deleted = False
            await db.commit()

    asyncio.run(mark_succeeded())
    launch = _launch(control_client, portal_actor, str(application["code"]))
    assert launch.status_code == 200, launch.text
    plain_code = _launch_code(launch)

    async def revoke_after_issuance() -> None:
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
            # Keep legacy mirrors active to prove the new state is authoritative.
            grant.status = 0
            grant.is_deleted = False
            await db.commit()

    asyncio.run(revoke_after_issuance())
    assert _exchange(control_client, application, plain_code).status_code == 403


def test_disabled_member_is_never_reported_launchable(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    _application, opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)

    async def disable_user() -> None:
        async with async_db_session() as db:
            user = await db.get(UserModel, portal_actor.id)
            user.status = 1
            await db.commit()

    asyncio.run(disable_user())
    query_actor = asyncio.run(_tenant_actor("module_control:user_grant:query"))
    with _as_tenant_actor(control_client, query_actor):
        response = control_client.get(f"/control/tenant-applications/{opening_id}/grants")

    assert response.status_code == 200, response.text
    member = next(item for item in response.json()["data"] if item["user_id"] == portal_actor.id)
    assert member["desired_state"] == "active"
    assert member["sync_status"] == "succeeded"
    assert member["launchable"] is False


@pytest.mark.parametrize("identity_change", ["delete_membership", "invalid_role", "disable_user"])
def test_exchange_rejects_changed_identity_without_consuming_ticket(
    control_client: TestClient,
    auth_headers: dict[str, str],
    identity_change: str,
) -> None:
    application, _opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)
    launch = _launch(control_client, portal_actor, str(application["code"]))
    assert launch.status_code == 200, launch.text
    plain_code = _launch_code(launch)

    async def change_identity() -> None:
        async with async_db_session() as db:
            if identity_change == "disable_user":
                user = await db.get(UserModel, portal_actor.id)
                user.status = 1
                await db.commit()
                return
            membership = (
                await db.execute(
                    select(TenantUserModel).where(
                        TenantUserModel.user_id == portal_actor.id,
                        TenantUserModel.tenant_id == 1,
                    )
                )
            ).scalar_one()
            if identity_change == "delete_membership":
                await db.delete(membership)
            else:
                membership.role = "unsupported"
            await db.commit()

    asyncio.run(change_identity())
    rejected = _exchange(control_client, application, plain_code)
    assert rejected.status_code == 403, rejected.text

    async def ticket_status() -> str:
        async with async_db_session() as db:
            ticket = (
                await db.execute(
                    select(ControlSSOLaunchTicketModel).where(
                        ControlSSOLaunchTicketModel.code_hash == hashlib.sha256(plain_code.encode()).hexdigest()
                    )
                )
            ).scalar_one()
            return ticket.status

    assert asyncio.run(ticket_status()) == "issued"


def test_launch_rechecks_revoke_before_issuing_ticket(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    application, opening_id, portal_actor = _prepare_portal_access(control_client, auth_headers)

    async def revoke_before_launch() -> None:
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

    asyncio.run(revoke_before_launch())
    rejected = _launch(control_client, portal_actor, str(application["code"]))
    assert rejected.status_code == 403, rejected.text


def test_failed_grant_retry_returns_new_pending_generation(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    application = control_client.post(
        "/control/applications",
        json=_application_payload("retry-failed-grant"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])
    user_id = asyncio.run(_create_user(member_tenant_id=1))
    grant_actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, grant_actor):
        granted = control_client.put(f"/control/tenant-applications/{opening_id}/grants/{user_id}")
    assert granted.status_code == 200, granted.text
    old_version = granted.json()["data"]["sync_version"]

    async def mark_failed() -> None:
        async with async_db_session() as db:
            grant = await db.get(ControlUserApplicationGrantModel, granted.json()["data"]["grant_id"])
            grant.sync_status = "failed"
            grant.last_error_message = "已脱敏失败"
            await db.commit()

    asyncio.run(mark_failed())
    published: list[int] = []

    async def record_publish(task_ids: list[int]) -> None:
        published.extend(task_ids)

    monkeypatch.setattr("app.api.v1.module_control.controller.publish_entitlement_tasks", record_publish)
    retry_actor = asyncio.run(_tenant_actor("module_control:user_grant:retry"))
    with _as_tenant_actor(control_client, retry_actor):
        retried = control_client.post(f"/control/tenant-applications/{opening_id}/grants/{user_id}/retry")
        duplicate = control_client.post(f"/control/tenant-applications/{opening_id}/grants/{user_id}/retry")

    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["sync_status"] == "pending"
    assert retried.json()["data"]["sync_version"] == old_version + 1
    assert retried.json()["data"]["error"] is None
    assert len(published) == 1
    assert duplicate.status_code == 409, duplicate.text


def test_post_commit_publish_failure_leaves_grant_pending_for_scanner_recovery(
    control_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    from app.api.v1.module_control.user_entitlement.service import publish_entitlement_tasks

    application = control_client.post(
        "/control/applications",
        json=_application_payload("publish-failure"),
        headers=auth_headers,
    ).json()["data"]
    opening_id = _open_application(control_client, auth_headers, tenant_id=1, application_id=application["id"])
    user_id = asyncio.run(_create_user(member_tenant_id=1))
    actor = asyncio.run(_tenant_actor("module_control:user_grant:update"))
    with _as_tenant_actor(control_client, actor):
        response = control_client.put(f"/control/tenant-applications/{opening_id}/grants/{user_id}")
    assert response.status_code == 200, response.text
    grant_id = response.json()["data"]["grant_id"]

    class FailingDispatcher:
        async def publish_existing(self, _task_id: int) -> None:
            raise RuntimeError("broker unavailable")

    async def verify_recoverable_pending() -> None:
        async with async_db_session() as db:
            task_id = (
                await db.execute(
                    select(BusinessTaskModel.id).where(
                        BusinessTaskModel.biz_type == "user_entitlement",
                        BusinessTaskModel.biz_id == str(grant_id),
                    )
                )
            ).scalar_one()
        await publish_entitlement_tasks([task_id], dispatcher=FailingDispatcher())
        async with async_db_session() as db:
            grant = await db.get(ControlUserApplicationGrantModel, grant_id)
            task = await db.get(BusinessTaskModel, task_id)
            assert grant.sync_status == "pending"
            assert task.status == "pending"

    asyncio.run(verify_recoverable_pending())
