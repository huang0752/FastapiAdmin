import asyncio
import base64
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import UniqueConstraint, func, select

from app.api.v1.module_platform.tenant.model import TenantUserModel
from app.api.v1.module_system.auth.federated_identity_service import (
    FederatedIdentityService,
    FederatedUserProfile,
)
from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.api.v1.module_system.user.schema import UserOutSchema
from app.config.setting import settings
from app.core.database import async_db_session
from app.utils.hash_bcrpy_util import PwdUtil


async def _mark_user_federated(username: str) -> None:
    async with async_db_session() as db:
        user = (await db.execute(select(UserModel).where(UserModel.username == username))).scalar_one()
        user.auth_source = "federated"
        user.password_login_enabled = False
        await db.commit()


def test_user_model_defaults_to_local_password_login() -> None:
    user = UserModel(username="local", password="hash", name="Local", tenant_id=1)

    assert user.auth_source == "local"
    assert user.password_login_enabled is True


def test_user_output_defaults_to_local_password_login() -> None:
    output = UserOutSchema()

    assert output.auth_source == "local"
    assert output.password_login_enabled is True


def test_federated_identity_has_stable_unique_keys() -> None:
    constraints = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in FederatedIdentityModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("central_user_uuid", "issuer", "site_id") in constraints
    assert ("issuer", "local_user_id", "site_id") in constraints


