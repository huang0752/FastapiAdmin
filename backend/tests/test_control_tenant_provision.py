"""Control tenant creation orchestration contracts."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.v1.module_control.application_package.model import ControlApplicationPackageModel
from app.api.v1.module_control.model import ControlApplicationModel, ControlTenantApplicationModel
from app.api.v1.module_control.tenant_provision.model import ControlTenantProvisionModel
from app.api.v1.module_platform.package.model import PackageModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.user.model import UserModel
from app.core.dependencies import AuthPermission
from app.plugin.module_task.business.task.model import BusinessTaskModel
from app.utils.hash_bcrpy_util import PwdUtil

VALID_USCC = "91350100M000100Y43"


@pytest.fixture
def control_client(test_client: TestClient) -> TestClient:
    from app.api.v1.module_control import control_router

    original_routes = list(test_client.app.router.routes)
    test_client.app.include_router(control_router)
    try:
        yield test_client
    finally:
        test_client.app.router.routes[:] = original_routes


async def _seed_products() -> dict[str, int]:
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    async with async_db_session() as db:
        applications: list[ControlApplicationModel] = []
        packages: list[ControlApplicationPackageModel] = []
        for product in ("wms", "mes"):
            application = ControlApplicationModel(
                site_id=1,
                code=f"{product}{suffix}",
                name=product.upper(),
                base_url=f"http://{product}.example.test",
                callback_url=f"http://{product}.example.test/web#/auth/control-sso-callback",
                client_id=f"{product}-{suffix}",
                client_secret_hash=PwdUtil.hash_password(f"secret-{suffix}"),
                provisioning_url=f"http://{product}.example.test/system/auth/control/tenant/provision",
                provisioning_enabled=True,
                provisioning_timeout_seconds=10,
                status=0,
            )
            db.add(application)
            await db.flush()
            package = ControlApplicationPackageModel(
                site_id=1,
                application_id=application.id,
                code="pro" if product == "wms" else "basic",
                name="专业版" if product == "wms" else "基础版",
                target_package_code="pro" if product == "wms" else "basic",
                status=0,
            )
            db.add(package)
            await db.flush()
            applications.append(application)
            packages.append(package)
        await db.commit()
        return {
            "wms_id": applications[0].id,
            "mes_id": applications[1].id,
            "wms_package_id": packages[0].id,
            "mes_package_id": packages[1].id,
        }


async def _control_package_id() -> int:
    from app.core.database import async_db_session

    async with async_db_session() as db:
        package_id = (
            await db.execute(
                select(PackageModel.id)
                .where(PackageModel.site_id == 1, PackageModel.status == 0, PackageModel.is_deleted.is_(False))
                .order_by(PackageModel.id)
                .limit(1)
            )
        ).scalar_one()
        return package_id


def _payload(products: dict[str, int], *, code: str, package_id: int) -> dict[str, object]:
    return {
        "tenant": {
            "name": f"自动开户企业{code}",
            "code": code,
            "site_id": 1,
            "package_id": package_id,
            "unified_social_credit_code": VALID_USCC,
        },
        "applications": [
            {
                "application_id": products["wms_id"],
                "application_package_id": products["wms_package_id"],
            },
            {
                "application_id": products["mes_id"],
                "application_package_id": products["mes_package_id"],
            },
        ],
    }


def _route_permission(method: str, path: str) -> list[str]:
    from app.api.v1.module_control import control_router

    route = next(item for item in control_router.routes if isinstance(item, APIRoute) and item.path == path and method in item.methods)
    dependencies = [item.call.permissions for item in route.dependant.dependencies if isinstance(item.call, AuthPermission)]
    assert len(dependencies) == 1
    return dependencies[0]


def test_tenant_provision_routes_declare_platform_permissions() -> None:
    assert _route_permission("POST", "/control/tenants/provision") == ["module_control:tenant_provision:create"]
    assert _route_permission("GET", "/control/tenant-provisions") == ["module_control:tenant_provision:query"]
    assert _route_permission("PUT", "/control/tenant-provisions/{provision_id}") == ["module_control:tenant_provision:update"]
    assert _route_permission("POST", "/control/tenant-provisions/{provision_id}/retry") == ["module_control:tenant_provision:retry"]
    assert _route_permission("POST", "/control/tenant-provisions/{provision_id}/reconcile") == ["module_control:tenant_provision:reconcile"]


def test_create_tenant_with_provisions_commits_tenant_owner_provisions_and_outbox_atomically(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    products = asyncio.run(_seed_products())
    package_id = asyncio.run(_control_package_id())
    code = f"auto{uuid4().hex[:10]}"
    published_task_ids: list[int] = []
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    monkeypatch.setattr("app.api.v1.module_control.tenant_provision.service.settings.CELERY_ENABLED", True)
    monkeypatch.setattr(
        "app.api.v1.module_control.tenant_provision.controller._publish_provision_tasks",
        lambda task_ids: published_task_ids.extend(task_ids),
    )

    response = control_client.post(
        "/control/tenants/provision",
        headers=auth_headers,
        json=_payload(products, code=code, package_id=package_id),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tenant"]["initial_admin"]["username"] == f"{code}_admin"
    assert {item["status"] for item in data["provisions"]} == {"pending"}
    assert len(published_task_ids) == 2

    async def assert_database() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            tenant = (await db.execute(select(TenantModel).where(TenantModel.code == code))).scalar_one()
            owner = (await db.execute(select(UserModel).where(UserModel.username == f"{code}_admin"))).scalar_one()
            provisions = (
                await db.execute(select(ControlTenantProvisionModel).where(ControlTenantProvisionModel.tenant_id == tenant.id))
            ).scalars().all()
            tasks = (
                await db.execute(
                    select(BusinessTaskModel).where(
                        BusinessTaskModel.handler_code == "control.tenant_provision",
                        BusinessTaskModel.biz_id.in_([str(item.id) for item in provisions]),
                    )
                )
            ).scalars().all()
            assert len(provisions) == 2
            assert {item.owner_user_id for item in provisions} == {owner.id}
            assert len(tasks) == 2
            assert {item.status for item in tasks} == {"pending"}
            assert {item.max_retries for item in tasks} == {1}
            for task in tasks:
                task.status = "canceled"
            await db.commit()

    asyncio.run(assert_database())


def test_tenant_provision_list_includes_matching_active_tenant_application_id(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    products = asyncio.run(_seed_products())
    package_id = asyncio.run(_control_package_id())
    code = f"opening{uuid4().hex[:8]}"
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    monkeypatch.setattr("app.api.v1.module_control.tenant_provision.service.settings.CELERY_ENABLED", True)

    payload = _payload(products, code=code, package_id=package_id)
    tenant_payload = payload["tenant"]
    assert isinstance(tenant_payload, dict)
    tenant_payload.pop("unified_social_credit_code")
    created = control_client.post(
        "/control/tenants/provision",
        headers=auth_headers,
        json=payload,
    )
    assert created.status_code == 200, created.text
    created_data = created.json()["data"]
    tenant_id = created_data["tenant"]["id"]
    provision_ids = [item["id"] for item in created_data["provisions"]]

    async def seed_opening() -> int:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            provision = (
                await db.execute(
                    select(ControlTenantProvisionModel).where(
                        ControlTenantProvisionModel.tenant_id == tenant_id,
                        ControlTenantProvisionModel.application_id == products["wms_id"],
                    )
                )
            ).scalar_one()
            provision.status = "succeeded"
            opening = ControlTenantApplicationModel(
                site_id=1,
                tenant_id=tenant_id,
                application_id=products["wms_id"],
                target_tenant_code=code,
                status=0,
            )
            db.add(opening)
            await db.flush()
            task_rows = (
                await db.execute(
                    select(BusinessTaskModel).where(
                        BusinessTaskModel.handler_code == "control.tenant_provision",
                        BusinessTaskModel.biz_id.in_([str(item) for item in provision_ids]),
                    )
                )
            ).scalars().all()
            for task in task_rows:
                task.status = "canceled"
            await db.commit()
            return opening.id

    opening_id = asyncio.run(seed_opening())
    listed = control_client.get(
        "/control/tenant-provisions",
        headers=auth_headers,
        params={"tenant_id": tenant_id, "page_no": 1, "page_size": 100},
    )
    assert listed.status_code == 200, listed.text
    by_application = {
        item["application_id"]: item["tenant_application_id"]
        for item in listed.json()["data"]["items"]
    }
    assert by_application[products["wms_id"]] == opening_id
    assert by_application[products["mes_id"]] is None


@pytest.mark.parametrize("failure", ["invalid_package", "celery_disabled"])
def test_create_tenant_with_provisions_rolls_back_everything_on_preflight_failure(
    control_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    products = asyncio.run(_seed_products())
    package_id = asyncio.run(_control_package_id())
    code = f"rollback{uuid4().hex[:8]}"
    payload = _payload(products, code=code, package_id=package_id)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", failure != "celery_disabled")
    monkeypatch.setattr("app.api.v1.module_control.tenant_provision.service.settings.CELERY_ENABLED", failure != "celery_disabled")
    if failure == "invalid_package":
        payload["applications"][0]["application_package_id"] = 99999999  # type: ignore[index]

    response = control_client.post("/control/tenants/provision", headers=auth_headers, json=payload)
    assert response.status_code == (503 if failure == "celery_disabled" else 404), response.text

    async def assert_zero() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            tenant_count = (await db.execute(select(func.count()).select_from(TenantModel).where(TenantModel.code == code))).scalar_one()
            user_count = (await db.execute(select(func.count()).select_from(UserModel).where(UserModel.username == f"{code}_admin"))).scalar_one()
            assert tenant_count == 0
            assert user_count == 0

    asyncio.run(assert_zero())
