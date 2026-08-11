import asyncio
import base64
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import func, select

from app.api.v1.module_platform.federated_tenant.model import FederatedTenantModel
from app.api.v1.module_platform.package.model import PackageModel, PackagePluginModel
from app.api.v1.module_platform.plugin.model import TenantPluginModel
from app.api.v1.module_platform.tenant.credit_code import USCC_ALPHABET, USCC_WEIGHTS
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.config.setting import Settings, settings
from app.core.database import async_db_session
from app.core.exceptions import CustomException

ISSUER = "https://control.example/api/v1"


def _make_uscc() -> str:
    prefix = "".join(USCC_ALPHABET[byte % len(USCC_ALPHABET)] for byte in uuid.uuid4().bytes)
    prefix = f"9{prefix[:16]}"
    total = sum(
        USCC_ALPHABET.index(char) * weight
        for char, weight in zip(prefix, USCC_WEIGHTS, strict=True)
    )
    return f"{prefix}{USCC_ALPHABET[(31 - total % 31) % 31]}"


VALID_USCC = _make_uscc()


def _unique(prefix: str) -> str:
    return f"{prefix}{time.time_ns()}"


def _claims(
    tag: str,
    *,
    request_uuid: str | None = None,
    central_tenant_uuid: str | None = None,
    tenant_code: str | None = None,
    package_code: str = "basic",
    site_code: str = "default",
    owner_uuid: str | None = None,
    unified_social_credit_code: str | None = None,
) -> dict:
    target_code = tenant_code or _unique("Auto")
    return {
        "provision_request_uuid": request_uuid or str(uuid.uuid4()),
        "central_tenant_uuid": central_tenant_uuid or str(uuid.uuid4()),
        "central_tenant_code": f"central{tag}",
        "tenant_name": _unique(f"自动开户{tag}"),
        "unified_social_credit_code": unified_social_credit_code,
        "contact_name": "企业联系人",
        "contact_phone": "13800009999",
        "contact_email": f"{tag}@example.com",
        "address": "企业地址",
        "site_code": site_code,
        "target_tenant_code": target_code,
        "target_package_code": package_code,
        "owner": {
            "central_user_uuid": owner_uuid or str(uuid.uuid4()),
            "username": f"{target_code}_admin",
            "name": f"{tag}管理员",
            "mobile": "13900008888",
            "email": f"owner-{tag}@example.com",
            "avatar": None,
            "status": 0,
        },
        "issuer": ISSUER,
    }


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, payloads: list[dict], endpoint: str = "control/provisioning/exchange") -> None:
        self._payloads = iter(payloads)
        self._endpoint = endpoint
        self._lock = threading.Lock()
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert request.url == f"{ISSUER}/{self._endpoint}"
        assert request.headers["authorization"] == "Basic " + base64.b64encode(
            b"target-client:target-secret"
        ).decode()
        with self._lock:
            self.calls += 1
            payload = next(self._payloads)
        return httpx.Response(200, json={"data": payload})


class ExchangeErrorTransport(httpx.AsyncBaseTransport):
    def __init__(self, failure: Exception | int) -> None:
        self.failure = failure

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self.failure, Exception):
            if isinstance(self.failure, httpx.RequestError):
                self.failure.request = request
            raise self.failure
        return httpx.Response(self.failure, request=request)


