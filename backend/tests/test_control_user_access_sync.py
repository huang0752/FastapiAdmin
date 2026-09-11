from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.module_platform.federated_tenant.model import FederatedTenantModel
from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
from app.api.v1.module_platform.site.model import SiteDomainModel, SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.auth.control_sso_schema import (
    ControlIdentityClaims,
    ControlTenantProvisionClaims,
)
from app.api.v1.module_system.auth.control_sso_service import ControlSSOClientService
from app.api.v1.module_system.auth.control_tenant_provisioning_service import (
    ControlTenantProvisioningService,
)
from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.auth.session_registry import (
    FederatedSessionRecovery,
    UserSessionRegistry,
)
from app.api.v1.module_system.federated_access.model import (
    FederatedAccessEntitlementModel,
    FederatedAccessEventModel,
)
from app.api.v1.module_system.federated_access.schema import (
    ControlUserAccessClaims,
    ControlUserAccessSyncOut,
)
from app.api.v1.module_system.federated_access.service import (
    ControlUserAccessSyncService,
)
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.common.enums import EnvironmentEnum, RedisInitKeyConfig
from app.config.setting import Settings, settings
from app.core.assembly import reset_assembly_cache
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.security import decode_access_token
from app.plugin.module_ai.chat.service import ChatService
from app.plugin.module_ai.chat.ws import WS_AI

ISSUER = "https://control.example/api/v1"
PRODUCT_CASES = {
    "alpha": ("alpha", "module_alpha", "alpha"),
    "beta": (
        "beta",
        "module_beta",
        "beta",
    ),
    "gamma": (
        "gamma",
        "module_gamma",
        "gamma",
    ),
}


def _selected_assembly() -> str:
    candidate = os.environ.get("APP_ASSEMBLY", "")
    return candidate if candidate in PRODUCT_CASES else "alpha"


@contextmanager
def _use_selected_assembly():
    assembly = _selected_assembly()
    original_assembly = settings.APP_ASSEMBLY
    original_file = settings.APP_ASSEMBLY_FILE
    settings.APP_ASSEMBLY = assembly
    settings.APP_ASSEMBLY_FILE = f"tests/fixtures/assemblies/{assembly}.toml"
    reset_assembly_cache()
    try:
        yield PRODUCT_CASES[assembly][0]
    finally:
        settings.APP_ASSEMBLY = original_assembly
        settings.APP_ASSEMBLY_FILE = original_file
        reset_assembly_cache()


@dataclass(frozen=True)
class SyncFixture:
    site_code: str
    tenant_id: int
    tenant_code: str
    central_tenant_uuid: str
    central_tenant_code: str
    business_menu_id: int | None


async def _seed_sync_fixture(*, with_business_menu: bool = True) -> SyncFixture:
    product_code, permission_prefix, route_group = PRODUCT_CASES[_selected_assembly()]
    suffix = uuid4().hex[:10]
    central_tenant_uuid = str(uuid4())
    central_tenant_code = f"central{suffix}"
    tenant_code = f"sync{suffix}"
    async with async_db_session() as db:
        site = await db.get(SiteModel, 1)
        assert site is not None
        test_domain = (await db.execute(select(SiteDomainModel).where(SiteDomainModel.host == "testserver"))).scalar_one_or_none()
        if test_domain is None:
            db.add(
                SiteDomainModel(
                    site_id=1,
                    host="testserver",
                    is_primary=False,
                )
            )
            await db.flush()
        package = PackageModel(
            site_id=1,
            name=f"授权同步套餐{suffix}",
            code=f"syncpkg{suffix}",
            status=0,
        )
        db.add(package)
        await db.flush()
        tenant = TenantModel(
            site_id=1,
            package_id=package.id,
            name=f"授权同步租户{suffix}",
            code=tenant_code,
            status=0,
        )
        db.add(tenant)
        await db.flush()

        menu_id = None
        if with_business_menu:
            parent = MenuModel(
                name=f"授权业务目录{suffix}",
                title=f"授权业务目录{suffix}",
                type=1,
                order=1,
                route_name=f"AccessCatalog{suffix}",
                route_path=f"/{route_group}/access-{suffix}",
                redirect=f"/{route_group}/access-{suffix}/page",
                client="pc",
                scope="tenant",
                status=0,
            )
            business = MenuModel(
                name=f"授权业务页{suffix}",
                title=f"授权业务页{suffix}",
                type=2,
                order=1,
                permission=f"{permission_prefix}:access:query",
                route_name=f"AccessPage{suffix}",
                route_path="page",
                component_path=f"{route_group}/access/index",
                parent=parent,
                client="pc",
                scope="tenant",
                status=0,
            )
            db.add_all([parent, business])
            await db.flush()
            db.add_all(
                [
                    PackageMenuModel(package_id=package.id, menu_id=parent.id),
                    PackageMenuModel(package_id=package.id, menu_id=business.id),
                ]
            )
            menu_id = business.id

        db.add(
            FederatedTenantModel(
                site_id=1,
                issuer=ISSUER,
                central_tenant_uuid=central_tenant_uuid,
                central_tenant_code=central_tenant_code,
                local_tenant_id=tenant.id,
                provision_request_uuid=str(uuid4()),
                target_package_code=package.code,
                owner_central_user_uuid=str(uuid4()),
            )
        )
        await db.commit()
        return SyncFixture(
            site_code=site.code,
            tenant_id=tenant.id,
            tenant_code=tenant.code,
            central_tenant_uuid=central_tenant_uuid,
            central_tenant_code=central_tenant_code,
            business_menu_id=menu_id,
        )


def _claims(
    fixture: SyncFixture,
    *,
    subject: str | None = None,
    event_id: str | None = None,
    sync_version: int = 1,
    desired_state: str = "active",
    application_code: str | None = None,
    site_code: str | None = None,
    issuer: str = ISSUER,
    central_tenant_uuid: str | None = None,
    central_tenant_code: str | None = None,
    target_tenant_code: str | None = None,
    user_status: int = 0,
    name: str = "中控普通用户",
) -> ControlUserAccessClaims:
    product_code = PRODUCT_CASES[_selected_assembly()][0]
    return ControlUserAccessClaims(
        issuer=issuer,
        event_id=event_id or str(uuid4()),
        sync_version=sync_version,
        desired_state=desired_state,
        application_code=application_code or product_code,
        site_code=site_code or fixture.site_code,
        central_tenant_uuid=central_tenant_uuid or fixture.central_tenant_uuid,
        central_tenant_code=central_tenant_code or fixture.central_tenant_code,
        target_tenant_code=target_tenant_code or fixture.tenant_code,
        central_user_uuid=subject or str(uuid4()),
        name=name,
        mobile="13800009999",
        email="private@example.com",
        avatar="https://example.com/private-avatar.png",
        user_status=user_status,
    )


def _enable_sync(monkeypatch, claims: ControlUserAccessClaims) -> None:
    async def exchange(_cls, code: str, site_code: str):
        assert code.startswith("ticket-")
        assert site_code
        return claims

    monkeypatch.setattr(settings, "CONTROL_USER_ACCESS_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", ISSUER)
    monkeypatch.setattr(
        ControlUserAccessSyncService,
        "_exchange_code",
        classmethod(exchange),
    )


def _enable_control_login(
    monkeypatch: pytest.MonkeyPatch,
    fixture: SyncFixture,
    subject: str,
) -> None:
    async def exchange(_cls, _code: str, site_code: str):
        assert site_code == fixture.site_code
        return ControlIdentityClaims(
            issuer=ISSUER,
            central_user_uuid=subject,
            name="中控普通用户",
            mobile="13800009999",
            email="private@example.com",
            avatar=None,
            status=0,
            site_code=fixture.site_code,
            central_tenant_code=fixture.central_tenant_code,
            central_is_superuser=False,
            central_tenant_role="member",
            target_tenant_code=fixture.tenant_code,
        )

    monkeypatch.setattr(settings, "CONTROL_SSO_ENABLED", True)
    monkeypatch.setattr(
        ControlSSOClientService,
        "_exchange_code",
        classmethod(exchange),
    )


