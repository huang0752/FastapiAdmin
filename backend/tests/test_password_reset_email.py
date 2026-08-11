import asyncio
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.module_system.user.model import UserModel
from app.config.setting import settings
from app.core.database import async_db_session


async def _mark_user_federated(username: str) -> None:
    async with async_db_session() as db:
        user = (await db.execute(select(UserModel).where(UserModel.username == username))).scalar_one()
        user.auth_source = "federated"
        user.password_login_enabled = False
        await db.commit()


def test_password_reset_email_code_sends_generic_response(
    test_client: TestClient,
    monkeypatch,
) -> None:
    sent: list[dict[str, Any]] = []

    async def fake_send_by_template(self, **kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.api.v1.module_platform.email.service.EmailSendService.send_by_template",
        fake_send_by_template,
    )

    resp = test_client.post(
        "/system/user/password/forget/email-code",
        json={"username": "test_admin", "email": "test@fastapiadmin.com"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["msg"] == "如账号邮箱匹配，验证码已发送"
    assert sent
    assert sent[0]["to_email"] == "test@fastapiadmin.com"
    assert sent[0]["template_code"] == "reset_password_code"
    assert sent[0]["biz_type"] == "reset_password"


def test_password_reset_email_flow_resets_password(
    test_client: TestClient,
    monkeypatch,
) -> None:
    captured_code: dict[str, str] = {}

    async def fake_send_by_template(self, **kwargs):
        captured_code["code"] = kwargs["variables"]["code"]
        return True

    monkeypatch.setattr(
        "app.api.v1.module_platform.email.service.EmailSendService.send_by_template",
        fake_send_by_template,
    )

    send_resp = test_client.post(
        "/system/user/password/forget/email-code",
        json={"username": "user", "email": "user@example.com"},
    )
    assert send_resp.status_code == 200, send_resp.text

    reset_resp = test_client.post(
        "/system/user/password/forget/email-reset",
        json={
            "username": "user",
            "email": "user@example.com",
            "code": captured_code["code"],
            "new_password": "newUser123",
        },
    )

    assert reset_resp.status_code == 200, reset_resp.text
    assert reset_resp.json()["msg"] == "重置密码成功"

    login_resp = test_client.post(
        "/system/auth/login",
        data={"username": "user", "password": "newUser123"},
    )
    assert login_resp.status_code == 200, login_resp.text


def test_password_reset_email_rate_limited(test_client: TestClient, monkeypatch) -> None:
    async def fake_send_by_template(self, **kwargs):
        return True

    monkeypatch.setattr(
        "app.api.v1.module_platform.email.service.EmailSendService.send_by_template",
        fake_send_by_template,
    )

    first = test_client.post(
        "/system/user/password/forget/email-code",
        json={"username": "test_user", "email": "test2@fastapiadmin.com"},
    )
    second = test_client.post(
        "/system/user/password/forget/email-code",
        json={"username": "test_user", "email": "test2@fastapiadmin.com"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 400
    assert "60 秒" in second.json()["msg"]


def test_federated_user_email_reset_keeps_generic_response_but_does_not_send(
    test_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    username = "federated_email_send"
    email = "federated-email-send@example.com"
    create_resp = test_client.post(
        "/system/user/create",
        headers=auth_headers,
        json={
            "username": username,
            "password": "localPass123",
            "name": "联邦邮箱发送测试",
            "email": email,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    asyncio.run(_mark_user_federated(username))
    sent: list[dict[str, Any]] = []

    async def fake_send_by_template(self, **kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.api.v1.module_platform.email.service.EmailSendService.send_by_template",
        fake_send_by_template,
    )

    response = test_client.post(
        "/system/user/password/forget/email-code",
        json={"username": username, "email": email},
    )

    assert response.status_code == 200, response.text
    assert response.json()["msg"] == "如账号邮箱匹配，验证码已发送"
    assert sent == []


def test_federated_user_cannot_complete_legacy_password_reset(
    test_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    username = "federated_email_reset"
    email = "federated-email-reset@example.com"
    create_resp = test_client.post(
        "/system/user/create",
        headers=auth_headers,
        json={
            "username": username,
            "password": "localPass123",
            "name": "联邦邮箱重置测试",
            "email": email,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    captured_code: dict[str, str] = {}

    async def fake_send_by_template(self, **kwargs):
        captured_code["code"] = kwargs["variables"]["code"]
        return True

    monkeypatch.setattr(
        "app.api.v1.module_platform.email.service.EmailSendService.send_by_template",
        fake_send_by_template,
    )
    send_resp = test_client.post(
        "/system/user/password/forget/email-code",
        json={"username": username, "email": email},
    )
    assert send_resp.status_code == 200, send_resp.text
    asyncio.run(_mark_user_federated(username))

    response = test_client.post(
        "/system/user/password/forget/email-reset",
        json={
            "username": username,
            "email": email,
            "code": captured_code["code"],
            "new_password": "newLocalPass123",
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["msg"] == "统一登录账号不支持本地密码操作"


def test_oauth_login_route_is_disabled_by_default(test_client: TestClient) -> None:
    old = settings.OAUTH_ENABLE
    settings.OAUTH_ENABLE = False
    try:
        resp = test_client.get(
            "/system/auth/oauth/github/login",
            params={"redirect_uri": settings.OAUTH_FRONTEND_FALLBACK},
            follow_redirects=False,
        )
    finally:
        settings.OAUTH_ENABLE = old

    assert resp.status_code == 404