def _enable_provisioning(monkeypatch, payloads: list[dict]) -> RecordingTransport:
    from app.api.v1.module_system.auth.control_tenant_provisioning_service import (
        ControlTenantProvisioningService,
    )

    transport = RecordingTransport(payloads)
    monkeypatch.setattr(settings, "CONTROL_TENANT_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "CONTROL_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", f"{ISSUER}/")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_ID", "target-client")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_SECRET", "target-secret")
    monkeypatch.setattr(settings, "CONTROL_SSO_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(ControlTenantProvisioningService, "transport", transport)
    return transport


def _enable_provisioning_transport(monkeypatch, transport: httpx.AsyncBaseTransport) -> None:
    from app.api.v1.module_system.auth.control_tenant_provisioning_service import (
        ControlTenantProvisioningService,
    )

    monkeypatch.setattr(settings, "CONTROL_TENANT_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "CONTROL_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", f"{ISSUER}/")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_ID", "target-client")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_SECRET", "target-secret")
    monkeypatch.setattr(settings, "CONTROL_SSO_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(ControlTenantProvisioningService, "transport", transport)


async def _provision_snapshot(claims: dict) -> dict:
    async with async_db_session() as db:
        mapping = (
            await db.execute(
                select(FederatedTenantModel).where(
                    FederatedTenantModel.provision_request_uuid
                    == claims["provision_request_uuid"]
                )
            )
        ).scalar_one_or_none()
        tenant = None if mapping is None else await db.get(TenantModel, mapping.local_tenant_id)
        identity = (
            await db.execute(
                select(FederatedIdentityModel).where(
                    FederatedIdentityModel.site_id == 1,
                    FederatedIdentityModel.issuer == ISSUER,
                    FederatedIdentityModel.central_user_uuid
                    == claims["owner"]["central_user_uuid"],
                )
            )
        ).scalar_one_or_none()
        user = None if identity is None else await db.get(UserModel, identity.local_user_id)
        membership = None
        roles = 0
        role_menus = 0
        tenant_plugins = 0
        if user is not None and tenant is not None:
            membership = (
                await db.execute(
                    select(TenantUserModel).where(
                        TenantUserModel.user_id == user.id,
                        TenantUserModel.tenant_id == tenant.id,
                    )
                )
            ).scalar_one_or_none()
            roles = (
                await db.execute(
                    select(func.count())
                    .select_from(UserRolesModel)
                    .join(RoleModel, RoleModel.id == UserRolesModel.role_id)
                    .where(
                        UserRolesModel.user_id == user.id,
                        RoleModel.tenant_id == tenant.id,
                        RoleModel.code == "owner",
                    )
                )
            ).scalar_one()
            role_menus = (
                await db.execute(
                    select(func.count())
                    .select_from(RoleMenusModel)
                    .join(RoleModel, RoleModel.id == RoleMenusModel.role_id)
                    .where(RoleModel.tenant_id == tenant.id, RoleModel.code == "owner")
                )
            ).scalar_one()
            tenant_plugins = (
                await db.execute(
                    select(func.count())
                    .select_from(TenantPluginModel)
                    .where(
                        TenantPluginModel.tenant_id == tenant.id,
                        TenantPluginModel.enabled.is_(True),
                        TenantPluginModel.purchased.is_(True),
                    )
                )
            ).scalar_one()
        package_plugin_count = (
            await db.execute(
                select(func.count())
                .select_from(PackagePluginModel)
                .join(PackageModel, PackageModel.id == PackagePluginModel.package_id)
                .where(PackageModel.site_id == 1, PackageModel.code == claims["target_package_code"])
            )
        ).scalar_one()
        local_admin = (
            await db.execute(
                select(UserModel).where(
                    UserModel.username == f"{claims['target_tenant_code']}_admin"
                )
            )
        ).scalar_one_or_none()
        return {
            "mapping": mapping,
            "tenant": tenant,
            "identity": identity,
            "user": user,
            "membership": membership,
            "roles": roles,
            "role_menus": role_menus,
            "tenant_plugins": tenant_plugins,
            "package_plugin_count": package_plugin_count,
            "local_admin": local_admin,
        }


async def _count_tenants(code: str) -> int:
    async with async_db_session() as db:
        return (
            await db.execute(
                select(func.count()).select_from(TenantModel).where(TenantModel.code == code)
            )
        ).scalar_one()


async def _set_tenant_package(tenant_code: str, package_code: str) -> None:
    async with async_db_session() as db:
        tenant = (
            await db.execute(select(TenantModel).where(TenantModel.code == tenant_code))
        ).scalar_one()
        package = (
            await db.execute(
                select(PackageModel).where(
                    PackageModel.site_id == tenant.site_id,
                    PackageModel.code == package_code,
                )
            )
        ).scalar_one()
        tenant.package_id = package.id
        await db.commit()


async def _provisioning_totals() -> tuple[int, int]:
    async with async_db_session() as db:
        tenants = (
            await db.execute(select(func.count()).select_from(TenantModel))
        ).scalar_one()
        mappings = (
            await db.execute(select(func.count()).select_from(FederatedTenantModel))
        ).scalar_one()
        return tenants, mappings


def test_tenant_provision_returns_404_without_network_or_writes_when_disabled(
    test_client, monkeypatch
) -> None:
    from app.api.v1.module_system.auth.control_tenant_provisioning_service import (
        ControlTenantProvisioningService,
    )

    transport = RecordingTransport([])
    monkeypatch.setattr(settings, "CONTROL_TENANT_PROVISIONING_ENABLED", False, raising=False)
    monkeypatch.setattr(ControlTenantProvisioningService, "transport", transport)
    before = asyncio.run(_provisioning_totals())

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "x" * 20}
    )

    assert response.status_code == 404
    assert response.json()["msg"] == "中控租户自动开户未启用"
    assert transport.calls == 0
    assert asyncio.run(_provisioning_totals()) == before