def _post_sync(test_client, claims, monkeypatch, *, code: str | None = None):
    _enable_sync(monkeypatch, claims)
    return test_client.post(
        "/system/auth/control/access/sync",
        json={"code": code or f"ticket-{uuid4().hex}"},
    )


def _login_headers_after_site_seed(test_client) -> dict[str, str]:
    response = test_client.post(
        "/system/auth/login",
        data={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def _entitlement_snapshot(subject: str, tenant_id: int) -> dict:
    async with async_db_session() as db:
        entitlement = (
            await db.execute(
                select(FederatedAccessEntitlementModel).where(
                    FederatedAccessEntitlementModel.issuer == ISSUER,
                    FederatedAccessEntitlementModel.central_user_uuid == subject,
                    FederatedAccessEntitlementModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        membership_count = 0
        role_codes: list[str] = []
        if entitlement.local_user_id is not None:
            membership_count = (
                await db.execute(
                    select(func.count())
                    .select_from(TenantUserModel)
                    .where(
                        TenantUserModel.user_id == entitlement.local_user_id,
                        TenantUserModel.tenant_id == tenant_id,
                    )
                )
            ).scalar_one()
            role_codes = list(
                (
                    await db.execute(
                        select(RoleModel.code)
                        .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                        .where(
                            UserRolesModel.user_id == entitlement.local_user_id,
                            RoleModel.tenant_id == tenant_id,
                        )
                        .order_by(RoleModel.code)
                    )
                )
                .scalars()
                .all()
            )
        return {
            "id": entitlement.id,
            "site_id": entitlement.site_id,
            "status": entitlement.status,
            "applied_version": entitlement.applied_version,
            "last_event_id": entitlement.last_event_id,
            "local_user_id": entitlement.local_user_id,
            "session_cleanup_pending": entitlement.session_cleanup_pending,
            "membership_count": membership_count,
            "role_codes": role_codes,
        }


async def _cleanup_commit_snapshot(subject: str, tenant_id: int) -> dict:
    async with async_db_session() as db:
        entitlement = (
            await db.execute(
                select(FederatedAccessEntitlementModel).where(
                    FederatedAccessEntitlementModel.issuer == ISSUER,
                    FederatedAccessEntitlementModel.central_user_uuid == subject,
                    FederatedAccessEntitlementModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        receipt = (await db.execute(select(FederatedAccessEventModel).where(FederatedAccessEventModel.event_id == entitlement.last_event_id))).scalar_one()
        return {
            "status": entitlement.status,
            "pending": entitlement.session_cleanup_pending,
            "receipt_pending": receipt.result_json["session_cleanup_pending"],
        }


async def _pending_cleanup_count() -> int:
    async with async_db_session() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(FederatedAccessEntitlementModel)
                    .where(
                        FederatedAccessEntitlementModel.status == "inactive",
                        FederatedAccessEntitlementModel.session_cleanup_pending.is_(
                            True
                        ),
                    )
                )
            ).scalar_one()
        )


async def _set_entitlement_status_without_cleanup(
    subject: str,
    tenant_id: int,
    status: str,
) -> int:
    async with async_db_session() as db:
        entitlement = (
            await db.execute(
                select(FederatedAccessEntitlementModel).where(
                    FederatedAccessEntitlementModel.issuer == ISSUER,
                    FederatedAccessEntitlementModel.central_user_uuid == subject,
                    FederatedAccessEntitlementModel.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        entitlement.status = status
        local_user_id = int(entitlement.local_user_id)
        await db.commit()
        return local_user_id


def test_control_user_access_sync_returns_404_when_disabled(
    test_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "CONTROL_USER_ACCESS_SYNC_ENABLED", False, raising=False)

    response = test_client.post(
        "/system/auth/control/access/sync",
        json={"code": f"ticket-{uuid4().hex}"},
    )

    assert response.status_code == 404
    assert response.json()["msg"] == "中控用户访问资格同步未启用"


def test_control_user_access_sync_body_only_accepts_code(test_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CONTROL_USER_ACCESS_SYNC_ENABLED", True, raising=False)

    response = test_client.post(
        "/system/auth/control/access/sync",
        json={"code": f"ticket-{uuid4().hex}", "event_id": str(uuid4())},
    )

    assert response.status_code == 422


def test_invalid_sync_bodies_are_redacted_from_response_and_operation_log(
    test_client,
    monkeypatch,
) -> None:
    from app.core import router_class

    asyncio.run(_seed_sync_fixture(with_business_menu=False))
    auth_headers = _login_headers_after_site_seed(test_client)
    ticket = f"ticket-{uuid4().hex}"
    captured: list[dict] = []

    async def capture(log_data: dict) -> None:
        captured.append(log_data)

    monkeypatch.setattr(settings, "CONTROL_USER_ACCESS_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "OPERATION_LOG_RECORD", True)
    monkeypatch.setattr(router_class, "_write_operation_log_async", capture)
    requests = [
        {"json": {"code": ticket, "unexpected": "rejected"}},
        {"json": {"unexpected": ticket}},
        {"json": {"code": ticket[:10]}},
        {
            "content": f'{{"code":"{ticket}"',
            "headers": {"content-type": "application/json"},
        },
    ]

    for kwargs in requests:
        headers = {**auth_headers, **kwargs.pop("headers", {})}
        response = test_client.post(
            "/system/auth/control/access/sync",
            headers=headers,
            **kwargs,
        )
        assert response.status_code == 422, response.text
        assert ticket not in response.text
        assert "unexpected" not in response.text

    assert all(ticket not in entry.get("request_payload", "") for entry in captured)


def test_disabled_sync_returns_stable_404_before_reading_any_body(
    test_client,
    monkeypatch,
) -> None:
    ticket = f"ticket-{uuid4().hex}"
    monkeypatch.setattr(settings, "CONTROL_USER_ACCESS_SYNC_ENABLED", False)
    requests = [
        {"json": {"code": ticket, "unexpected": "rejected"}},
        {"json": {"unexpected": ticket}},
        {"json": {"code": ticket[:10]}},
        {
            "content": f'{{"code":"{ticket}"',
            "headers": {"content-type": "application/json"},
        },
    ]

    for kwargs in requests:
        response = test_client.post(
            "/system/auth/control/access/sync",
            **kwargs,
        )
        assert response.status_code == 404
        assert response.json()["msg"] == "中控用户访问资格同步未启用"
        assert ticket not in response.text


def test_sync_openapi_keeps_safe_manual_code_schema(test_client) -> None:
    operation = test_client.get("/openapi.json").json()["paths"]["/system/auth/control/access/sync"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert schema["type"] == "object"
    assert schema["required"] == ["code"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["code"] == {
        "type": "string",
        "minLength": 20,
        "maxLength": 512,
    }


def test_exchange_code_uses_site_basic_auth_and_only_sends_ticket(monkeypatch) -> None:
    fixture = asyncio.run(_seed_sync_fixture())
    claims = _claims(fixture)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"data": claims.model_dump()})

    monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", f"{ISSUER}/")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_ID", "target-client")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_SECRET", "target-secret")
    monkeypatch.setattr(settings, "CONTROL_SSO_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(
        ControlUserAccessSyncService,
        "transport",
        httpx.MockTransport(handler),
    )

    result = asyncio.run(ControlUserAccessSyncService._exchange_code("ticket-" + "x" * 32, "default"))

    assert result == claims
    assert captured == {
        "url": f"{ISSUER}/control/user-entitlements/exchange",
        "authorization": "Basic " + base64.b64encode(b"target-client:target-secret").decode(),
        "json": {"code": "ticket-" + "x" * 32},
    }


def test_settings_rejects_non_https_control_issuer_in_production() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(
            ENVIRONMENT=EnvironmentEnum.PROD,
            SECRET_KEY="production-secret-key",
            ALLOW_CREDENTIALS=False,
            CONTROL_SSO_ENABLED=True,
            CONTROL_SSO_ISSUER="http://control.example/api/v1",
            CONTROL_SSO_CLIENT_ID="target-client",
            CONTROL_SSO_CLIENT_SECRET="target-secret",
        )


def test_settings_only_allows_localhost_http_control_issuer_in_development() -> None:
    with pytest.raises(ValueError, match="localhost.*HTTP|HTTP.*localhost"):
        Settings(
            ENVIRONMENT=EnvironmentEnum.DEV,
            CONTROL_SSO_ENABLED=True,
            CONTROL_SSO_ISSUER="http://control.example/api/v1",
            CONTROL_SSO_CLIENT_ID="target-client",
            CONTROL_SSO_CLIENT_SECRET="target-secret",
        )

    configured = Settings(
        ENVIRONMENT=EnvironmentEnum.DEV,
        CONTROL_SSO_ENABLED=True,
        CONTROL_SSO_ISSUER="http://127.0.0.1:8100/api/v1",
        CONTROL_SSO_CLIENT_ID="target-client",
        CONTROL_SSO_CLIENT_SECRET="target-secret",
    )
    assert configured.CONTROL_SSO_ISSUER == "http://127.0.0.1:8100/api/v1"


def test_site_client_rejects_blank_secret_without_echoing_it() -> None:
    blank_secret = " " * 17
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT=EnvironmentEnum.DEV,
            CONTROL_SSO_ENABLED=True,
            CONTROL_SSO_ISSUER="https://control.example/api/v1",
            CONTROL_SSO_SITE_CLIENTS={
                "default": {
                    "client_id": "target-client",
                    "client_secret": blank_secret,
                }
            },
        )

    assert blank_secret not in str(exc_info.value)


def test_exchange_rejects_insecure_runtime_issuer_and_blank_secret_before_http(
    monkeypatch,
) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.PROD)
    monkeypatch.setattr(
        settings,
        "CONTROL_SSO_ISSUER",
        "http://control.example/api/v1",
    )
    monkeypatch.setattr(
        ControlUserAccessSyncService,
        "transport",
        httpx.MockTransport(handler),
    )
    with pytest.raises(CustomException, match="HTTPS"):
        asyncio.run(
            ControlUserAccessSyncService._exchange_code(
                "ticket-" + "x" * 32,
                "default",
            )
        )
    assert called is False

    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.DEV)
    monkeypatch.setattr(
        settings,
        "CONTROL_SSO_ISSUER",
        "http://127.0.0.1:8100/api/v1",
    )
    monkeypatch.setattr(
        Settings,
        "control_client_for_site",
        lambda _self, _site_code: SimpleNamespace(
            client_id="target-client",
            client_secret=SecretStr("   "),
        ),
    )
    with pytest.raises(CustomException) as exc_info:
        asyncio.run(
            ControlUserAccessSyncService._exchange_code(
                "ticket-" + "x" * 32,
                "default",
            )
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.data is None
    assert called is False


def test_control_user_access_sync_creates_active_entitlement_and_default_role(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        claims = _claims(fixture)

        response = _post_sync(test_client, claims, monkeypatch)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["disposition"] == "applied"
    assert data["status"] == "active"
    assert data["applied_version"] == 1
    assert data["role_codes"] == ["USER"]
    assert data["effective_menu_count"] > 0
    assert set(data) == {
        "disposition",
        "status",
        "applied_version",
        "local_user_id",
        "role_codes",
        "effective_menu_count",
        "session_cleanup_pending",
    }
    assert claims.mobile not in response.text
    assert claims.email not in response.text
    assert claims.name not in response.text
    assert "ticket-" not in response.text
    snapshot = asyncio.run(_entitlement_snapshot(claims.central_user_uuid, fixture.tenant_id))
    assert snapshot["status"] == "active"
    assert snapshot["membership_count"] == 1
    assert snapshot["role_codes"] == ["USER"]


def test_same_event_same_fingerprint_replays_saved_original_result(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        claims = _claims(fixture)
        first = _post_sync(test_client, claims, monkeypatch)
        assert first.status_code == 200, first.text
        original = first.json()["data"]

        async def mutate_after_first() -> None:
            async with async_db_session() as db:
                entitlement = (await db.execute(select(FederatedAccessEntitlementModel).where(FederatedAccessEntitlementModel.central_user_uuid == claims.central_user_uuid))).scalar_one()
                entitlement.status = "inactive"
                await db.commit()

        asyncio.run(mutate_after_first())
        replay = _post_sync(test_client, claims, monkeypatch)

    assert replay.status_code == 200, replay.text
    replayed = replay.json()["data"]
    assert replayed == {**original, "disposition": "replayed"}


def test_same_event_normalizes_equivalent_site_code_before_fingerprinting(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        claims = _claims(fixture)
        first = _post_sync(test_client, claims, monkeypatch)
        assert first.status_code == 200, first.text

        equivalent = claims.model_copy(deep=True)
        equivalent.site_code = f"  {fixture.site_code.upper()}  "
        replay = _post_sync(test_client, equivalent, monkeypatch)

    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == {
        **first.json()["data"],
        "disposition": "replayed",
    }


def test_same_event_different_fingerprint_returns_409(test_client, monkeypatch) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        event_id = str(uuid4())
        first_claims = _claims(fixture, event_id=event_id)
        assert _post_sync(test_client, first_claims, monkeypatch).status_code == 200
        changed = _claims(
            fixture,
            subject=first_claims.central_user_uuid,
            event_id=event_id,
            name="篡改后姓名",
        )
        response = _post_sync(test_client, changed, monkeypatch)

    assert response.status_code == 409
    assert "事件" in response.json()["msg"] and "不一致" in response.json()["msg"]


def test_lower_version_is_saved_as_superseded_without_changing_state(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        high = _claims(fixture, subject=subject, sync_version=3)
        assert _post_sync(test_client, high, monkeypatch).status_code == 200
        old = _claims(
            fixture,
            subject=subject,
            desired_state="inactive",
            sync_version=2,
        )
        response = _post_sync(test_client, old, monkeypatch)
        replay = _post_sync(test_client, old, monkeypatch)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["disposition"] == "superseded"
    assert response.json()["data"]["status"] == "active"
    assert response.json()["data"]["applied_version"] == 3
    assert replay.json()["data"] == {
        **response.json()["data"],
        "disposition": "replayed",
    }
    snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))
    assert snapshot["status"] == "active"
    assert snapshot["applied_version"] == 3


def test_same_version_different_event_returns_409(test_client, monkeypatch) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        first = _claims(fixture, subject=subject, sync_version=2)
        assert _post_sync(test_client, first, monkeypatch).status_code == 200
        conflict = _claims(
            fixture,
            subject=subject,
            sync_version=2,
            desired_state="inactive",
        )
        response = _post_sync(test_client, conflict, monkeypatch)

    assert response.status_code == 409
    assert "版本" in response.json()["msg"]


def test_higher_inactive_version_creates_nullable_tombstone_and_preserves_existing_links(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        tombstone_subject = str(uuid4())
        tombstone = _claims(
            fixture,
            subject=tombstone_subject,
            desired_state="inactive",
            sync_version=2,
            user_status=1,
        )
        tombstone_response = _post_sync(test_client, tombstone, monkeypatch)
        assert tombstone_response.status_code == 200, tombstone_response.text

        linked_subject = str(uuid4())
        active = _claims(fixture, subject=linked_subject)
        assert _post_sync(test_client, active, monkeypatch).status_code == 200
        inactive = _claims(
            fixture,
            subject=linked_subject,
            desired_state="inactive",
            sync_version=2,
            user_status=1,
        )
        linked_response = _post_sync(test_client, inactive, monkeypatch)

    assert tombstone_response.json()["data"]["local_user_id"] is None
    tombstone_snapshot = asyncio.run(_entitlement_snapshot(tombstone_subject, fixture.tenant_id))
    assert tombstone_snapshot["local_user_id"] is None
    assert tombstone_snapshot["status"] == "inactive"
    assert linked_response.status_code == 200, linked_response.text
    linked_snapshot = asyncio.run(_entitlement_snapshot(linked_subject, fixture.tenant_id))
    assert linked_snapshot["status"] == "inactive"
    assert linked_snapshot["membership_count"] == 1
    assert linked_snapshot["role_codes"] == ["USER"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"issuer": "https://evil.example/api/v1"}, "签发方"),
        ({"site_code": "other"}, "站点"),
        ({"application_code": "other"}, "应用"),
        ({"central_tenant_uuid": str(uuid4())}, "租户映射"),
        ({"central_tenant_code": "drifted"}, "租户映射"),
        ({"target_tenant_code": "missing"}, "租户映射"),
        ({"user_status": 1}, "用户状态"),
    ],
)
def test_active_claim_boundaries_are_strict(
    test_client,
    monkeypatch,
    override,
    message,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        claims = _claims(fixture, **override)
        response = _post_sync(test_client, claims, monkeypatch)

    assert response.status_code in {401, 403, 409}
    assert message in response.json()["msg"]


def test_active_sync_keeps_manual_role_without_binding_user_role(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        claims = _claims(fixture)

        async def seed_manual_role() -> None:
            async with async_db_session() as db:
                user = UserModel(
                    username=f"manual_{uuid4().hex[:16]}",
                    password="unused",
                    name="人工角色用户",
                    tenant_id=fixture.tenant_id,
                    auth_source="federated",
                    password_login_enabled=False,
                    status=0,
                )
                role = RoleModel(
                    name="业务专员",
                    code=f"MANUAL_{uuid4().hex[:8]}",
                    tenant_id=fixture.tenant_id,
                    status=0,
                    is_system=False,
                    data_scope=1,
                )
                db.add_all([user, role])
                await db.flush()
                db.add_all(
                    [
                        FederatedIdentityModel(
                            site_id=1,
                            issuer=ISSUER,
                            central_user_uuid=claims.central_user_uuid,
                            local_user_id=user.id,
                        ),
                        TenantUserModel(
                            user_id=user.id,
                            tenant_id=fixture.tenant_id,
                            role="member",
                            is_default=1,
                        ),
                        UserRolesModel(user_id=user.id, role_id=role.id),
                        RoleMenusModel(
                            role_id=role.id,
                            menu_id=fixture.business_menu_id,
                        ),
                    ]
                )
                await db.commit()

        asyncio.run(seed_manual_role())
        response = _post_sync(test_client, claims, monkeypatch)

    assert response.status_code == 200, response.text
    role_codes = response.json()["data"]["role_codes"]
    assert role_codes[0].startswith("MANUAL_")
    assert "USER" not in role_codes


def test_active_sync_without_user_business_menu_rolls_back_everything(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture(with_business_menu=False))
        claims = _claims(fixture)
        response = _post_sync(test_client, claims, monkeypatch)

    assert response.status_code == 409
    assert "没有有效业务菜单" in response.json()["msg"]

    async def counts() -> tuple[int, int, int]:
        async with async_db_session() as db:
            entitlement_count = (
                await db.execute(select(func.count()).select_from(FederatedAccessEntitlementModel).where(FederatedAccessEntitlementModel.central_user_uuid == claims.central_user_uuid))
            ).scalar_one()
            identity_count = (await db.execute(select(func.count()).select_from(FederatedIdentityModel).where(FederatedIdentityModel.central_user_uuid == claims.central_user_uuid))).scalar_one()
            event_count = (await db.execute(select(func.count()).select_from(FederatedAccessEventModel).where(FederatedAccessEventModel.event_id == claims.event_id))).scalar_one()
            return entitlement_count, identity_count, event_count

    assert asyncio.run(counts()) == (0, 0, 0)


def test_sync_ticket_is_never_written_to_operation_log(
    test_client,
    monkeypatch,
) -> None:
    from app.core import router_class

    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        auth_headers = _login_headers_after_site_seed(test_client)
        claims = _claims(fixture, desired_state="inactive", user_status=1)
        ticket = f"ticket-{uuid4().hex}"
        captured: list[dict] = []

        async def capture(log_data: dict) -> None:
            captured.append(log_data)

        _enable_sync(monkeypatch, claims)
        monkeypatch.setattr(settings, "OPERATION_LOG_RECORD", True)
        monkeypatch.setattr(router_class, "_write_operation_log_async", capture)
        response = test_client.post(
            "/system/auth/control/access/sync",
            headers=auth_headers,
            json={"code": ticket},
        )

    assert response.status_code == 200, response.text
    assert all(ticket not in entry.get("request_payload", "") for entry in captured)


def test_access_sync_setting_defaults_false_and_requires_sso_when_enabled() -> None:
    assert Settings().CONTROL_USER_ACCESS_SYNC_ENABLED is False
    assert Settings().CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED is False
    with pytest.raises(ValueError, match="访问资格同步.*SSO"):
        Settings(
            CONTROL_USER_ACCESS_SYNC_ENABLED=True,
            CONTROL_SSO_ENABLED=False,
        )
    with pytest.raises(ValueError, match="访问资格强制校验.*同步"):
        Settings(
            CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED=True,
            CONTROL_USER_ACCESS_SYNC_ENABLED=False,
            CONTROL_SSO_ENABLED=True,
            CONTROL_SSO_ISSUER=ISSUER,
            CONTROL_SSO_CLIENT_ID="client",
            CONTROL_SSO_CLIENT_SECRET="secret",
        )


def test_access_sync_flag_does_not_gate_existing_control_sso(
    test_client,
    monkeypatch,
) -> None:
    async def exchange_and_login(_cls, _request, _db, _redis, code: str):
        assert code.startswith("ticket-")
        return {
            "access_token": "existing-sso-access-token",
            "refresh_token": "existing-sso-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(settings, "CONTROL_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "CONTROL_USER_ACCESS_SYNC_ENABLED", False)
    monkeypatch.setattr(
        ControlSSOClientService,
        "exchange_and_login",
        classmethod(exchange_and_login),
    )

    response = test_client.post(
        "/system/auth/control/exchange",
        json={"code": f"ticket-{uuid4().hex}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["access_token"] == "existing-sso-access-token"


def test_enforcement_rejects_control_sso_before_signing_without_active_entitlement(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture(with_business_menu=False))
        subject = str(uuid4())
        _enable_control_login(monkeypatch, fixture, subject)
        monkeypatch.setattr(
            settings,
            "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
            True,
            raising=False,
        )

        response = test_client.post(
            "/system/auth/control/exchange",
            json={"code": f"ticket-{uuid4().hex}"},
        )

    assert response.status_code == 401
    assert response.json()["msg"] == "中控已撤销该产品访问权限"


def test_inactive_sync_revokes_existing_session_without_removing_membership_or_roles(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        active = _claims(fixture, subject=subject, sync_version=1)
        active_response = _post_sync(test_client, active, monkeypatch)
        assert active_response.status_code == 200, active_response.text
        before_inactive = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))

        _enable_control_login(monkeypatch, fixture, subject)
        monkeypatch.setattr(
            settings,
            "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
            True,
            raising=False,
        )
        login = test_client.post(
            "/system/auth/control/exchange",
            json={"code": f"ticket-{uuid4().hex}"},
        )
        assert login.status_code == 200, login.text
        access_token = login.json()["data"]["access_token"]

        inactive = _claims(
            fixture,
            subject=subject,
            sync_version=2,
            desired_state="inactive",
            user_status=1,
        )
        inactive_response = _post_sync(test_client, inactive, monkeypatch)
        assert inactive_response.status_code == 200, inactive_response.text
        current = test_client.get(
            "/system/user/current/info",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))

    assert current.status_code == 401
    assert inactive_response.json()["data"]["session_cleanup_pending"] is False
    assert snapshot["status"] == "inactive"
    assert snapshot["session_cleanup_pending"] is False
    assert snapshot["membership_count"] == 1
    assert snapshot["role_codes"] == before_inactive["role_codes"]


def test_inactive_sync_commits_state_and_marks_pending_when_redis_cleanup_fails(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        active = _claims(fixture, subject=subject, sync_version=1)
        active_response = _post_sync(test_client, active, monkeypatch)
        assert active_response.status_code == 200, active_response.text

        observed_at_cleanup: dict = {}

        async def fail_cleanup(*_args, **_kwargs):
            observed_at_cleanup.update(await _cleanup_commit_snapshot(subject, fixture.tenant_id))
            raise ConnectionError("redis unavailable")

        monkeypatch.setattr(
            UserSessionRegistry,
            "revoke_user",
            classmethod(fail_cleanup),
        )
        inactive = _claims(
            fixture,
            subject=subject,
            sync_version=2,
            desired_state="inactive",
            user_status=1,
        )
        response = _post_sync(test_client, inactive, monkeypatch)
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["session_cleanup_pending"] is True
    assert snapshot["status"] == "inactive"
    assert snapshot["session_cleanup_pending"] is True
    assert observed_at_cleanup == {
        "status": "inactive",
        "pending": True,
        "receipt_pending": True,
    }


def test_replayed_inactive_sync_clears_pending_after_complete_cleanup(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        active = _claims(fixture, subject=subject, sync_version=1)
        assert _post_sync(test_client, active, monkeypatch).status_code == 200
        original_revoke = UserSessionRegistry.revoke_user.__func__

        async def fail_cleanup(*_args, **_kwargs):
            raise ConnectionError("redis unavailable")

        monkeypatch.setattr(
            UserSessionRegistry,
            "revoke_user",
            classmethod(fail_cleanup),
        )
        inactive = _claims(
            fixture,
            subject=subject,
            sync_version=2,
            desired_state="inactive",
            user_status=1,
        )
        failed = _post_sync(test_client, inactive, monkeypatch)
        assert failed.status_code == 200, failed.text
        assert failed.json()["data"]["session_cleanup_pending"] is True

        monkeypatch.setattr(
            UserSessionRegistry,
            "revoke_user",
            classmethod(original_revoke),
        )
        replay = _post_sync(test_client, inactive, monkeypatch)
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))
        reactivated = _claims(fixture, subject=subject, sync_version=3)
        reactivated_response = _post_sync(test_client, reactivated, monkeypatch)
        reactivated_snapshot = asyncio.run(_cleanup_commit_snapshot(subject, fixture.tenant_id))

    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["disposition"] == "replayed"
    assert replay.json()["data"]["session_cleanup_pending"] is False
    assert snapshot["session_cleanup_pending"] is False
    assert reactivated_response.status_code == 200, reactivated_response.text
    assert reactivated_response.json()["data"]["session_cleanup_pending"] is False
    assert reactivated_snapshot["pending"] is False
    assert reactivated_snapshot["receipt_pending"] is False


def test_inactive_entitlement_blocks_current_ws_shared_auth_refresh_select_and_tenants_even_before_cleanup(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        active = _claims(fixture, subject=subject, sync_version=1)
        assert _post_sync(test_client, active, monkeypatch).status_code == 200
        _enable_control_login(monkeypatch, fixture, subject)
        monkeypatch.setattr(
            settings,
            "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
            True,
            raising=False,
        )
        login = test_client.post(
            "/system/auth/control/exchange",
            json={"code": f"ticket-{uuid4().hex}"},
        )
        assert login.status_code == 200, login.text
        token_data = login.json()["data"]
        asyncio.run(
            _set_entitlement_status_without_cleanup(
                subject,
                fixture.tenant_id,
                "inactive",
            )
        )
        headers = {"Authorization": f"Bearer {token_data['access_token']}"}

        current = test_client.get("/system/user/current/info", headers=headers)
        tenants = test_client.get("/system/auth/tenants", headers=headers)
        selected = test_client.post(
            "/system/auth/select-tenant",
            headers=headers,
            json={"tenant_id": fixture.tenant_id},
        )
        refreshed = test_client.post(
            "/system/auth/token/refresh",
            json={"refresh_token": token_data["refresh_token"]},
        )

    assert current.status_code == 401
    assert current.json()["msg"] == "中控已撤销该产品访问权限"
    # HTTP current and WebSocket both call dependencies._authenticate.
    assert tenants.status_code == 401
    assert selected.status_code == 401
    assert refreshed.status_code == 401


def test_websocket_reauthenticates_before_each_message_after_redis_revocation(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        active = _claims(fixture, subject=subject, sync_version=1)
        assert _post_sync(test_client, active, monkeypatch).status_code == 200
        _enable_control_login(monkeypatch, fixture, subject)
        monkeypatch.setattr(
            settings,
            "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
            True,
            raising=False,
        )
        login = test_client.post(
            "/system/auth/control/exchange",
            json={"code": f"ticket-{uuid4().hex}"},
        )
        assert login.status_code == 200, login.text
        token_data = login.json()["data"]
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))

        async def allow_dynamic_plugin(*_args, **_kwargs) -> None:
            return None

        monkeypatch.setattr(
            "app.plugin.module_ai.chat.ws.validate_dynamic_plugin_access_for_path",
            allow_dynamic_plugin,
        )
        ws_app = FastAPI()
        ws_app.state.redis = test_client.app.state.redis
        ws_app.include_router(WS_AI, prefix="/api/v1")

        with TestClient(ws_app) as ws_client:
            with ws_client.websocket_connect(f"/api/v1/ai/chat/ws?token={token_data['access_token']}") as websocket:
                websocket.send_json({"action": "stop", "session_id": "probe"})
                assert websocket.receive_text() == "当前没有正在进行的生成任务"

                asyncio.run(
                    UserSessionRegistry.revoke_user(
                        test_client.app.state.redis,
                        snapshot["site_id"],
                        fixture.tenant_id,
                        snapshot["local_user_id"],
                    )
                )
                websocket.send_json({"action": "stop", "session_id": "probe"})
                assert websocket.receive_text().startswith("错误: ")
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_text()


def test_chat_query_aclose_closes_nested_agent_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner_closed = asyncio.Event()

    class _Agent:
        def arun(self, **_kwargs):
            async def inner_stream():
                try:
                    yield SimpleNamespace(content="first-chunk")
                    yield SimpleNamespace(content="must-not-send")
                finally:
                    inner_closed.set()

            return inner_stream()

    monkeypatch.setattr(
        "app.plugin.module_ai.chat.service.AgnoFactory.create_agent",
        lambda *_args, **_kwargs: _Agent(),
    )
    auth = AuthSchema.model_construct(
        db=SimpleNamespace(),
        user=SimpleNamespace(id=81, username="federated-user", dept_id=None),
        tenant_id=23,
        check_data_scope=False,
    )

    async def scenario() -> None:
        stream = ChatService(auth).chat_query(
            SimpleNamespace(session_id="nested-stream", message="hello"),
        )
        assert await anext(stream) == "first-chunk"
        await stream.aclose()
        assert inner_closed.is_set()

    asyncio.run(scenario())


def test_websocket_task_cancellation_propagates_and_closes_nested_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream_started = asyncio.Event()
    inner_closed = asyncio.Event()

    class _Agent:
        def arun(self, **_kwargs):
            async def inner_stream():
                try:
                    stream_started.set()
                    await asyncio.Event().wait()
                    yield SimpleNamespace(content="must-not-send")
                finally:
                    inner_closed.set()

            return inner_stream()

    auth = AuthSchema.model_construct(
        db=SimpleNamespace(),
        user=SimpleNamespace(
            id=81,
            username="federated-user",
            dept_id=None,
        ),
        tenant_id=23,
        check_data_scope=False,
    )

    class _WebSocket:
        def __init__(self) -> None:
            self.query_params = {"token": "access-token"}
            self.app = SimpleNamespace(state=SimpleNamespace(redis=SimpleNamespace()))
            self.url = SimpleNamespace(path="/api/v1/ai/chat/ws")
            self.client = "test-client"
            self.sent: list[str] = []
            self.receive_count = 0
            self.closed = False

        async def accept(self) -> None:
            return None

        async def receive_text(self) -> str:
            self.receive_count += 1
            if self.receive_count == 1:
                return json.dumps(
                    {
                        "message": "hello",
                        "session_id": "cancel-stream",
                    }
                )
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send_text(self, text: str) -> None:
            self.sent.append(text)

        async def close(self) -> None:
            self.closed = True

    @asynccontextmanager
    async def fake_db_session():
        yield SimpleNamespace()

    async def authenticate(*_args, **_kwargs):
        return auth

    async def allow_dynamic_plugin(*_args, **_kwargs) -> None:
        return None

    async def model_config(*_args, **_kwargs):
        return SimpleNamespace()

    monkeypatch.setattr(
        "app.plugin.module_ai.chat.service.AgnoFactory.create_agent",
        lambda *_args, **_kwargs: _Agent(),
    )
    monkeypatch.setattr(
        "app.plugin.module_ai.chat.ws.async_db_session",
        fake_db_session,
    )
    monkeypatch.setattr(
        "app.plugin.module_ai.chat.ws._authenticate",
        authenticate,
    )
    monkeypatch.setattr(
        "app.plugin.module_ai.chat.ws.validate_dynamic_plugin_access_for_path",
        allow_dynamic_plugin,
    )
    monkeypatch.setattr(
        "app.plugin.module_ai.chat.ws.resolve_effective_model_config",
        model_config,
    )

    async def scenario() -> None:
        websocket = _WebSocket()
        task = asyncio.create_task(WS_AI.routes[0].endpoint(websocket))
        await asyncio.wait_for(stream_started.wait(), timeout=1)
        task.cancel()
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert task.done()
            assert task.cancelled()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        assert inner_closed.is_set()
        assert "[DONE]" not in websocket.sent
        assert websocket.receive_count == 1

    asyncio.run(scenario())


def test_websocket_revocation_stops_inflight_stream_before_next_chunk(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        assert _post_sync(
            test_client,
            _claims(fixture, subject=subject, sync_version=1),
            monkeypatch,
        ).status_code == 200
        _enable_control_login(monkeypatch, fixture, subject)
        monkeypatch.setattr(
            settings,
            "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
            True,
            raising=False,
        )
        login = test_client.post(
            "/system/auth/control/exchange",
            json={"code": f"ticket-{uuid4().hex}"},
        )
        assert login.status_code == 200, login.text
        token_data = login.json()["data"]
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))
        allow_second_chunk = threading.Event()
        generator_closed = threading.Event()

        async def allow_dynamic_plugin(*_args, **_kwargs) -> None:
            return None

        async def model_config(*_args, **_kwargs):
            return SimpleNamespace()

        class _Agent:
            def arun(self, **_kwargs):
                async def inner_stream():
                    try:
                        yield SimpleNamespace(content="first-chunk")
                        while not allow_second_chunk.is_set():
                            await asyncio.sleep(0.01)
                        yield SimpleNamespace(content="must-not-send")
                    finally:
                        generator_closed.set()

                return inner_stream()

        monkeypatch.setattr(
            "app.plugin.module_ai.chat.ws.validate_dynamic_plugin_access_for_path",
            allow_dynamic_plugin,
        )
        monkeypatch.setattr(
            "app.plugin.module_ai.chat.ws.resolve_effective_model_config",
            model_config,
        )
        monkeypatch.setattr(
            "app.plugin.module_ai.chat.service.AgnoFactory.create_agent",
            lambda *_args, **_kwargs: _Agent(),
        )
        ws_app = FastAPI()
        ws_app.state.redis = test_client.app.state.redis
        ws_app.include_router(WS_AI, prefix="/api/v1")

        with TestClient(ws_app) as ws_client:
            with ws_client.websocket_connect(
                f"/api/v1/ai/chat/ws?token={token_data['access_token']}"
            ) as websocket:
                websocket.send_json({"message": "hello", "session_id": "probe"})
                assert websocket.receive_text() == "first-chunk"
                asyncio.run(
                    UserSessionRegistry.revoke_user(
                        test_client.app.state.redis,
                        snapshot["site_id"],
                        fixture.tenant_id,
                        snapshot["local_user_id"],
                    )
                )
                allow_second_chunk.set()
                assert websocket.receive_text().startswith("错误: ")
                assert generator_closed.is_set()
                with pytest.raises(WebSocketDisconnect):
                    websocket.receive_text()

        assert generator_closed.wait(timeout=1)


def test_recovery_compensates_pending_cleanup_and_updates_replay_receipt(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        active = _claims(fixture, subject=subject, sync_version=1)
        assert _post_sync(test_client, active, monkeypatch).status_code == 200
        original_revoke = UserSessionRegistry.revoke_user.__func__

        async def fail_cleanup(*_args, **_kwargs):
            raise ConnectionError("redis unavailable")

        monkeypatch.setattr(
            UserSessionRegistry,
            "revoke_user",
            classmethod(fail_cleanup),
        )
        inactive = _claims(
            fixture,
            subject=subject,
            sync_version=2,
            desired_state="inactive",
            user_status=1,
        )
        failed = _post_sync(test_client, inactive, monkeypatch)
        assert failed.status_code == 200, failed.text
        assert failed.json()["data"]["session_cleanup_pending"] is True
        superseded = _claims(
            fixture,
            subject=subject,
            sync_version=1,
            desired_state="inactive",
            user_status=1,
        )
        superseded_before = _post_sync(test_client, superseded, monkeypatch)
        assert superseded_before.status_code == 200, superseded_before.text
        assert superseded_before.json()["data"]["disposition"] == "superseded"
        assert superseded_before.json()["data"]["session_cleanup_pending"] is True

        monkeypatch.setattr(
            UserSessionRegistry,
            "revoke_user",
            classmethod(original_revoke),
        )
        before_full_scan = asyncio.run(
            FederatedSessionRecovery.compensate_pending(
                test_client.app.state.redis,
                batch_size=50,
                max_batches=10,
            )
        )
        before_snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))
        asyncio.run(
            FederatedSessionRecovery.rebuild_indexes(
                test_client.app.state.redis,
                scan_count=100,
                max_batches=100,
            )
        )
        compensated = asyncio.run(
            FederatedSessionRecovery.compensate_pending(
                test_client.app.state.redis,
                batch_size=50,
                max_batches=10,
            )
        )
        replay = _post_sync(test_client, inactive, monkeypatch)
        superseded_replay = _post_sync(test_client, superseded, monkeypatch)
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))

    assert before_full_scan == 0
    assert before_snapshot["session_cleanup_pending"] is True
    assert compensated >= 1
    assert snapshot["status"] == "inactive"
    assert snapshot["session_cleanup_pending"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["disposition"] == "replayed"
    assert replay.json()["data"]["session_cleanup_pending"] is False
    assert superseded_replay.status_code == 200, superseded_replay.text
    assert superseded_replay.json()["data"]["disposition"] == "replayed"
    assert (
        superseded_replay.json()["data"]["session_cleanup_pending"] is False
    )


@pytest.mark.parametrize("inline_cleanup", [True, False])
def test_recovery_full_scan_indexes_and_revokes_historical_unindexed_session(
    test_client,
    monkeypatch,
    inline_cleanup,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        assert _post_sync(
            test_client,
            _claims(fixture, subject=subject, sync_version=1),
            monkeypatch,
        ).status_code == 200
        _enable_control_login(monkeypatch, fixture, subject)
        login = test_client.post(
            "/system/auth/control/exchange",
            json={"code": f"ticket-{uuid4().hex}"},
        )
        assert login.status_code == 200, login.text
        session_id = decode_access_token(login.json()["data"]["access_token"]).sub
        assert session_id is not None
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))
        session_key = f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}"
        index_key = UserSessionRegistry.index_key(
            snapshot["site_id"], fixture.tenant_id, snapshot["local_user_id"]
        )
        redis = test_client.app.state.redis

        if not inline_cleanup:
            async def incomplete_scan(*args, **kwargs):
                return False
            monkeypatch.setattr(UserSessionRegistry, "index_user_historical_sessions", classmethod(incomplete_scan))

        # Simulate a session created before the reverse index existed.
        asyncio.run(redis.srem(index_key, session_id))
        assert asyncio.run(redis.get(session_key)) is not None

        inactive = _post_sync(
            test_client,
            _claims(
                fixture,
                subject=subject,
                sync_version=2,
                desired_state="inactive",
                user_status=1,
            ),
            monkeypatch,
        )
        before_scan = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))
        assert inactive.status_code == 200, inactive.text
        if inline_cleanup:
            assert inactive.json()["data"]["session_cleanup_pending"] is False
            assert before_scan["session_cleanup_pending"] is False
            assert asyncio.run(redis.get(session_key)) is None
            return
        assert inactive.json()["data"]["session_cleanup_pending"] is True
        assert before_scan["session_cleanup_pending"] is True
        assert asyncio.run(redis.get(session_key)) is not None
        pending_before_compensation = asyncio.run(_pending_cleanup_count())

        asyncio.run(
            FederatedSessionRecovery.rebuild_indexes(
                redis,
                scan_count=100,
                max_batches=100,
            )
        )
        compensated = asyncio.run(
            FederatedSessionRecovery.compensate_pending(
                redis,
                batch_size=50,
                max_batches=10,
            )
        )
        after_scan = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))
        pending_after_compensation = asyncio.run(_pending_cleanup_count())

    assert compensated == pending_before_compensation - pending_after_compensation
    assert compensated >= 1
    assert asyncio.run(redis.get(session_key)) is None
    assert after_scan["session_cleanup_pending"] is False


