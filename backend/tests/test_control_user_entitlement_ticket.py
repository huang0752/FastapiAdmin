"""One-time Control user-entitlement ticket security contracts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient
from fastapi_limiter.depends import RateLimiter
from sqlalchemy import select

from app.api.v1.module_control.model import (
    ControlApplicationModel,
    ControlTenantApplicationModel,
    ControlUserApplicationGrantModel,
)
from app.api.v1.module_control.user_entitlement.model import ControlUserEntitlementTicketModel
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


async def _seed_ticket_case(*, ttl_seconds: int = 60, desired_state: str = "active") -> dict[str, object]:
    from app.api.v1.module_control.user_entitlement.ticket_service import ControlUserEntitlementTicketService
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    secret = f"entitlement-secret-{suffix}"
    event_id = f"event-{uuid4().hex}"
    async with async_db_session() as db:
        site = SiteModel(
            code=f"entsite_{suffix}",
            name=f"授权站点{suffix}",
            status=0,
        )
        db.add(site)
        await db.flush()
        tenant = TenantModel(name=f"授权企业{suffix}", code=f"ent{suffix}", site_id=site.id, status=0)
        user = UserModel(
            tenant_id=1,
            username=f"ent_{suffix}",
            password=PwdUtil.hash_password("test-password"),
            name="签发时姓名",
            mobile="13800000000",
            email=f"issued-{suffix}@example.com",
            avatar="https://example.test/issued.png",
            status=0,
        )
        application = ControlApplicationModel(
            site_id=site.id,
            code=f"wms{suffix}",
            name="授权目标应用",
            base_url="https://target.example.test",
            callback_url="https://target.example.test/web#/auth/control-sso-callback",
            client_id=f"ent-client-{suffix}",
            client_secret_hash=PwdUtil.hash_password(secret),
            entitlement_sync_url="https://target.example.test/api/v1/system/auth/control/access/sync",
            entitlement_sync_enabled=True,
            status=0,
        )
        wrong_application = ControlApplicationModel(
            site_id=site.id,
            code=f"mes{suffix}",
            name="其他应用",
            base_url="https://other.example.test",
            callback_url="https://other.example.test/callback",
            client_id=f"wrong-client-{suffix}",
            client_secret_hash=PwdUtil.hash_password(f"wrong-secret-{suffix}"),
            status=0,
        )
        db.add_all([tenant, user, application, wrong_application])
        await db.flush()
        db.add(TenantUserModel(user_id=user.id, tenant_id=tenant.id, role="member", is_default=1))
        opening = ControlTenantApplicationModel(
            site_id=site.id,
            tenant_id=tenant.id,
            application_id=application.id,
            target_tenant_code=f"target{suffix}",
            status=0,
        )
        db.add(opening)
        await db.flush()
        grant = ControlUserApplicationGrantModel(
            site_id=site.id,
            tenant_application_id=opening.id,
            tenant_id=tenant.id,
            user_id=user.id,
            desired_state=desired_state,
            status=0 if desired_state == "active" else 1,
            sync_status="processing",
            sync_version=3,
            last_event_id=event_id,
        )
        db.add(grant)
        await db.flush()
        code = await ControlUserEntitlementTicketService(db).issue(
            grant.id,
            event_id=event_id,
            sync_version=3,
            ttl_seconds=ttl_seconds,
        )
        await db.commit()
        return {
            "application_id": application.id,
            "client_id": application.client_id,
            "code": code,
            "event_id": event_id,
            "grant_id": grant.id,
            "opening_id": opening.id,
            "secret": secret,
            "site_id": site.id,
            "tenant_id": tenant.id,
            "tenant_uuid": tenant.uuid,
            "user_id": user.id,
            "user_uuid": user.uuid,
            "wrong_client_id": wrong_application.client_id,
            "wrong_secret": f"wrong-secret-{suffix}",
        }


def _exchange(
    client: TestClient,
    case: dict[str, object],
    *,
    code: object | None = None,
    client_id: str | None = None,
    secret: str | None = None,
):
    return client.post(
        "/control/user-entitlements/exchange",
        auth=(client_id or str(case["client_id"]), secret or str(case["secret"])),
        json={"code": case["code"] if code is None else code},
    )


def test_entitlement_exchange_route_is_public_basic_auth_only() -> None:
    from app.api.v1.module_control import control_router
    from app.api.v1.module_control.user_entitlement.schema import ControlUserEntitlementExchangeIn

    route = next(
        item
        for item in control_router.routes
        if isinstance(item, APIRoute)
        and item.path == "/control/user-entitlements/exchange"
        and "POST" in item.methods
    )
    assert all(not isinstance(dependency.call, AuthPermission) for dependency in route.dependant.dependencies)
    code_schema = ControlUserEntitlementExchangeIn.model_json_schema()["properties"]["code"]
    assert code_schema["type"] == "string"
    assert code_schema["minLength"] == 20
    assert code_schema["maxLength"] == 512
    dedicated_limits = [
        dependency.call
        for dependency in route.dependant.dependencies
        if isinstance(dependency.call, RateLimiter)
    ]
    assert len(dedicated_limits) == 2
    assert sorted((item.times, item.milliseconds) for item in dedicated_limits) == [
        (10, 60_000),
        (120, 60_000),
    ]
    assert dedicated_limits[0].identifier.__name__ == "_control_peer_identifier"
    assert dedicated_limits[1].identifier.__name__ == "_control_client_identifier"


def test_exchange_identifiers_ignore_forwarded_headers_and_hash_client_identity() -> None:
    from app.api.v1.module_control.user_entitlement.controller import (
        _control_client_identifier,
        _control_peer_identifier,
    )

    def request(*, client_id: str, forwarded_for: str) -> Request:
        credentials = base64.b64encode(f"{client_id}:secret".encode()).decode()
        return Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "https",
                "server": ("control.example", 443),
                "client": ("10.0.0.8", 43210),
                "path": "/api/v1/control/user-entitlements/exchange",
                "root_path": "/api/v1",
                "query_string": b"",
                "headers": [
                    (b"authorization", f"Basic {credentials}".encode()),
                    (b"x-forwarded-for", forwarded_for.encode()),
                ],
            }
        )

    first = request(client_id="machine-a", forwarded_for="198.51.100.1")
    forged = request(client_id="machine-a", forwarded_for="203.0.113.99")
    other_client = request(client_id="machine-b", forwarded_for="198.51.100.1")
    assert asyncio.run(_control_client_identifier(first)) == asyncio.run(_control_client_identifier(forged))
    assert asyncio.run(_control_client_identifier(first)) != asyncio.run(_control_client_identifier(other_client))
    assert asyncio.run(_control_peer_identifier(first)) == asyncio.run(_control_peer_identifier(forged))
    identifier = asyncio.run(_control_client_identifier(first))
    assert "machine-a" not in identifier
    assert "secret" not in identifier

    canonical = base64.b64encode(b"machine-a:secret").decode()
    noncanonical = f"{canonical[:4]}!{canonical[4:]}"
    noncanonical_request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("control.example", 443),
            "client": ("10.0.0.8", 43210),
            "path": "/api/v1/control/user-entitlements/exchange",
            "root_path": "/api/v1",
            "query_string": b"",
            "headers": [(b"authorization", f"Basic {noncanonical}".encode())],
        }
    )
    assert asyncio.run(_control_client_identifier(first)) == asyncio.run(
        _control_client_identifier(noncanonical_request)
    )

    def trusted_proxy_request(forwarded_for: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "https",
                "server": ("control.example", 443),
                "client": ("127.0.0.1", 43210),
                "path": "/api/v1/control/user-entitlements/exchange",
                "root_path": "/api/v1",
                "query_string": b"",
                "headers": [(b"x-forwarded-for", forwarded_for.encode())],
            }
        )

    first_real_peer = trusted_proxy_request("198.51.100.10")
    second_real_peer = trusted_proxy_request("198.51.100.11")
    assert asyncio.run(_control_peer_identifier(first_real_peer)) != asyncio.run(
        _control_peer_identifier(second_real_peer)
    )
    assert asyncio.run(_control_peer_identifier(trusted_proxy_request("198.51.100.10, 203.0.113.2"))) == (
        asyncio.run(_control_peer_identifier(trusted_proxy_request("not-an-ip")))
    )


def test_exchange_identifier_malformed_non_ascii_basic_falls_back_without_500() -> None:
    from app.api.v1.module_control.user_entitlement.controller import _control_client_identifier

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("control.example", 443),
            "client": ("10.0.0.8", 43210),
            "path": "/api/v1/control/user-entitlements/exchange",
            "root_path": "/api/v1",
            "query_string": b"",
            "headers": [(b"authorization", b"Basic \xff")],
        }
    )
    identifier = asyncio.run(_control_client_identifier(request))
    assert identifier.startswith("control-user-entitlement:client-fallback-peer:")


def test_control_nginx_overwrites_untrusted_forwarded_ip_headers() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    content = (repository_root / "deploy/nginx/control.conf").read_text(encoding="utf-8")
    compose = (repository_root / "deploy/docker-compose.control.yml").read_text(encoding="utf-8")
    assert "$proxy_add_x_forwarded_for" not in content
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in content.replace("  ", " ")
    assert "proxy_set_header X-Real-IP $remote_addr;" in content.replace("  ", " ")
    assert "server 127.0.0.1:8100;" in content
    assert '"127.0.0.1:8100:8100"' in compose


def test_control_client_auth_runs_kdf_off_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.module_control import service as control_service
    from app.core.exceptions import CustomException

    class Result:
        @staticmethod
        def scalar_one_or_none():
            return SimpleNamespace(client_secret_hash="known-hash")

    class DB:
        async def execute(self, _statement):
            return Result()

    def slow_reject(*_args: object) -> bool:
        time.sleep(0.12)
        return False

    monkeypatch.setattr(control_service.PwdUtil, "verify_password", slow_reject)

    async def exercise() -> float:
        started = time.perf_counter()
        authentication = asyncio.create_task(
            control_service.authenticate_control_client(
                DB(),
                client_id="known-client",
                client_secret="wrong-secret",
            )
        )
        await asyncio.sleep(0.02)
        timer_elapsed = time.perf_counter() - started
        with pytest.raises(CustomException):
            await authentication
        return timer_elapsed

    assert asyncio.run(exercise()) < 0.08


def test_client_auth_admission_precedes_sql_and_rejects_excess_without_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_control import service as control_service
    from app.core.exceptions import CustomException

    started = threading.Event()
    release = threading.Event()
    db_calls = 0
    kdf_calls = 0

    class Result:
        @staticmethod
        def scalar_one_or_none():
            return SimpleNamespace(client_secret_hash="known-hash")

    class DB:
        async def execute(self, _statement):
            nonlocal db_calls
            db_calls += 1
            return Result()

    def blocked_reject(*_args: object) -> bool:
        nonlocal kdf_calls
        kdf_calls += 1
        if kdf_calls == 4:
            started.set()
        assert release.wait(2)
        return False

    monkeypatch.setattr(control_service, "_CONTROL_CLIENT_AUTH_ADMISSION", control_service.anyio.CapacityLimiter(4))
    monkeypatch.setattr(control_service.PwdUtil, "verify_password", blocked_reject)

    async def exercise() -> list[object]:
        calls = [
            asyncio.create_task(
                control_service.authenticate_control_client(
                    DB(),
                    client_id=f"client-{index}",
                    client_secret="wrong-secret",
                )
            )
            for index in range(5)
        ]
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0.12)
        assert db_calls == 4
        release.set()
        return await asyncio.gather(*calls, return_exceptions=True)

    results = asyncio.run(exercise())
    assert db_calls == 4
    assert kdf_calls == 4
    assert sum(isinstance(item, CustomException) and item.status_code == 503 for item in results) == 1
    assert control_service._CONTROL_CLIENT_AUTH_ADMISSION.borrowed_tokens == 0


def test_client_auth_cancellation_releases_admission_without_sql_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_control import service as control_service
    from app.core.exceptions import CustomException

    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    db_calls = 0

    class Result:
        @staticmethod
        def scalar_one_or_none():
            return None

    class DB:
        async def execute(self, _statement):
            nonlocal db_calls
            db_calls += 1
            if db_calls == 1:
                first_entered.set()
                await release_first.wait()
            return Result()

    limiter = control_service.anyio.CapacityLimiter(1)
    monkeypatch.setattr(control_service, "_CONTROL_CLIENT_AUTH_ADMISSION", limiter)

    async def exercise() -> None:
        first = asyncio.create_task(
            control_service.authenticate_control_client(
                DB(),
                client_id="first",
                client_secret="secret",
            )
        )
        await first_entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert limiter.borrowed_tokens == 0

        with pytest.raises(CustomException):
            await control_service.authenticate_control_client(
                DB(),
                client_id="second",
                client_secret="secret",
            )
        assert db_calls == 2
        assert limiter.borrowed_tokens == 0

    asyncio.run(exercise())


def test_unknown_and_known_wrong_clients_pay_same_kdf_and_return_generic_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_control import service as control_service
    from app.core.exceptions import CustomException

    known = SimpleNamespace(client_secret_hash="known-hash")

    class Result:
        def __init__(self, application: object | None) -> None:
            self.application = application

        def scalar_one_or_none(self):
            return self.application

    class DB:
        def __init__(self, application: object | None) -> None:
            self.application = application

        async def execute(self, _statement):
            return Result(self.application)

    checked_hashes: list[str] = []

    def reject(_secret: str, password_hash: str) -> bool:
        checked_hashes.append(password_hash)
        return False

    monkeypatch.setattr(control_service.PwdUtil, "verify_password", reject)

    async def reject_message(application: object | None) -> tuple[int, str]:
        with pytest.raises(CustomException) as caught:
            await control_service.authenticate_control_client(
                DB(application),
                client_id="candidate",
                client_secret="wrong-secret",
            )
        return caught.value.status_code, caught.value.msg

    unknown = asyncio.run(reject_message(None))
    known_wrong = asyncio.run(reject_message(known))
    assert unknown == known_wrong == (401, "Client 认证失败")
    assert len(checked_hashes) == 2
    assert checked_hashes[0].startswith("$pbkdf2-sha256$600000$")
    assert checked_hashes[1] == "known-hash"


def test_password_verification_uses_constant_time_digest_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils import hash_bcrpy_util

    derived = b"derived-secret"
    compared: list[tuple[bytes, bytes]] = []
    monkeypatch.setattr(hash_bcrpy_util.hashlib, "pbkdf2_hmac", lambda *_args: derived)

    def compare(left: bytes, right: bytes) -> bool:
        compared.append((left, right))
        return left == right

    monkeypatch.setattr(hash_bcrpy_util.hmac, "compare_digest", compare)
    encoded = (
        "$pbkdf2-sha256$600000$"
        + base64.b64encode(b"fixed-salt").decode()
        + "$"
        + base64.b64encode(derived).decode()
    )
    assert PwdUtil.verify_password("candidate", encoded) is True
    assert compared == [(derived, derived)]


def test_ticket_issue_uses_48_random_bytes_and_persists_only_hash(control_client: TestClient) -> None:
    case = asyncio.run(_seed_ticket_case())
    code = str(case["code"])
    assert len(code) == 64
    assert code.replace("-", "").replace("_", "").isalnum()

    async def assert_ticket() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket = (
                await db.execute(
                    select(ControlUserEntitlementTicketModel).where(
                        ControlUserEntitlementTicketModel.grant_id == case["grant_id"]
                    )
                )
            ).scalar_one()
            assert ticket.code_hash == hashlib.sha256(code.encode()).hexdigest()
            assert ticket.desired_state == "active"
            assert code not in repr(ticket.__dict__)

    asyncio.run(assert_ticket())


def test_exchange_returns_fresh_database_claims_matching_product_contract(control_client: TestClient) -> None:
    case = asyncio.run(_seed_ticket_case())

    async def mutate_current_data() -> dict[str, str]:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            application = await db.get(ControlApplicationModel, case["application_id"])
            opening = await db.get(ControlTenantApplicationModel, case["opening_id"])
            tenant = await db.get(TenantModel, case["tenant_id"])
            user = await db.get(UserModel, case["user_id"])
            site = await db.get(SiteModel, case["site_id"])
            assert application and opening and tenant and user and site
            application.code = f"fresh{uuid4().hex[:8]}"
            opening.target_tenant_code = f"mapped{uuid4().hex[:8]}"
            tenant.code = f"central{uuid4().hex[:8]}"
            user.name = "兑换时真实姓名"
            user.mobile = None
            user.email = None
            user.avatar = None
            site.code = f"site_{uuid4().hex[:8]}"
            await db.commit()
            return {
                "application_code": application.code,
                "target_tenant_code": opening.target_tenant_code,
                "central_tenant_code": tenant.code,
                "site_code": site.code,
            }

    fresh = asyncio.run(mutate_current_data())
    response = _exchange(control_client, case)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "issuer": "http://testserver/api/v1",
        "event_id": case["event_id"],
        "sync_version": 3,
        "desired_state": "active",
        "application_code": fresh["application_code"],
        "site_code": fresh["site_code"],
        "central_tenant_uuid": case["tenant_uuid"],
        "central_tenant_code": fresh["central_tenant_code"],
        "target_tenant_code": fresh["target_tenant_code"],
        "central_user_uuid": case["user_uuid"],
        "name": "兑换时真实姓名",
        "mobile": None,
        "email": None,
        "avatar": None,
        "user_status": 0,
    }


def test_wrong_secret_and_wrong_application_cannot_consume_ticket(control_client: TestClient) -> None:
    case = asyncio.run(_seed_ticket_case())
    assert _exchange(control_client, case, secret="not-the-secret").status_code == 401
    assert (
        _exchange(
            control_client,
            case,
            client_id=str(case["wrong_client_id"]),
            secret=str(case["wrong_secret"]),
        ).status_code
        == 400
    )
    assert _exchange(control_client, case).status_code == 200


def test_redeemed_and_expired_ticket_are_rejected_with_400(control_client: TestClient) -> None:
    redeemed = asyncio.run(_seed_ticket_case())
    assert _exchange(control_client, redeemed).status_code == 200
    assert _exchange(control_client, redeemed).status_code == 400

    expired = asyncio.run(_seed_ticket_case())

    async def expire() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket = (
                await db.execute(
                    select(ControlUserEntitlementTicketModel).where(
                        ControlUserEntitlementTicketModel.grant_id == expired["grant_id"]
                    )
                )
            ).scalar_one()
            ticket.issued_at = datetime.now(UTC) - timedelta(minutes=2)
            ticket.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()

    asyncio.run(expire())
    assert _exchange(control_client, expired).status_code == 400


@pytest.mark.parametrize("changed_field", ["last_event_id", "sync_version", "desired_state"])
def test_ticket_rejects_changed_grant_generation(control_client: TestClient, changed_field: str) -> None:
    case = asyncio.run(_seed_ticket_case())

    async def change_generation() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
            assert grant is not None
            if changed_field == "last_event_id":
                grant.last_event_id = f"new-{uuid4().hex}"
            elif changed_field == "sync_version":
                grant.sync_version += 1
            else:
                grant.desired_state = "inactive"
                grant.status = 1
            await db.commit()

    asyncio.run(change_generation())
    assert _exchange(control_client, case).status_code == 400


def test_ticket_rejects_site_binding_mismatch(control_client: TestClient) -> None:
    case = asyncio.run(_seed_ticket_case())

    async def move_application_to_other_site() -> None:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            other_site = SiteModel(code=f"other_{uuid4().hex[:8]}", name=f"其他站点{uuid4().hex[:8]}", status=0)
            db.add(other_site)
            await db.flush()
            application = await db.get(ControlApplicationModel, case["application_id"])
            assert application is not None
            application.site_id = other_site.id
            await db.commit()

    asyncio.run(move_application_to_other_site())
    assert _exchange(control_client, case).status_code == 400


def test_concurrent_exchange_allows_exactly_one_redemption(control_client: TestClient) -> None:
    case = asyncio.run(_seed_ticket_case())
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: _exchange(control_client, case), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 400]


@pytest.mark.parametrize(
    ("code", "marker"),
    [
        ("short-secret-marker", "short-secret-marker"),
        ({"secret": "typed-secret-marker"}, "typed-secret-marker"),
    ],
)
def test_invalid_code_and_client_secret_do_not_leak_in_response_or_logs(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    code: object,
    marker: str,
) -> None:
    from app.core import exceptions

    log_calls: list[tuple[object, ...]] = []

    def capture_error(*args: object, **kwargs: object) -> None:
        log_calls.append((*args, kwargs))

    monkeypatch.setattr(exceptions.logger, "error", capture_error)
    client_secret = "basic-client-secret-marker"
    response = control_client.post(
        "/control/user-entitlements/exchange",
        auth=("invalid-client", client_secret),
        json={"code": code},
    )
    assert response.status_code == 400
    assert marker not in response.text
    assert marker not in repr(log_calls)
    assert client_secret not in response.text
    assert client_secret not in repr(log_calls)


@pytest.mark.parametrize(
    "raw_body",
    [
        b'{"code":"malformed-sensitive-marker"',
        json.dumps(
            {
                "code": "extra-sensitive-marker-" + "x" * 48,
                "unexpected": True,
            }
        ).encode(),
        json.dumps({"code": {"value": "typed-sensitive-marker"}}).encode(),
        json.dumps({"code": "short-marker"}).encode(),
    ],
)
def test_raw_invalid_payload_is_generic_400_does_not_leak_and_does_not_consume_ticket(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    raw_body: bytes,
) -> None:
    from app.core import exceptions

    case = asyncio.run(_seed_ticket_case())
    markers = (
        "malformed-sensitive-marker",
        "extra-sensitive-marker",
        "typed-sensitive-marker",
        "short-marker",
    )
    log_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        exceptions.logger,
        "error",
        lambda *args, **kwargs: log_calls.append((*args, kwargs)),
    )
    response = control_client.post(
        "/control/user-entitlements/exchange",
        auth=(str(case["client_id"]), str(case["secret"])),
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["msg"] == "用户授权请求无效"
    for marker in markers:
        assert marker not in response.text
        assert marker not in repr(log_calls)

    assert _exchange(control_client, case).status_code == 200


def test_exchange_requires_application_json_and_does_not_consume_ticket(control_client: TestClient) -> None:
    case = asyncio.run(_seed_ticket_case())
    response = control_client.post(
        "/control/user-entitlements/exchange",
        auth=(str(case["client_id"]), str(case["secret"])),
        content=json.dumps({"code": case["code"]}),
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 400
    assert response.json()["msg"] == "用户授权请求无效"
    assert str(case["code"]) not in response.text
    assert _exchange(control_client, case).status_code == 200


def test_streaming_body_stops_after_limit_and_never_calls_service(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_control.user_entitlement import controller
    from app.core.exceptions import CustomException

    marker = b"stream-sensitive-marker"
    chunk = marker + b"x" * (512 - len(marker))
    chunks = [chunk] * (2 * 1024 * 1024 // len(chunk))
    received_bytes = 0
    receive_calls = 0
    service_calls = 0
    case = asyncio.run(_seed_ticket_case())

    async def receive():
        nonlocal received_bytes, receive_calls
        current = chunks[receive_calls]
        receive_calls += 1
        received_bytes += len(current)
        return {
            "type": "http.request",
            "body": current,
            "more_body": receive_calls < len(chunks),
        }

    async def forbidden_exchange(*_args, **_kwargs):
        nonlocal service_calls
        service_calls += 1
        raise AssertionError("oversized body must not call exchange service")

    monkeypatch.setattr(controller.ControlUserEntitlementTicketService, "exchange", forbidden_exchange)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("control.example", 443),
            "path": "/api/v1/control/user-entitlements/exchange",
            "root_path": "/api/v1",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        },
        receive,
    )

    async def exercise() -> None:
        with pytest.raises(CustomException) as caught:
            await controller.exchange_user_entitlement(
                request=request,
                credentials=HTTPBasicCredentials(
                    username=str(case["client_id"]),
                    password=str(case["secret"]),
                ),
                db=None,
            )
        assert caught.value.status_code == 400
        assert marker.decode() not in caught.value.msg

    asyncio.run(exercise())
    assert received_bytes <= 1024 + len(chunk)
    assert receive_calls == 3
    assert service_calls == 0
    assert request.state.skip_operation_log is True

    async def ticket_status() -> str:
        from app.core.database import async_db_session

        async with async_db_session() as db:
            ticket = (
                await db.execute(
                    select(ControlUserEntitlementTicketModel).where(
                        ControlUserEntitlementTicketModel.grant_id == case["grant_id"]
                    )
                )
            ).scalar_one()
            return ticket.status

    assert asyncio.run(ticket_status()) == "issued"


def test_declared_oversized_body_is_rejected_before_stream_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.module_control.user_entitlement import controller
    from app.core.exceptions import CustomException

    receive_calls = 0

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"sensitive", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("control.example", 443),
            "path": "/api/v1/control/user-entitlements/exchange",
            "root_path": "/api/v1",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"2097152"),
            ],
        },
        receive,
    )
    monkeypatch.setattr(
        controller.ControlUserEntitlementTicketService,
        "exchange",
        lambda *_args, **_kwargs: pytest.fail("service must not be called"),
    )

    async def exercise() -> None:
        with pytest.raises(CustomException) as caught:
            await controller.exchange_user_entitlement(
                request=request,
                credentials=HTTPBasicCredentials(username="client", password="secret"),
                db=None,
            )
        assert caught.value.status_code == 400

    asyncio.run(exercise())
    assert receive_calls == 0


def test_real_exchange_rate_limiter_rejects_eleventh_request_before_service(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fastapi_limiter.depends as patched_depends
    from fastapi_limiter import FastAPILimiter

    from app.api.v1.module_control.user_entitlement import controller
    from app.core.database import async_db_session

    spec = importlib.util.spec_from_file_location("unpatched_fastapi_limiter_depends", patched_depends.__file__)
    assert spec and spec.loader
    unpatched = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(unpatched)

    checks: dict[int, int] = {}
    service_calls = 0

    async def check(limiter, _key):
        limiter_key = id(limiter)
        checks[limiter_key] = checks.get(limiter_key, 0) + 1
        return 0 if checks[limiter_key] <= limiter.times else 1000

    async def identifier(_request):
        return "entitlement-test-client"

    async def callback(_request, _response, _expire):
        raise HTTPException(status_code=429, detail="rate limited")

    async def forbidden_exchange(*_args, **_kwargs):
        nonlocal service_calls
        service_calls += 1
        raise AssertionError("invalid body must not call exchange service")

    monkeypatch.setattr(RateLimiter, "__call__", unpatched.RateLimiter.__call__)
    monkeypatch.setattr(RateLimiter, "_check", check)
    monkeypatch.setattr(FastAPILimiter, "redis", object())
    monkeypatch.setattr(FastAPILimiter, "identifier", identifier)
    monkeypatch.setattr(FastAPILimiter, "http_callback", callback)
    monkeypatch.setattr(controller.ControlUserEntitlementTicketService, "exchange", forbidden_exchange)

    case = asyncio.run(_seed_ticket_case())
    responses = [
        control_client.post(
            "/control/user-entitlements/exchange",
            auth=(str(case["client_id"]), str(case["secret"])),
            content=b'{"code":"invalid"}',
            headers={"Content-Type": "application/json"},
        )
        for _index in range(11)
    ]
    assert [response.status_code for response in responses[:10]] == [400] * 10
    assert responses[10].status_code == 429
    assert service_calls == 0

    async def ticket_status() -> str:

        async with async_db_session() as db:
            ticket = (
                await db.execute(
                    select(ControlUserEntitlementTicketModel).where(
                        ControlUserEntitlementTicketModel.grant_id == case["grant_id"]
                    )
                )
            ).scalar_one()
            return ticket.status

    assert asyncio.run(ticket_status()) == "issued"


def _install_real_exchange_rate_limiter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prefix: str,
):
    import fastapi_limiter.depends as patched_depends
    import redis
    import redis.asyncio as async_redis
    from fastapi_limiter import FastAPILimiter, default_identifier

    spec = importlib.util.spec_from_file_location(
        f"unpatched_fastapi_limiter_depends_{prefix}",
        patched_depends.__file__,
    )
    assert spec and spec.loader
    unpatched = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(unpatched)

    sync_client = redis.Redis(host="127.0.0.1", port=6379, decode_responses=False)
    try:
        sync_client.ping()
    except redis.RedisError:
        sync_client.close()
        raise
    lua_sha = sync_client.script_load(FastAPILimiter.lua_script)
    for key in sync_client.scan_iter(match=f"{prefix}:*"):
        sync_client.delete(key)

    async_client = async_redis.Redis(host="127.0.0.1", port=6379, decode_responses=False)

    async def callback(_request, _response, _expire):
        raise HTTPException(status_code=429, detail="rate limited")

    monkeypatch.setattr(RateLimiter, "__call__", unpatched.RateLimiter.__call__)
    monkeypatch.setattr(FastAPILimiter, "redis", async_client)
    monkeypatch.setattr(FastAPILimiter, "lua_sha", lua_sha)
    monkeypatch.setattr(FastAPILimiter, "prefix", prefix)
    monkeypatch.setattr(FastAPILimiter, "identifier", default_identifier)
    monkeypatch.setattr(FastAPILimiter, "http_callback", callback)
    return sync_client, async_client


@pytest.fixture
def real_exchange_rate_limiter(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    resources = []

    def install(prefix: str):
        import redis

        try:
            resource = _install_real_exchange_rate_limiter(monkeypatch, prefix=prefix)
        except redis.RedisError as exc:
            pytest.skip(f"real Redis is unavailable: {exc}")
        resources.append((*resource, prefix))
        return resource

    try:
        yield install
    finally:
        for sync_client, async_client, prefix in resources:
            if control_client.portal is not None:
                control_client.portal.call(async_client.aclose)
            for key in sync_client.scan_iter(match=f"{prefix}:*"):
                sync_client.delete(key)
            sync_client.close()


def test_real_redis_exchange_limit_ignores_rotating_forwarded_for(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    real_exchange_rate_limiter,
) -> None:
    from app.api.v1.module_control.user_entitlement import controller
    from app.core.database import async_db_session

    prefix = f"entitlement-xff-{uuid4().hex}"
    real_exchange_rate_limiter(prefix)
    service_calls = 0

    async def forbidden_exchange(*_args, **_kwargs):
        nonlocal service_calls
        service_calls += 1
        raise AssertionError("invalid body must not call exchange service")

    monkeypatch.setattr(controller.ControlUserEntitlementTicketService, "exchange", forbidden_exchange)
    case = asyncio.run(_seed_ticket_case())
    responses = [
        control_client.post(
            "/control/user-entitlements/exchange",
            auth=(str(case["client_id"]), str(case["secret"])),
            content=b'{"code":"invalid"}',
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-For": f"198.51.100.{index}",
            },
        )
        for index in range(1, 12)
    ]
    assert [response.status_code for response in responses[:10]] == [400] * 10
    assert responses[10].status_code == 429
    assert service_calls == 0

    async def ticket_status() -> str:
        async with async_db_session() as db:
            ticket = (
                await db.execute(
                    select(ControlUserEntitlementTicketModel).where(
                        ControlUserEntitlementTicketModel.grant_id == case["grant_id"]
                    )
                )
            ).scalar_one()
            return ticket.status

    assert asyncio.run(ticket_status()) == "issued"


def test_real_redis_noncanonical_basic_uses_same_client_bucket(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    real_exchange_rate_limiter,
) -> None:
    from app.api.v1.module_control.user_entitlement import controller

    prefix = f"entitlement-basic-{uuid4().hex}"
    real_exchange_rate_limiter(prefix)
    service_calls = 0

    async def forbidden_exchange(*_args, **_kwargs):
        nonlocal service_calls
        service_calls += 1
        raise AssertionError("invalid body must not call exchange service")

    monkeypatch.setattr(controller.ControlUserEntitlementTicketService, "exchange", forbidden_exchange)
    case = asyncio.run(_seed_ticket_case())
    credentials = base64.b64encode(f'{case["client_id"]}:{case["secret"]}'.encode()).decode()
    noncanonical = f"{credentials[:4]}!{credentials[4:]}"
    responses = [
        control_client.post(
            "/control/user-entitlements/exchange",
            auth=(str(case["client_id"]), str(case["secret"])),
            content=b'{"code":"invalid"}',
            headers={"Content-Type": "application/json"},
        )
        for _index in range(10)
    ]
    noncanonical_response = control_client.post(
        "/control/user-entitlements/exchange",
        content=b'{"code":"invalid"}',
        headers={
            "Authorization": f"Basic {noncanonical}",
            "Content-Type": "application/json",
        },
    )
    assert [response.status_code for response in responses] == [400] * 10
    assert noncanonical_response.status_code == 429
    assert service_calls == 0


def test_real_redis_peer_limit_bounds_rotating_unknown_client_ids(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    real_exchange_rate_limiter,
) -> None:
    from app.api.v1.module_control.user_entitlement import controller
    from app.core.database import async_db_session
    from app.core.exceptions import CustomException

    prefix = f"entitlement-peer-{uuid4().hex}"
    sync_redis, _async_client = real_exchange_rate_limiter(prefix)
    service_calls = 0

    async def reject_unknown_client(*_args, **_kwargs):
        nonlocal service_calls
        service_calls += 1
        raise CustomException(msg="中控客户端认证失败", status_code=401)

    monkeypatch.setattr(controller.ControlUserEntitlementTicketService, "exchange", reject_unknown_client)
    case = asyncio.run(_seed_ticket_case())
    responses = [
        control_client.post(
            "/control/user-entitlements/exchange",
            auth=(f"unknown-client-{index}", "wrong-secret"),
            json={"code": case["code"]},
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        for index in range(120)
    ]
    keys_before_rejection = set(sync_redis.scan_iter(match=f"{prefix}:*"))
    rejected = control_client.post(
        "/control/user-entitlements/exchange",
        auth=("unknown-client-120", "wrong-secret"),
        json={"code": case["code"]},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    keys_after_rejection = set(sync_redis.scan_iter(match=f"{prefix}:*"))
    assert [response.status_code for response in responses] == [401] * 120
    assert rejected.status_code == 429
    assert service_calls == 120
    assert len(keys_before_rejection) == 121
    assert keys_after_rejection == keys_before_rejection

    async def ticket_status() -> str:
        async with async_db_session() as db:
            ticket = (
                await db.execute(
                    select(ControlUserEntitlementTicketModel).where(
                        ControlUserEntitlementTicketModel.grant_id == case["grant_id"]
                    )
                )
            ).scalar_one()
            return ticket.status

    assert asyncio.run(ticket_status()) == "issued"


def test_wrong_secret_and_application_do_not_leak_secret_or_code(
    control_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import exceptions

    case = asyncio.run(_seed_ticket_case())
    log_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        exceptions.logger,
        "error",
        lambda *args, **kwargs: log_calls.append((*args, kwargs)),
    )
    wrong_secret = "wrong-basic-secret-marker"
    wrong_secret_response = _exchange(control_client, case, secret=wrong_secret)
    wrong_application_response = _exchange(
        control_client,
        case,
        client_id=str(case["wrong_client_id"]),
        secret=str(case["wrong_secret"]),
    )
    response_and_logs = (
        wrong_secret_response.text
        + wrong_application_response.text
        + repr(log_calls)
    )
    assert wrong_secret not in response_and_logs
    assert str(case["wrong_secret"]) not in response_and_logs
    assert str(case["code"]) not in response_and_logs


def test_exchange_sets_skip_operation_log_before_reading_raw_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.module_control.user_entitlement import controller

    observed: dict[str, object] = {}

    async def fake_exchange(_db, **kwargs):
        observed.update(kwargs)
        return {
            "issuer": "http://control.example/api/v1",
            "event_id": "event-1",
            "sync_version": 1,
            "desired_state": "inactive",
            "application_code": "wms",
            "site_code": "default",
            "central_tenant_uuid": "tenant-uuid",
            "central_tenant_code": "tenant-code",
            "target_tenant_code": "target-code",
            "central_user_uuid": "user-uuid",
            "name": "普通用户",
            "mobile": None,
            "email": None,
            "avatar": None,
            "user_status": 0,
        }

    monkeypatch.setattr(controller.ControlUserEntitlementTicketService, "exchange", fake_exchange)
    body = json.dumps({"code": "x" * 64}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("control.example", 443),
            "path": "/api/v1/control/user-entitlements/exchange",
            "root_path": "/api/v1",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )
    response = asyncio.run(
        controller.exchange_user_entitlement(
            request=request,
            credentials=HTTPBasicCredentials(username="client", password="secret"),
            db=None,
        )
    )
    assert response.status_code == 200
    assert request.state.skip_operation_log is True
    assert observed["code"] == "x" * 64


@pytest.mark.parametrize("entity", ["application", "opening", "tenant", "site", "user", "membership"])
@pytest.mark.parametrize("desired_state", ["active", "inactive"])
def test_revocation_ticket_survives_target_deactivation_but_activation_does_not(control_client, entity, desired_state):
    from sqlalchemy import delete

    from app.core.database import async_db_session

    case = asyncio.run(_seed_ticket_case(desired_state=desired_state))
    async def deactivate():
        async with async_db_session() as db:
            if entity == "membership":
                await db.execute(delete(TenantUserModel).where(TenantUserModel.user_id == case["user_id"], TenantUserModel.tenant_id == case["tenant_id"]))
            else:
                model = {"application": ControlApplicationModel, "opening": ControlTenantApplicationModel, "tenant": TenantModel, "site": SiteModel, "user": UserModel}[entity]
                row = await db.get(model, case[f"{entity}_id"])
                row.status = 1
                row.is_deleted = True
            await db.commit()
    asyncio.run(deactivate())
    response = _exchange(control_client, case)
    assert response.status_code == (200 if desired_state == "inactive" else 400), response.text
    if desired_state == "inactive":
        assert response.json()["data"]["desired_state"] == "inactive"
        assert _exchange(control_client, case).status_code == 400