def test_provisioning_configuration_requires_complete_sso_configuration() -> None:
    with pytest.raises(ValueError, match="中控租户自动开户.*SSO"):
        Settings(
            _env_file=None,
            CONTROL_TENANT_PROVISIONING_ENABLED=True,
            CONTROL_SSO_ENABLED=False,
        )


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connect failed"),
        httpx.ReadTimeout("read timed out"),
        429,
        502,
        503,
        504,
    ],
)
def test_tenant_provision_maps_retryable_exchange_failures_to_503(
    test_client, monkeypatch, failure: Exception | int
) -> None:
    _enable_provisioning_transport(monkeypatch, ExchangeErrorTransport(failure))

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "r" * 20}
    )

    assert response.status_code == 503


def test_tenant_provision_preserves_invalid_exchange_credentials_as_401(
    test_client, monkeypatch
) -> None:
    _enable_provisioning_transport(monkeypatch, ExchangeErrorTransport(401))

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "u" * 20}
    )

    assert response.status_code == 401


def test_tenant_provision_creates_federated_owner_without_local_admin(
    test_client, monkeypatch
) -> None:
    claims = _claims("create", unified_social_credit_code=VALID_USCC)
    transport = _enable_provisioning(monkeypatch, [claims])

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "p" * 20}
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["result"] == "created"
    assert transport.calls == 1
    snapshot = asyncio.run(_provision_snapshot(claims))
    assert snapshot["mapping"] is not None
    assert snapshot["tenant"].unified_social_credit_code == VALID_USCC
    assert snapshot["user"].auth_source == "federated"
    assert snapshot["user"].password_login_enabled is False
    assert snapshot["membership"].role == "owner"
    assert snapshot["roles"] == 1
    assert snapshot["role_menus"] > 0
    assert snapshot["tenant_plugins"] == snapshot["package_plugin_count"]
    assert snapshot["local_admin"] is None


def test_tenant_provision_does_not_send_one_time_code_to_operation_log(
    test_client, auth_headers, monkeypatch
) -> None:
    from app.core import router_class

    claims = _claims("operation-log")
    code = f"provision-{uuid.uuid4().hex}"
    captured: list[dict] = []

    async def capture(log_data: dict) -> None:
        captured.append(log_data)

    _enable_provisioning(monkeypatch, [claims])
    monkeypatch.setattr(settings, "OPERATION_LOG_RECORD", True)
    monkeypatch.setattr(router_class, "_write_operation_log_async", capture)

    response = test_client.post(
        "/system/auth/control/tenant/provision",
        headers=auth_headers,
        json={"code": code},
    )

    assert response.status_code == 200, response.text
    assert all(code not in entry.get("request_payload", "") for entry in captured)


def test_repeated_provision_is_idempotent_and_does_not_exchange_twice_per_request(
    test_client, monkeypatch
) -> None:
    claims = _claims("repeat")
    transport = _enable_provisioning(monkeypatch, [claims, claims])

    first = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "a" * 20}
    )
    second = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "b" * 20}
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["result"] == "created"
    assert second.json()["data"]["result"] == "already_exists"
    assert first.json()["data"]["target_tenant_id"] == second.json()["data"]["target_tenant_id"]
    assert transport.calls == 2
    assert asyncio.run(_count_tenants(claims["target_tenant_code"])) == 1