def test_recovery_does_not_count_compensation_when_database_commit_fails(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        subject = str(uuid4())
        assert _post_sync(
            test_client,
            _claims(fixture, subject=subject, sync_version=1),
            monkeypatch,
        ).status_code == 200
        original_revoke = UserSessionRegistry.revoke_user.__func__

        async def fail_cleanup(*_args, **_kwargs):
            raise ConnectionError("redis unavailable")

        monkeypatch.setattr(
            UserSessionRegistry,
            "revoke_user",
            classmethod(fail_cleanup),
        )
        inactive = _post_sync(
            test_client,
            _claims(
                fixture,
                subject=subject,
                sync_version=2,
                desired_state="inactive",
                user_status=1,
            ),
            monkeypatch,
        )
        assert inactive.status_code == 200, inactive.text
        monkeypatch.setattr(
            UserSessionRegistry,
            "revoke_user",
            classmethod(original_revoke),
        )
        redis = test_client.app.state.redis
        asyncio.run(
            FederatedSessionRecovery.rebuild_indexes(
                redis,
                scan_count=100,
                max_batches=100,
            )
        )

        def reject_commit(_session) -> None:
            raise RuntimeError("forced compensation commit failure")

        event.listen(Session, "before_commit", reject_commit)
        try:
            compensated = asyncio.run(
                FederatedSessionRecovery.compensate_pending(
                    redis,
                    batch_size=50,
                    max_batches=10,
                )
            )
        finally:
            event.remove(Session, "before_commit", reject_commit)
        snapshot = asyncio.run(_entitlement_snapshot(subject, fixture.tenant_id))

    assert compensated == 0
    assert snapshot["session_cleanup_pending"] is True


async def _run_direct_sync(
    fixture: SyncFixture,
    claims: ControlUserAccessClaims,
):
    async with async_db_session() as db:
        async with db.begin():
            return await ControlUserAccessSyncService._sync_claims(
                request=SimpleNamespace(),
                db=db,
                claims=claims.model_copy(deep=True),
                site=SimpleNamespace(id=1, code=fixture.site_code),
            )


def test_two_sessions_same_first_event_replay_without_duplicate(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture(with_business_menu=False))
        claims = _claims(fixture, desired_state="inactive", user_status=1)
        monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", ISSUER)

        async def run_concurrently():
            results = await asyncio.gather(
                _run_direct_sync(fixture, claims),
                _run_direct_sync(fixture, claims),
            )
            async with async_db_session() as db:
                entitlement_count = (
                    await db.execute(
                        select(func.count())
                        .select_from(FederatedAccessEntitlementModel)
                        .where(
                            FederatedAccessEntitlementModel.central_user_uuid == claims.central_user_uuid,
                            FederatedAccessEntitlementModel.tenant_id == fixture.tenant_id,
                        )
                    )
                ).scalar_one()
                event_count = (await db.execute(select(func.count()).select_from(FederatedAccessEventModel).where(FederatedAccessEventModel.event_id == claims.event_id))).scalar_one()
            return results, entitlement_count, event_count

        results, entitlement_count, event_count = asyncio.run(run_concurrently())

    assert sorted(result.disposition for result in results) == ["applied", "replayed"]
    assert entitlement_count == 1
    assert event_count == 1


def test_two_sessions_same_first_version_different_events_return_409(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture(with_business_menu=False))
        subject = str(uuid4())
        first = _claims(
            fixture,
            subject=subject,
            desired_state="inactive",
            user_status=1,
        )
        second = _claims(
            fixture,
            subject=subject,
            desired_state="inactive",
            user_status=1,
        )
        monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", ISSUER)

        async def run_concurrently():
            return await asyncio.gather(
                _run_direct_sync(fixture, first),
                _run_direct_sync(fixture, second),
                return_exceptions=True,
            )

        results = asyncio.run(run_concurrently())

    applied = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, CustomException)]
    assert len(applied) == 1
    assert applied[0].disposition == "applied"
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409


