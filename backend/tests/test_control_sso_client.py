import asyncio

from sqlalchemy import UniqueConstraint, select

from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.user.model import UserModel
from app.api.v1.module_system.user.schema import UserOutSchema
from app.core.database import async_db_session


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