def test_repeated_provision_rejects_actual_tenant_package_drift(
    test_client, monkeypatch
) -> None:
    claims = _claims("actual-package-drift", package_code="basic")
    _enable_provisioning(monkeypatch, [claims, claims])
    created = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "l" * 20}
    )
    assert created.status_code == 200, created.text
    asyncio.run(_set_tenant_package(claims["target_tenant_code"], "pro"))

    repeated = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "m" * 20}
    )

    assert repeated.status_code == 409, repeated.text
    assert "漂移" in repeated.json()["msg"]


def test_repeated_provision_normalizes_uscc_before_idempotency_comparison(
    test_client, monkeypatch
) -> None:
    normalized_uscc = _make_uscc()
    raw_uscc = f" {normalized_uscc.lower()} "
    claims = _claims("normalized-uscc", unified_social_credit_code=raw_uscc)
    _enable_provisioning(monkeypatch, [claims, claims])

    created = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "n" * 20}
    )
    repeated = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "o" * 20}
    )

    assert created.status_code == repeated.status_code == 200
    assert repeated.json()["data"]["result"] == "already_exists"
    snapshot = asyncio.run(_provision_snapshot(claims))
    assert snapshot["tenant"].unified_social_credit_code == normalized_uscc


def test_repeated_provision_accepts_changed_central_tenant_code_and_updates_audit_value(
    test_client, monkeypatch
) -> None:
    initial = _claims("central-code")
    changed = {**initial, "central_tenant_code": "central-renamed"}
    _enable_provisioning(monkeypatch, [initial, changed])

    created = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "v" * 20}
    )
    repeated = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "w" * 20}
    )

    assert created.status_code == repeated.status_code == 200
    assert repeated.json()["data"]["result"] == "already_exists"
    snapshot = asyncio.run(_provision_snapshot(initial))
    assert snapshot["mapping"].central_tenant_code == "central-renamed"


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("target_package_code", "pro"),
        ("unified_social_credit_code", "different-credit-code"),
        ("owner.central_user_uuid", "different-owner"),
    ],
)
def test_existing_mapping_rejects_package_credit_code_and_owner_drift(
    test_client, monkeypatch, changed_field: str, changed_value: str
) -> None:
    initial_credit_code = _make_uscc()
    initial = _claims("drift", unified_social_credit_code=initial_credit_code)
    changed = {**initial, "owner": dict(initial["owner"])}
    if changed_field.startswith("owner."):
        changed["owner"][changed_field.split(".", 1)[1]] = changed_value
    else:
        changed[changed_field] = (
            _make_uscc()
            if changed_field == "unified_social_credit_code"
            else changed_value
        )
    _enable_provisioning(monkeypatch, [initial, changed])

    created = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "c" * 20}
    )
    drifted = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "d" * 20}
    )

    assert created.status_code == 200, created.text
    assert drifted.status_code == 409, drifted.text
    assert "漂移" in drifted.json()["msg"]
    snapshot = asyncio.run(_provision_snapshot(initial))
    assert snapshot["tenant"].package.code == initial["target_package_code"]
    assert snapshot["tenant"].unified_social_credit_code == initial_credit_code
    assert snapshot["mapping"].owner_central_user_uuid == initial["owner"]["central_user_uuid"]


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        ({"site_code": "other"}, "站点"),
        ({"target_package_code": "missing"}, "套餐"),
    ],
)
def test_site_and_package_errors_roll_back_entire_provision(
    test_client, monkeypatch, override: dict, expected_message: str
) -> None:
    claims = {**_claims("invalid"), **override}
    _enable_provisioning(monkeypatch, [claims])

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "e" * 20}
    )

    assert response.status_code in {400, 403}
    assert expected_message in response.json()["msg"]
    assert asyncio.run(_count_tenants(claims["target_tenant_code"])) == 0
    assert asyncio.run(_provision_snapshot(claims))["mapping"] is None


def test_tenant_code_conflict_rolls_back_without_replacing_existing_tenant(
    test_client, auth_headers, monkeypatch
) -> None:
    code = _unique("Conflict")
    existing = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={"name": _unique("现有租户"), "code": code, "site_id": 1},
    )
    assert existing.status_code == 200, existing.text
    claims = _claims("code-conflict", tenant_code=code)
    _enable_provisioning(monkeypatch, [claims])

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "f" * 20}
    )

    assert response.status_code == 409, response.text
    assert "租户编码" in response.json()["msg"]
    assert asyncio.run(_count_tenants(code)) == 1
    assert asyncio.run(_provision_snapshot(claims))["mapping"] is None