def test_cross_tenant_global_event_conflict_is_deterministic_409(
    test_client,
    monkeypatch,
) -> None:
    with _use_selected_assembly():
        first_fixture = asyncio.run(_seed_sync_fixture(with_business_menu=False))
        second_fixture = asyncio.run(_seed_sync_fixture(with_business_menu=False))
        event_id = str(uuid4())
        first = _claims(
            first_fixture,
            event_id=event_id,
            desired_state="inactive",
            user_status=1,
        )
        second = _claims(
            second_fixture,
            event_id=event_id,
            desired_state="inactive",
            user_status=1,
        )
        monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", ISSUER)

        async def run_concurrently():
            return await asyncio.gather(
                _run_direct_sync(first_fixture, first),
                _run_direct_sync(second_fixture, second),
                return_exceptions=True,
            )

        results = asyncio.run(run_concurrently())

    applied = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, CustomException)]
    assert len(applied) == 1
    assert applied[0].disposition == "applied"
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409


def test_global_event_unique_race_is_safely_reread_as_replay_or_409(
    monkeypatch,
) -> None:
    fixture = SyncFixture(
        site_code="default",
        tenant_id=1,
        tenant_code="local-tenant",
        central_tenant_uuid=str(uuid4()),
        central_tenant_code="central-tenant",
        business_menu_id=None,
    )
    claims = _claims(fixture, desired_state="inactive", user_status=1)
    result = ControlUserAccessSyncOut(
        disposition="applied",
        status="inactive",
        applied_version=1,
        local_user_id=None,
        role_codes=[],
        effective_menu_count=0,
        session_cleanup_pending=False,
    )
    fingerprint = ControlUserAccessSyncService._request_fingerprint(claims)

    class UniqueRaceDB:
        @asynccontextmanager
        async def begin_nested(self):
            yield

        def add(self, _event) -> None:
            return None

        async def flush(self) -> None:
            raise IntegrityError("INSERT", {}, Exception("duplicate event"))

    async def run_with_receipt(receipt):
        async def lock_event(_db, _event_id):
            return receipt

        monkeypatch.setattr(
            ControlUserAccessSyncService,
            "_lock_event",
            staticmethod(lock_event),
        )
        return await ControlUserAccessSyncService._save_receipt(
            db=UniqueRaceDB(),
            entitlement=SimpleNamespace(id=101),
            claims=claims,
            fingerprint=fingerprint,
            result=result,
        )

    replay = asyncio.run(
        run_with_receipt(
            SimpleNamespace(
                entitlement_id=101,
                request_fingerprint=fingerprint,
                result_json=result.model_dump(mode="json"),
            )
        )
    )
    assert replay.disposition == "replayed"

    with pytest.raises(CustomException) as exc_info:
        asyncio.run(
            run_with_receipt(
                SimpleNamespace(
                    entitlement_id=202,
                    request_fingerprint="different",
                    result_json=result.model_dump(mode="json"),
                )
            )
        )
    assert exc_info.value.status_code == 409


