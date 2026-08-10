import pytest
from fastapi.testclient import TestClient

from app.config.setting import Settings, settings
from app.core.auth_features import AuthFeatures


def test_legacy_auth_features_payload_defaults_control_sso_to_false() -> None:
    legacy_payload = {
        "register": True,
        "forgotPassword": True,
        "passwordResetMode": "email_code",
    }

    payload = AuthFeatures.model_validate(legacy_payload).model_dump(by_alias=True)

    assert payload["controlSso"] is False


def test_public_config_info_returns_auth_features_defaults(test_client: TestClient) -> None:
    old_oauth = settings.OAUTH_ENABLE
    old_github_id = settings.OAUTH_GITHUB_CLIENT_ID
    old_github_secret = settings.OAUTH_GITHUB_CLIENT_SECRET
    try:
        settings.OAUTH_ENABLE = False
        settings.OAUTH_GITHUB_CLIENT_ID = ""
        settings.OAUTH_GITHUB_CLIENT_SECRET = ""
        response = test_client.get("/system/config/info")
    finally:
        settings.OAUTH_ENABLE = old_oauth
        settings.OAUTH_GITHUB_CLIENT_ID = old_github_id
        settings.OAUTH_GITHUB_CLIENT_SECRET = old_github_secret

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    auth_features = payload["authFeatures"]
    assert payload["assembly"]["name"]
    assert auth_features == {
        "register": False,
        "forgotPassword": True,
        "passwordResetMode": "email_code",
        "oauth": False,
        "oauthProviders": [],
        "mobileLogin": False,
        "qrLogin": False,
        "rememberMe": True,
        "demoAccounts": False,
        "controlSso": False,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"CONTROL_SSO_ISSUER": ""}, "issuer"),
        ({"CONTROL_SSO_CLIENT_ID": ""}, "client ID"),
        ({"CONTROL_SSO_CLIENT_SECRET": ""}, "secret"),
        ({"CONTROL_SSO_TIMEOUT_SECONDS": 0}, "超时"),
    ],
)
def test_enabled_control_sso_requires_complete_configuration(overrides: dict, message: str) -> None:
    config = {
        "CONTROL_SSO_ENABLED": True,
        "CONTROL_SSO_ISSUER": "https://control.example/api/v1",
        "CONTROL_SSO_CLIENT_ID": "client",
        "CONTROL_SSO_CLIENT_SECRET": "secret",
        "CONTROL_SSO_TIMEOUT_SECONDS": 5,
        **overrides,
    }

    with pytest.raises(ValueError, match=message):
        Settings(**config)


def test_public_config_info_exposes_configured_oauth_provider(test_client: TestClient) -> None:
    old_oauth = settings.OAUTH_ENABLE
    old_github_id = settings.OAUTH_GITHUB_CLIENT_ID
    old_github_secret = settings.OAUTH_GITHUB_CLIENT_SECRET
    old_gitee_id = settings.OAUTH_GITEE_CLIENT_ID
    old_gitee_secret = settings.OAUTH_GITEE_CLIENT_SECRET
    try:
        settings.OAUTH_ENABLE = True
        settings.OAUTH_GITHUB_CLIENT_ID = "github-client"
        settings.OAUTH_GITHUB_CLIENT_SECRET = "github-secret"
        settings.OAUTH_GITEE_CLIENT_ID = "gitee-client"
        settings.OAUTH_GITEE_CLIENT_SECRET = ""
        response = test_client.get("/system/config/info")
    finally:
        settings.OAUTH_ENABLE = old_oauth
        settings.OAUTH_GITHUB_CLIENT_ID = old_github_id
        settings.OAUTH_GITHUB_CLIENT_SECRET = old_github_secret
        settings.OAUTH_GITEE_CLIENT_ID = old_gitee_id
        settings.OAUTH_GITEE_CLIENT_SECRET = old_gitee_secret

    assert response.status_code == 200, response.text
    auth_features = response.json()["data"]["authFeatures"]
    assert auth_features["oauth"] is True
    assert auth_features["oauthProviders"] == ["github"]


def test_tenant_register_disabled_by_default(test_client: TestClient) -> None:
    old_register = settings.AUTH_LOGIN_REGISTER_ENABLE
    settings.AUTH_LOGIN_REGISTER_ENABLE = False
    try:
        response = test_client.post(
            "/system/auth/tenant/register",
            json={"username": "disabled_register", "password": "admin123", "email": "disabled@example.com"},
        )
    finally:
        settings.AUTH_LOGIN_REGISTER_ENABLE = old_register

    assert response.status_code == 404


def test_password_reset_email_disabled_when_mode_is_disabled(test_client: TestClient) -> None:
    old_enabled = settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE
    old_mode = settings.AUTH_PASSWORD_RESET_MODE
    settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = True
    settings.AUTH_PASSWORD_RESET_MODE = "disabled"
    try:
        response = test_client.post(
            "/system/user/password/forget/email-code",
            json={"username": "user", "email": "user@example.com"},
        )
    finally:
        settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = old_enabled
        settings.AUTH_PASSWORD_RESET_MODE = old_mode

    assert response.status_code == 404


def test_legacy_password_reset_disabled_by_default(test_client: TestClient) -> None:
    old_enabled = settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE
    old_mode = settings.AUTH_PASSWORD_RESET_MODE
    settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = True
    settings.AUTH_PASSWORD_RESET_MODE = "email_code"
    try:
        response = test_client.post(
            "/system/user/password/forget",
            json={"username": "user", "mobile": "13800000000", "new_password": "newUser123"},
        )
    finally:
        settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = old_enabled
        settings.AUTH_PASSWORD_RESET_MODE = old_mode

    assert response.status_code == 404


def test_oauth_supported_provider_requires_credentials(test_client: TestClient) -> None:
    old_oauth = settings.OAUTH_ENABLE
    old_github_id = settings.OAUTH_GITHUB_CLIENT_ID
    old_github_secret = settings.OAUTH_GITHUB_CLIENT_SECRET
    settings.OAUTH_ENABLE = True
    settings.OAUTH_GITHUB_CLIENT_ID = ""
    settings.OAUTH_GITHUB_CLIENT_SECRET = ""
    try:
        response = test_client.get(
            "/system/auth/oauth/github/login",
            params={"redirect_uri": settings.OAUTH_FRONTEND_FALLBACK},
            follow_redirects=False,
        )
    finally:
        settings.OAUTH_ENABLE = old_oauth
        settings.OAUTH_GITHUB_CLIENT_ID = old_github_id
        settings.OAUTH_GITHUB_CLIENT_SECRET = old_github_secret

    assert response.status_code == 404
