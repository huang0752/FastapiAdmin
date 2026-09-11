"""One-time Control tenant-provision ticket contracts."""

from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.module_control.application_package.model import ControlApplicationPackageModel
from app.api.v1.module_control.model import ControlApplicationModel
from app.api.v1.module_control.tenant_provision.model import ControlTenantProvisionModel, ControlTenantProvisionTicketModel
from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.model import UserModel
from app.core.dependencies import AuthPermission
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


async def _seed_provision_case(*, ticket_ttl_seconds: int = 60) -> dict[str, object]:
    from app.api.v1.module_control.tenant_provision.ticket_service import ControlProvisionTicketService
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    secret = f"secret-{suffix}"
    async with async_db_session() as db:
        site = await db.get(SiteModel, 1)
        assert site is not None
        tenant = TenantModel(
            name=f"开户企业{suffix}",
            code=f"tenant{suffix}",
            contact_name="企业联系人",
            contact_phone="13800000000",
            contact_email=f"contact-{suffix}@example.com",
            address="企业地址",
            unified_social_credit_code=None,
            site_id=site.id,
            status=0,
        )
        owner = UserModel(
            tenant_id=1,
            username=f"tenant{suffix}_admin",
            password=PwdUtil.hash_password("test-password"),
            name="示例企业管理员",
            mobile=None,
            email=f"owner-{suffix}@example.com",
            status=0,
        )
        application = ControlApplicationModel(
            site_id=site.id,
            code=f"ticket{suffix}",
            name="票据目标应用",
            base_url="http://target.example.test",
            callback_url="http://target.example.test/web#/auth/control-sso-callback",
            client_id=f"client-{suffix}",
            client_secret_hash=PwdUtil.hash_password(secret),
            provisioning_url="http://target.example.test/system/auth/control/tenant/provision",
            provisioning_enabled=True,
            status=0,
        )
        db.add_all([tenant, owner, application])
        await db.flush()
        db.add(TenantUserModel(user_id=owner.id, tenant_id=tenant.id, role="owner", is_default=1))
        package = ControlApplicationPackageModel(
            site_id=site.id,
            application_id=application.id,
            code="pro",
            name="专业版",
            target_package_code="wmspro",
            is_default=True,
            status=0,
        )
        db.add(package)
        await db.flush()
        provision = ControlTenantProvisionModel(
            site_id=site.id,
            tenant_id=tenant.id,
            application_id=application.id,
            application_package_id=package.id,
            owner_user_id=owner.id,
            provision_request_uuid=str(uuid4()),
            desired_target_tenant_code=tenant.code,
            status="processing",
        )
        db.add(provision)
        await db.flush()
        plain_code = await ControlProvisionTicketService(db).issue(provision.id, ttl_seconds=ticket_ttl_seconds)
        await db.commit()
        return {
            "code": plain_code,
            "secret": secret,
            "client_id": application.client_id,
            "application_id": application.id,
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "package_id": package.id,
            "provision_id": provision.id,
            "request_uuid": provision.provision_request_uuid,
            "tenant_uuid": tenant.uuid,
            "site_code": site.code,
        }


def _exchange(client: TestClient, case: dict[str, object], *, code: str | None = None, client_id: str | None = None, secret: str | None = None):
    return client.post(
        "/control/provisioning/exchange",
        auth=(client_id or str(case["client_id"]), secret or str(case["secret"])),
        json={"code": code or str(case["code"])},
    )


def test_provision_exchange_route_is_public_basic_auth_only() -> None:
    from app.api.v1.module_control import control_router
    from app.api.v1.module_control.tenant_provision.schema import ControlProvisionExchangeInSchema

    route = next(
        item
        for item in control_router.routes
        if isinstance(item, APIRoute) and item.path == "/control/provisioning/exchange" and "POST" in item.methods
    )
    assert all(not isinstance(dependency.call, AuthPermission) for dependency in route.dependant.dependencies)
    code_schema = ControlProvisionExchangeInSchema.model_json_schema()["properties"]["code"]
    assert code_schema["type"] == "string"
    assert code_schema["minLength"] == 20
    assert code_schema["maxLength"] == 512


@pytest.mark.parametrize(
    ("code", "secret_marker"),
    [
        ("short-sensitive", "short-sensitive"),
        ("long-sensitive-marker" + "x" * 512, "long-sensitive-marker"),
        ({"secret": "typed-sensitive-marker"}, "typed-sensitive-marker"),
    ],
)
def test_invalid_provision_code_does_not_leak_in_response_or_logs(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    code: object,
    secret_marker: str,
) -> None:
    from app.core import exceptions

    log_calls: list[tuple[object, ...]] = []

    def capture_error(*args: object, **kwargs: object) -> None:
        log_calls.append((*args, kwargs))

    monkeypatch.setattr(exceptions.logger, "error", capture_error)

    response = control_client.post(
        "/control/provisioning/exchange",
        auth=("invalid-client", "invalid-secret"),
        json={"code": code},
    )

    assert response.status_code == 422, response.text
    assert response.json()["data"] is None
    assert secret_marker not in response.text
    assert secret_marker not in repr(log_calls)