def test_federated_user_cannot_password_login_and_creates_no_session(
    test_client,
    auth_headers: dict[str, str],
) -> None:
    username = "federated_password_login"
    password = "localPass123"
    create_resp = test_client.post(
        "/system/user/create",
        headers=auth_headers,
        json={"username": username, "password": password, "name": "联邦登录测试"},
    )
    assert create_resp.status_code == 200, create_resp.text
    asyncio.run(_mark_user_federated(username))

    redis_set_calls_before = test_client.app.state.redis.set.call_count

    response = test_client.post(
        "/system/auth/login",
        data={"username": username, "password": password, "login_type": "PC端"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["msg"] == "账号或密码错误"
    assert test_client.app.state.redis.set.call_count == redis_set_calls_before


def _claims(subject: str, *, tenant_code: str = "test", name: str = "中控用户") -> dict:
    return {
        "issuer": "https://control.example/api/v1",
        "central_user_uuid": subject,
        "name": name,
        "mobile": "13800009999",
        "email": f"{subject[:16]}@example.com",
        "avatar": "https://example.com/avatar.png",
        "status": 0,
        "site_code": "default",
        "central_tenant_code": "central-test",
        "target_tenant_code": tenant_code,
    }


def _transport(payloads: list[dict]) -> httpx.MockTransport:
    calls = iter(payloads)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://control.example/api/v1/control/sso/exchange"
        assert request.headers["authorization"] == "Basic " + base64.b64encode(b"target-client:target-secret").decode()
        return httpx.Response(200, json=next(calls))

    return httpx.MockTransport(handler)


def _enable_control_sso(monkeypatch, payloads: list[dict]) -> None:
    from app.api.v1.module_system.auth.control_sso_service import ControlSSOClientService

    monkeypatch.setattr(settings, "CONTROL_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", "https://control.example/api/v1/")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_ID", "target-client")
    monkeypatch.setattr(settings, "CONTROL_SSO_CLIENT_SECRET", "target-secret")
    monkeypatch.setattr(settings, "CONTROL_SSO_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(ControlSSOClientService, "transport", _transport(payloads))


async def _identity_snapshot(subject: str) -> tuple[UserModel, FederatedIdentityModel, int, int]:
    async with async_db_session() as db:
        identity = (
            await db.execute(
                select(FederatedIdentityModel).where(
                    FederatedIdentityModel.issuer == "https://control.example/api/v1",
                    FederatedIdentityModel.central_user_uuid == subject,
                )
            )
        ).scalar_one()
        user = (await db.execute(select(UserModel).where(UserModel.id == identity.local_user_id))).scalar_one()
        memberships = (
            await db.execute(select(func.count()).select_from(TenantUserModel).where(TenantUserModel.user_id == user.id))
        ).scalar_one()
        roles = (
            await db.execute(select(func.count()).select_from(UserRolesModel).where(UserRolesModel.user_id == user.id))
        ).scalar_one()
        return user, identity, memberships, roles


async def _set_membership_role(subject: str, role: str) -> None:
    user, _identity, _memberships, _roles = await _identity_snapshot(subject)
    async with async_db_session() as db:
        membership = (
            await db.execute(select(TenantUserModel).where(TenantUserModel.user_id == user.id))
        ).scalar_one()
        membership.role = role
        await db.commit()


async def _set_legacy_synthetic_username(subject: str) -> None:
    user, _identity, _memberships, _roles = await _identity_snapshot(subject)
    async with async_db_session() as db:
        local_user = (await db.execute(select(UserModel).where(UserModel.id == user.id))).scalar_one()
        local_user.username = "control_" + "f" * 48
        await db.commit()


async def _rollback_new_federated_identity(subject: str) -> tuple[int, int, int]:
    async with async_db_session() as db:
        user = await FederatedIdentityService.upsert_user_and_membership(
            db=db,
            site_id=1,
            issuer="https://control.example/api/v1",
            tenant_id=2,
            profile=FederatedUserProfile(
                central_user_uuid=subject,
                name="联邦负责人",
                mobile=None,
                email=None,
                avatar=None,
                status=0,
            ),
        )
        assert user.id is not None
        user_id = user.id
        await db.rollback()

    async with async_db_session() as db:
        identity_count = (
            await db.execute(
                select(func.count()).select_from(FederatedIdentityModel).where(
                    FederatedIdentityModel.issuer == "https://control.example/api/v1",
                    FederatedIdentityModel.central_user_uuid == subject,
                )
            )
        ).scalar_one()
        user_count = (
            await db.execute(select(func.count()).select_from(UserModel).where(UserModel.id == user_id))
        ).scalar_one()
        membership_count = (
            await db.execute(select(func.count()).select_from(TenantUserModel).where(TenantUserModel.user_id == user_id))
        ).scalar_one()
        return user_count, identity_count, membership_count


async def _rollback_reused_federated_identity(subject: str) -> tuple[str, int, bool]:
    initial_profile = FederatedUserProfile(
        central_user_uuid=subject,
        name="初始姓名",
        mobile=None,
        email=None,
        avatar=None,
        status=0,
    )
    async with async_db_session() as db:
        await FederatedIdentityService.upsert_user_and_membership(
            db=db,
            site_id=1,
            issuer="https://control.example/api/v1",
            tenant_id=2,
            profile=initial_profile,
        )
        await db.commit()

    async with async_db_session() as db:
        original_last_login_time = (
            await db.execute(
                select(FederatedIdentityModel.last_login_time).where(
                    FederatedIdentityModel.issuer == "https://control.example/api/v1",
                    FederatedIdentityModel.central_user_uuid == subject,
                )
            )
        ).scalar_one()
        await FederatedIdentityService.upsert_user_and_membership(
            db=db,
            site_id=1,
            issuer="https://control.example/api/v1",
            tenant_id=1,
            profile=FederatedUserProfile(
                central_user_uuid=subject,
                name="不应提交的新姓名",
                mobile="13900001111",
                email="rollback@example.com",
                avatar=None,
                status=0,
            ),
        )
        await db.rollback()

    async with async_db_session() as db:
        identity = (
            await db.execute(
                select(FederatedIdentityModel).where(
                    FederatedIdentityModel.issuer == "https://control.example/api/v1",
                    FederatedIdentityModel.central_user_uuid == subject,
                )
            )
        ).scalar_one()
        user = (await db.execute(select(UserModel).where(UserModel.id == identity.local_user_id))).scalar_one()
        membership_count = (
            await db.execute(select(func.count()).select_from(TenantUserModel).where(TenantUserModel.user_id == user.id))
        ).scalar_one()
        return user.name, membership_count, identity.last_login_time == original_last_login_time


def test_federated_identity_primitive_flushes_new_records_without_committing() -> None:
    subject = f"primitive-create-{uuid.uuid4()}"

    assert asyncio.run(_rollback_new_federated_identity(subject)) == (0, 0, 0)


def test_federated_identity_primitive_flushes_reuse_without_committing() -> None:
    subject = f"primitive-reuse-{uuid.uuid4()}"

    name, membership_count, identity_unchanged = asyncio.run(_rollback_reused_federated_identity(subject))

    assert name == "初始姓名"
    assert membership_count == 1
    assert identity_unchanged is True


def test_control_exchange_returns_404_when_disabled(test_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CONTROL_SSO_ENABLED", False, raising=False)

    response = test_client.post("/system/auth/control/exchange", json={"code": "x" * 20})

    assert response.status_code == 404
    assert response.json()["msg"] == "中控 SSO 未启用"


def test_control_exchange_rejects_missing_target_tenant(test_client, monkeypatch) -> None:
    subject = f"missing-{uuid.uuid4()}"
    _enable_control_sso(monkeypatch, [_claims(subject, tenant_code="missingtenant")])

    response = test_client.post("/system/auth/control/exchange", json={"code": "m" * 20})

    assert response.status_code == 400
    assert response.json()["msg"] == "目标租户不存在或已停用"


def test_control_exchange_accepts_standard_provider_response_envelope(test_client, monkeypatch) -> None:
    subject = f"envelope-{uuid.uuid4()}"
    _enable_control_sso(monkeypatch, [{"code": 200, "msg": "成功", "data": _claims(subject)}])

    response = test_client.post("/system/auth/control/exchange", json={"code": "w" * 20})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["access_token"]


def test_control_exchange_creates_shadow_user_membership_without_role(test_client, monkeypatch) -> None:
    subject = f"create-{uuid.uuid4()}"
    _enable_control_sso(monkeypatch, [_claims(subject)])

    response = test_client.post("/system/auth/control/exchange", json={"code": "c" * 20})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["access_token"]
    user, identity, memberships, roles = asyncio.run(_identity_snapshot(subject))
    assert identity.site_id == 1
    assert user.username.startswith("control_")
    assert len(user.username) <= 32
    assert user.username not in {subject, user.email, user.mobile}
    assert user.auth_source == "federated"
    assert user.password_login_enabled is False
    assert user.password.startswith("$pbkdf2-sha256$")
    assert PwdUtil.verify_password("unavailable-password", user.password) is False
    assert user.tenant_id == 2
    assert memberships == 1
    assert roles == 0
    current_user = test_client.get(
        "/system/user/current/info",
        headers={"Authorization": f"Bearer {response.json()['data']['access_token']}"},
    )
    assert current_user.status_code == 200, current_user.text


def test_control_exchange_reuses_identity_and_updates_profile(test_client, monkeypatch) -> None:
    subject = f"reuse-{uuid.uuid4()}"
    first = _claims(subject, name="旧昵称")
    second = _claims(subject, name="新昵称")
    second["mobile"] = "13900008888"
    second["email"] = "updated@example.com"
    _enable_control_sso(monkeypatch, [first, second])

    first_response = test_client.post("/system/auth/control/exchange", json={"code": "a" * 20})
    asyncio.run(_set_legacy_synthetic_username(subject))
    asyncio.run(_set_membership_role(subject, "owner"))
    second_response = test_client.post("/system/auth/control/exchange", json={"code": "b" * 20})

    assert first_response.status_code == second_response.status_code == 200
    user, _identity, memberships, roles = asyncio.run(_identity_snapshot(subject))
    assert user.name == "新昵称"
    assert len(user.username) <= 32
    assert user.mobile == "13900008888"
    assert user.email == "updated@example.com"
    assert memberships == 1
    assert roles == 0
    async def membership_role() -> str:
        async with async_db_session() as db:
            return (
                await db.execute(select(TenantUserModel.role).where(TenantUserModel.user_id == user.id))
            ).scalar_one()

    assert asyncio.run(membership_role()) == "owner"


def test_concurrent_exchange_creates_one_identity(test_client, monkeypatch) -> None:
    subject = f"concurrent-{uuid.uuid4()}"
    _enable_control_sso(monkeypatch, [_claims(subject), _claims(subject)])

    def exchange(code: str):
        return test_client.post("/system/auth/control/exchange", json={"code": code * 20})

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(exchange, ("d", "e")))

    assert [response.status_code for response in responses] == [200, 200]
    user, _identity, memberships, roles = asyncio.run(_identity_snapshot(subject))
    async def count_identities() -> int:
        async with async_db_session() as db:
            return (
                await db.execute(
                    select(func.count()).select_from(FederatedIdentityModel).where(
                        FederatedIdentityModel.issuer == "https://control.example/api/v1",
                        FederatedIdentityModel.central_user_uuid == subject,
                    )
                )
            ).scalar_one()

    assert asyncio.run(count_identities()) == 1
    assert memberships == 1
    assert roles == 0