def _provision_claims(
    fixture: SyncFixture,
    *,
    owner_uuid: str,
    request_uuid: str,
) -> ControlTenantProvisionClaims:
    return ControlTenantProvisionClaims(
        provision_request_uuid=request_uuid,
        central_tenant_uuid=fixture.central_tenant_uuid,
        central_tenant_code=fixture.central_tenant_code,
        tenant_name="已开户租户",
        site_code=fixture.site_code,
        target_tenant_code=fixture.tenant_code,
        target_package_code="unused",
        owner={
            "central_user_uuid": owner_uuid,
            "username": "unused_owner",
            "name": "开户所有者",
            "mobile": None,
            "email": None,
            "avatar": None,
            "status": 0,
        },
        issuer=ISSUER,
    )


def test_owner_bootstrap_entitlement_is_version_one_and_keeps_owner_role() -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        owner_uuid = str(uuid4())
        request_uuid = str(uuid4())
        claims = _provision_claims(
            fixture,
            owner_uuid=owner_uuid,
            request_uuid=request_uuid,
        )

        async def seed_owner_and_bootstrap() -> tuple[int, list[str], int]:
            async with async_db_session() as db:
                owner = UserModel(
                    username=f"owner_{uuid4().hex[:16]}",
                    password="unused",
                    name="开户所有者",
                    tenant_id=fixture.tenant_id,
                    auth_source="federated",
                    password_login_enabled=False,
                    status=0,
                )
                owner_role = RoleModel(
                    name="租户所有者",
                    code="owner",
                    tenant_id=fixture.tenant_id,
                    status=0,
                    is_system=True,
                    data_scope=1,
                )
                db.add_all([owner, owner_role])
                await db.flush()
                db.add_all(
                    [
                        FederatedIdentityModel(
                            site_id=1,
                            issuer=ISSUER,
                            central_user_uuid=owner_uuid,
                            local_user_id=owner.id,
                        ),
                        TenantUserModel(
                            user_id=owner.id,
                            tenant_id=fixture.tenant_id,
                            role="owner",
                            is_default=1,
                        ),
                        UserRolesModel(user_id=owner.id, role_id=owner_role.id),
                    ]
                )
                await ControlTenantProvisioningService._ensure_owner_entitlement(
                    db=db,
                    mapping=SimpleNamespace(
                        site_id=1,
                        issuer=ISSUER,
                        central_tenant_uuid=fixture.central_tenant_uuid,
                        local_tenant_id=fixture.tenant_id,
                        owner_central_user_uuid=owner_uuid,
                        provision_request_uuid=request_uuid,
                    ),
                    claims=claims,
                )
                await db.commit()

            snapshot = await _entitlement_snapshot(owner_uuid, fixture.tenant_id)
            async with async_db_session() as db:
                event_count = (await db.execute(select(func.count()).select_from(FederatedAccessEventModel).where(FederatedAccessEventModel.event_id == request_uuid))).scalar_one()
            return snapshot["applied_version"], snapshot["role_codes"], event_count

        version, role_codes, event_count = asyncio.run(seed_owner_and_bootstrap())

    assert version == 1
    assert role_codes == ["owner"]
    assert event_count == 1