def test_provision_ticket_stores_only_sha256_and_returns_complete_safe_claims(control_client: TestClient) -> None:
    case = asyncio.run(_seed_provision_case())

    async def assert_hash_only() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket = (
                await db.execute(select(ControlTenantProvisionTicketModel).where(ControlTenantProvisionTicketModel.provision_id == case["provision_id"]))
            ).scalar_one()
            assert ticket.code_hash == hashlib.sha256(str(case["code"]).encode()).hexdigest()
            assert str(case["code"]) not in ticket.code_hash

    asyncio.run(assert_hash_only())
    response = _exchange(control_client, case)
    assert response.status_code == 200, response.text
    claims = response.json()["data"]
    assert claims == {
        "provision_request_uuid": case["request_uuid"],
        "central_tenant_uuid": case["tenant_uuid"],
        "central_tenant_code": claims["central_tenant_code"],
        "tenant_name": claims["tenant_name"],
        "unified_social_credit_code": None,
        "contact_name": "企业联系人",
        "contact_phone": "13800000000",
        "contact_email": claims["contact_email"],
        "address": "企业地址",
        "site_code": case["site_code"],
        "target_tenant_code": claims["central_tenant_code"],
        "target_package_code": "wmspro",
        "owner": {
            "central_user_uuid": claims["owner"]["central_user_uuid"],
            "username": claims["owner"]["username"],
            "name": "示例企业管理员",
            "mobile": None,
            "email": claims["owner"]["email"],
            "avatar": None,
            "status": 0,
        },
        "issuer": "http://testserver",
    }
    serialized = response.text.lower()
    assert "secret" not in serialized
    assert "code_hash" not in serialized
    assert str(case["code"]) not in response.text


def test_provision_ticket_is_single_use_and_wrong_client_cannot_consume(control_client: TestClient) -> None:
    case = asyncio.run(_seed_provision_case())
    wrong_secret = _exchange(control_client, case, secret="wrong-secret")
    wrong_client = _exchange(control_client, case, client_id="unknown-client")
    assert wrong_secret.status_code == 401, wrong_secret.text
    assert wrong_client.status_code == 401, wrong_client.text

    first = _exchange(control_client, case)
    replay = _exchange(control_client, case)
    assert first.status_code == 200, first.text
    assert replay.status_code == 401, replay.text


def test_provision_ticket_rejects_expired_code(control_client: TestClient) -> None:
    case = asyncio.run(_seed_provision_case())

    async def expire() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket = (
                await db.execute(select(ControlTenantProvisionTicketModel).where(ControlTenantProvisionTicketModel.provision_id == case["provision_id"]))
            ).scalar_one()
            ticket.issued_at = datetime.now(UTC) - timedelta(minutes=2)
            ticket.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()

    asyncio.run(expire())
    assert _exchange(control_client, case).status_code == 401


@pytest.mark.parametrize("disabled_state", ["application", "tenant", "owner", "owner_membership", "package"])
def test_provision_ticket_rejects_revoked_current_state(control_client: TestClient, disabled_state: str) -> None:
    case = asyncio.run(_seed_provision_case())

    async def disable() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            if disabled_state == "application":
                target = await db.get(ControlApplicationModel, case["application_id"])
                target.status = 1
            elif disabled_state == "tenant":
                target = await db.get(TenantModel, case["tenant_id"])
                target.status = 2
            elif disabled_state == "owner":
                target = await db.get(UserModel, case["owner_id"])
                target.status = 1
            elif disabled_state == "owner_membership":
                target = (
                    await db.execute(
                        select(TenantUserModel).where(
                            TenantUserModel.tenant_id == case["tenant_id"],
                            TenantUserModel.user_id == case["owner_id"],
                        )
                    )
                ).scalar_one()
                target.role = "member"
            else:
                target = await db.get(ControlApplicationPackageModel, case["package_id"])
                target.status = 1
            assert target is not None
            await db.commit()

    asyncio.run(disable())
    response = _exchange(control_client, case)
    assert response.status_code == 403, response.text


def test_concurrent_provision_exchange_allows_exactly_one_redemption(control_client: TestClient) -> None:
    case = asyncio.run(_seed_provision_case())
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: _exchange(control_client, case), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 401]