def test_credit_code_conflict_rolls_back_without_creating_mapping(
    test_client, auth_headers, monkeypatch
) -> None:
    credit_code = _make_uscc()
    existing = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("信用代码现有租户"),
            "code": _unique("Credit"),
            "site_id": 1,
            "unified_social_credit_code": credit_code,
        },
    )
    assert existing.status_code == 200, existing.text
    claims = _claims("credit-conflict", unified_social_credit_code=credit_code)
    _enable_provisioning(monkeypatch, [claims])

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "q" * 20}
    )

    assert response.status_code == 409, response.text
    assert "统一社会信用代码" in response.json()["msg"]
    assert asyncio.run(_count_tenants(claims["target_tenant_code"])) == 0
    assert asyncio.run(_provision_snapshot(claims))["mapping"] is None


def test_owner_failure_rolls_back_tenant_identity_membership_and_mapping(
    test_client, monkeypatch
) -> None:
    from app.api.v1.module_platform.tenant.service import TenantService

    claims = _claims("owner-failure")
    _enable_provisioning(monkeypatch, [claims])

    async def fail_owner(*args, **kwargs) -> None:
        raise CustomException(msg="owner 创建失败", status_code=409)

    monkeypatch.setattr(TenantService, "ensure_tenant_owner", fail_owner)

    response = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "g" * 20}
    )

    assert response.status_code == 409, response.text
    assert asyncio.run(_count_tenants(claims["target_tenant_code"])) == 0
    snapshot = asyncio.run(_provision_snapshot(claims))
    assert snapshot["mapping"] is None
    assert snapshot["identity"] is None
    assert snapshot["user"] is None


def test_concurrent_provision_creates_only_one_tenant_and_mapping(
    test_client, monkeypatch
) -> None:
    claims = _claims("concurrent")
    _enable_provisioning(monkeypatch, [claims, claims])

    def provision(code: str):
        return test_client.post(
            "/system/auth/control/tenant/provision", json={"code": code * 20}
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(provision, ("h", "i")))

    for response in responses:
        assert response.status_code == 200, response.text
    assert sorted(response.json()["data"]["result"] for response in responses) == [
        "already_exists",
        "created",
    ]
    assert asyncio.run(_count_tenants(claims["target_tenant_code"])) == 1
    snapshot = asyncio.run(_provision_snapshot(claims))
    assert snapshot["mapping"] is not None
    assert snapshot["roles"] == 1


def test_normal_sso_reuses_provisioned_owner_and_preserves_owner_menus(
    test_client, monkeypatch
) -> None:
    claims = _claims("sso-owner")
    transport = _enable_provisioning(monkeypatch, [claims])
    provision = test_client.post(
        "/system/auth/control/tenant/provision", json={"code": "j" * 20}
    )
    assert provision.status_code == 200, provision.text
    before = asyncio.run(_provision_snapshot(claims))

    from app.api.v1.module_system.auth.control_sso_service import ControlSSOClientService

    sso_claims = {
        "issuer": ISSUER,
        "central_user_uuid": claims["owner"]["central_user_uuid"],
        "name": "SSO 更新后的负责人",
        "mobile": claims["owner"]["mobile"],
        "email": claims["owner"]["email"],
        "avatar": None,
        "status": 0,
        "site_code": "default",
        "central_tenant_code": claims["central_tenant_code"],
        "target_tenant_code": claims["target_tenant_code"],
    }
    monkeypatch.setattr(
        ControlSSOClientService,
        "transport",
        RecordingTransport([sso_claims], endpoint="control/sso/exchange"),
    )

    login = test_client.post(
        "/system/auth/control/exchange", json={"code": "k" * 20}
    )

    assert login.status_code == 200, login.text
    after = asyncio.run(_provision_snapshot(claims))
    assert after["user"].id == before["user"].id
    assert after["user"].name == "SSO 更新后的负责人"
    assert after["membership"].role == "owner"
    assert after["roles"] == 1
    assert after["role_menus"] > 0
    assert transport.calls == 1