def test_owner_bootstrap_does_not_overwrite_higher_governed_version() -> None:
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture())
        owner_uuid = str(uuid4())
        request_uuid = str(uuid4())
        claims = _provision_claims(
            fixture,
            owner_uuid=owner_uuid,
            request_uuid=request_uuid,
        )

        async def seed_governed_state() -> tuple[str, int]:
            async with async_db_session() as db:
                db.add(
                    FederatedAccessEntitlementModel(
                        site_id=1,
                        tenant_id=fixture.tenant_id,
                        local_user_id=None,
                        issuer=ISSUER,
                        central_user_uuid=owner_uuid,
                        status="inactive",
                        applied_version=4,
                        last_event_id=str(uuid4()),
                    )
                )
                await db.commit()
            async with async_db_session() as db:
                await ControlTenantProvisioningService._ensure_owner_entitlement(
                    db=db,
                    mapping=SimpleNamespace(
                        site_id=1,
                        issuer=ISSUER,
                        central_tenant_uuid=fixture.central_tenant_uuid,
                        local_tenant_id=fixture.tenant_id,
                        owner_central_user_uuid=owner_uuid,
                        provision_request_uuid=request_uuid,
                    ),
                    claims=claims,
                )
                await db.commit()
            snapshot = await _entitlement_snapshot(owner_uuid, fixture.tenant_id)
            return snapshot["status"], snapshot["applied_version"]

        state = asyncio.run(seed_governed_state())

    assert state == ("inactive", 4)


