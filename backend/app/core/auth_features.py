from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config.setting import settings

OAuthProvider = Literal["wechat", "qq", "github", "gitee"]
PasswordResetMode = Literal["email_code", "legacy_mobile", "disabled"]


class AuthFeatures(BaseModel):
    """Public login/auth feature switches consumed by the frontend."""

    model_config = ConfigDict(populate_by_name=True)

    register_: bool = Field(default=False, alias="register", description="租户自助注册")
    forgotPassword: bool = Field(default=True, description="忘记密码入口")
    passwordResetMode: PasswordResetMode = Field(default="email_code", description="忘记密码模式")
    oauth: bool = Field(default=False, description="第三方 OAuth 登录")
    oauthProviders: list[OAuthProvider] = Field(default_factory=list, description="启用的 OAuth 提供方")
    mobileLogin: bool = Field(default=False, description="手机号登录入口")
    qrLogin: bool = Field(default=False, description="扫码登录入口")
    rememberMe: bool = Field(default=True, description="记住密码")
    demoAccounts: bool = Field(default=False, description="演示快捷账号")
    controlSso: bool = Field(default=False, description="中控统一登录")


_OAUTH_PROVIDER_CREDENTIALS: dict[OAuthProvider, tuple[str, str]] = {
    "wechat": ("OAUTH_WECHAT_OPEN_APP_ID", "OAUTH_WECHAT_OPEN_APP_SECRET"),
    "qq": ("OAUTH_QQ_APP_ID", "OAUTH_QQ_APP_SECRET"),
    "github": ("OAUTH_GITHUB_CLIENT_ID", "OAUTH_GITHUB_CLIENT_SECRET"),
    "gitee": ("OAUTH_GITEE_CLIENT_ID", "OAUTH_GITEE_CLIENT_SECRET"),
}


def get_configured_oauth_providers() -> list[OAuthProvider]:
    if not settings.OAUTH_ENABLE:
        return []
    providers: list[OAuthProvider] = []
    for provider, (client_id_key, client_secret_key) in _OAUTH_PROVIDER_CREDENTIALS.items():
        if getattr(settings, client_id_key, "") and getattr(settings, client_secret_key, ""):
            providers.append(provider)
    return providers


def get_auth_features() -> AuthFeatures:
    reset_mode = settings.AUTH_PASSWORD_RESET_MODE
    forgot_password = settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE and reset_mode != "disabled"
    providers = get_configured_oauth_providers()
    return AuthFeatures(
        register=settings.AUTH_LOGIN_REGISTER_ENABLE,
        forgotPassword=forgot_password,
        passwordResetMode=reset_mode,
        oauth=settings.OAUTH_ENABLE and bool(providers),
        oauthProviders=providers,
        mobileLogin=settings.AUTH_LOGIN_MOBILE_ENABLE,
        qrLogin=settings.AUTH_LOGIN_QR_ENABLE,
        rememberMe=settings.AUTH_LOGIN_REMEMBER_ME_ENABLE,
        demoAccounts=settings.AUTH_LOGIN_DEMO_ACCOUNTS_ENABLE,
        controlSso=settings.CONTROL_SSO_ENABLED,
    )


def require_tenant_register_enabled() -> None:
    if not settings.AUTH_LOGIN_REGISTER_ENABLE:
        raise HTTPException(status_code=404, detail="租户自助注册未启用")


def require_password_reset_mode(mode: PasswordResetMode) -> None:
    if not settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE or settings.AUTH_PASSWORD_RESET_MODE != mode:
        raise HTTPException(status_code=404, detail="忘记密码能力未启用")


def require_oauth_provider_enabled(provider: str) -> OAuthProvider:
    if not settings.OAUTH_ENABLE:
        raise HTTPException(status_code=404, detail="OAuth 登录未启用")
    if provider not in _OAUTH_PROVIDER_CREDENTIALS:
        raise HTTPException(status_code=404, detail="不支持的 OAuth 渠道")
    configured = get_configured_oauth_providers()
    if provider not in configured:
        raise HTTPException(status_code=404, detail="OAuth 渠道未配置")
    return provider  # type: ignore[return-value]