def test_claim_fingerprint_is_canonical_and_contains_no_ticket() -> None:
    fixture = asyncio.run(_seed_sync_fixture())
    claims = _claims(fixture)
    canonical = json.dumps(
        claims.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert ControlUserAccessSyncService._request_fingerprint(claims) == hashlib.sha256(canonical).hexdigest()


def test_active_sync_uses_tenant_then_user_lock_protocol(monkeypatch) -> None:
    fixture = asyncio.run(_seed_sync_fixture())
    claims = _claims(fixture)
    events: list[str] = []
    monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", ISSUER)

    @contextmanager
    def product_context():
        with _use_selected_assembly() as product:
            yield product

    async def exercise() -> None:
        from app.api.v1.module_system.federated_access import service as service_module

        async with async_db_session() as db:

            async def fake_resolve(_db, _request):
                return SimpleNamespace(id=1, code=fixture.site_code)

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def tenant_lock(_db, tenant_id):
                assert tenant_id == fixture.tenant_id
                events.append("tenant_enter")
                try:
                    yield
                finally:
                    events.append("tenant_exit")

            @asynccontextmanager
            async def user_lock(_db, user_ids):
                assert events == ["tenant_enter"]
                events.append("user_enter")
                try:
                    yield []
                finally:
                    events.append("user_exit")

            monkeypatch.setattr(service_module, "resolve_request_site", fake_resolve)
            monkeypatch.setattr(
                service_module,
                "lock_tenant_role_assignment",
                tenant_lock,
            )
            monkeypatch.setattr(
                service_module,
                "lock_tenant_membership_users",
                user_lock,
            )
            with product_context():
                await ControlUserAccessSyncService._sync_claims(
                    request=SimpleNamespace(headers={"host": "testserver"}),
                    db=db,
                    claims=claims,
                )
            await db.rollback()

    asyncio.run(exercise())

    assert events == ["tenant_enter", "user_enter", "user_exit", "tenant_exit"]


def test_manual_policy_accepts_entitlement_without_assigning_business_role(test_client, monkeypatch, tmp_path):
    from pathlib import Path
    with _use_selected_assembly():
        fixture = asyncio.run(_seed_sync_fixture(with_business_menu=False))
        policy = tmp_path / "manual.toml"
        policy.write_text(Path(settings.APP_ASSEMBLY_FILE).read_text().replace('mode = "declared"', 'mode = "manual"'))
        settings.APP_ASSEMBLY_FILE = str(policy)
        reset_assembly_cache()
        claims = _claims(fixture)
        response = _post_sync(test_client, claims, monkeypatch)
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        assert result["status"] == "active"
        assert result["role_codes"] == []
        assert result["effective_menu_count"] == 0
        snapshot = asyncio.run(_entitlement_snapshot(claims.central_user_uuid, fixture.tenant_id))
        assert snapshot["membership_count"] == 1
        assert snapshot["role_codes"] == []
