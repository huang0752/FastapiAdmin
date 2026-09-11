"""Schemas for Control application administration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Query
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.common.enums import EnvironmentEnum, QueueEnum
from app.config.setting import settings
from app.core.base_params import BaseQueryParam
from app.core.base_schema import BaseSchema


def _validate_application_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("应用地址必须是绝对 HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("应用地址不能包含用户凭据")
    if settings.ENVIRONMENT == EnvironmentEnum.PROD and parsed.scheme != "https":
        raise ValueError("生产环境应用地址必须使用 HTTPS")
    return normalized


def validate_provisioning_configuration(*, enabled: bool, provisioning_url: str | None) -> None:
    if not enabled:
        return
    if not provisioning_url:
        raise ValueError("启用自动开户时必须配置目标租户开户地址")
    _validate_application_url(provisioning_url)


def validate_entitlement_configuration(*, enabled: bool, sync_url: str | None) -> None:
    if enabled and not sync_url:
        raise ValueError("启用用户授权同步时必须配置目标接口地址")
    if sync_url:
        _validate_entitlement_url(sync_url)


def _validate_entitlement_url(value: str) -> str:
    normalized = _validate_application_url(value)
    parsed = urlsplit(normalized)
    if parsed.query or parsed.fragment:
        raise ValueError("授权同步接口不能包含查询参数或片段")
    return normalized


class ControlApplicationBaseSchema(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=500)
    base_url: str = Field(..., max_length=500)
    callback_url: str = Field(..., max_length=500)
    provisioning_url: str | None = Field(default=None, max_length=500)
    provisioning_enabled: bool = False
    provisioning_timeout_seconds: int = Field(default=10, ge=1, le=60)
    entitlement_sync_url: str | None = Field(default=None, max_length=500)
    entitlement_sync_enabled: bool = False
    entitlement_sync_timeout_seconds: int = Field(default=10, ge=1, le=60)
    status: int = Field(default=0, ge=0, le=1)
    sort: int = Field(default=0, ge=0)

    @field_validator("entitlement_sync_url")
    @classmethod
    def validate_entitlement_url(cls, value: str | None) -> str | None:
        return _validate_entitlement_url(value) if value is not None else None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not normalized.replace("-", "").replace("_", "").isalnum():
            raise ValueError("应用编码仅允许字母、数字、横线和下划线")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("应用名称不能为空")
        return normalized

    @field_validator("base_url", "callback_url", "provisioning_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_application_url(value) if value is not None else None

    @model_validator(mode="after")
    def validate_provisioning_configuration(self) -> ControlApplicationBaseSchema:
        validate_provisioning_configuration(
            enabled=self.provisioning_enabled,
            provisioning_url=self.provisioning_url,
        )
        validate_entitlement_configuration(enabled=self.entitlement_sync_enabled, sync_url=self.entitlement_sync_url)
        return self


class ControlApplicationCreateSchema(ControlApplicationBaseSchema):
    pass


class ControlApplicationUpdateSchema(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=500)
    base_url: str | None = Field(default=None, max_length=500)
    callback_url: str | None = Field(default=None, max_length=500)
    provisioning_url: str | None = Field(default=None, max_length=500)
    provisioning_enabled: bool | None = None
    provisioning_timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    entitlement_sync_url: str | None = Field(default=None, max_length=500)
    entitlement_sync_enabled: bool | None = None
    entitlement_sync_timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    status: int | None = Field(default=None, ge=0, le=1)
    sort: int | None = Field(default=None, ge=0)

    @field_validator("entitlement_sync_url")
    @classmethod
    def validate_entitlement_url(cls, value: str | None) -> str | None:
        return _validate_entitlement_url(value) if value is not None else None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str | None) -> str | None:
        return ControlApplicationBaseSchema.validate_code(value) if value is not None else None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return ControlApplicationBaseSchema.validate_name(value) if value is not None else None

    @field_validator("base_url", "callback_url", "provisioning_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_application_url(value) if value is not None else None


class ControlApplicationOutSchema(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    site_id: int
    code: str
    name: str
    description: str | None = None
    icon: str | None = None
    base_url: str
    callback_url: str
    provisioning_url: str | None = None
    provisioning_enabled: bool
    provisioning_timeout_seconds: int
    entitlement_sync_url: str | None = None
    entitlement_sync_enabled: bool
    entitlement_sync_timeout_seconds: int
    client_id: str
    status: int
    sort: int


class ControlApplicationSecretSchema(ControlApplicationOutSchema):
    client_secret: str


class ControlClientSecretResultSchema(BaseModel):
    client_id: str
    client_secret: str


class ControlTenantApplicationCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(..., gt=0)
    application_id: int = Field(..., gt=0)
    target_tenant_code: str = Field(..., min_length=1, max_length=100)
    status: int = Field(default=0, ge=0, le=1)

    @field_validator("target_tenant_code")
    @classmethod
    def validate_target_tenant_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or not normalized.isalnum():
            raise ValueError("目标租户编码仅允许字母和数字")
        return normalized


class ControlTenantApplicationUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_tenant_code: str | None = Field(default=None, min_length=1, max_length=100)
    status: int | None = Field(default=None, ge=0, le=1)

    @field_validator("target_tenant_code")
    @classmethod
    def validate_target_tenant_code(cls, value: str | None) -> str | None:
        return ControlTenantApplicationCreateSchema.validate_target_tenant_code(value) if value is not None else None


class ControlTenantApplicationOutSchema(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    site_id: int
    tenant_id: int
    application_id: int
    target_tenant_code: str
    status: int


class ControlTenantAvailableApplicationSchema(BaseModel):
    tenant_application_id: int
    application_id: int
    application_code: str
    application_name: str
    target_tenant_code: str
    status: int


class ControlUserApplicationGrantOutSchema(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    site_id: int
    tenant_application_id: int
    tenant_id: int
    user_id: int
    status: int
    desired_state: Literal["active", "inactive"]
    sync_status: Literal["pending", "processing", "succeeded", "failed"]
    sync_version: int
    error: str | None = Field(default=None, validation_alias=AliasChoices("error", "last_error_message"))

    @computed_field
    @property
    def grant_id(self) -> int | None:
        return self.id

    @computed_field
    @property
    def launchable(self) -> bool:
        return self.desired_state == "active" and self.sync_status == "succeeded"


class ControlGrantMemberSchema(BaseModel):
    user_id: int
    username: str
    name: str
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None
    granted: bool
    grant_id: int | None = None
    desired_state: Literal["active", "inactive"] | None = None
    sync_status: Literal["pending", "processing", "succeeded", "failed"] | None = None
    sync_version: int = 0
    error: str | None = None
    launchable: bool = False


class ControlPortalApplicationSchema(BaseModel):
    code: str
    name: str
    description: str | None = None
    icon: str | None = None
    base_url: str
    status: int
    sort: int
    grant_id: int
    desired_state: Literal["active", "inactive"]
    sync_status: Literal["pending", "processing", "succeeded", "failed"]
    sync_version: int
    error: str | None = None
    launchable: bool


class ControlLaunchResultSchema(BaseModel):
    redirect_url: str
    expires_at: datetime


class ControlExchangeInSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=20, max_length=512)


class ControlIdentityClaimsSchema(BaseModel):
    issuer: str
    central_user_uuid: str
    name: str
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None
    status: int
    site_code: str
    central_tenant_code: str
    central_tenant_role: Literal["owner", "admin", "member"]
    central_is_superuser: bool
    target_tenant_code: str


@dataclass
class ControlApplicationQueryParam(BaseQueryParam):
    code: str | None = Query(default=None)
    name: str | None = Query(default=None)
    status: int | None = Query(default=None, ge=0, le=1)

    def __post_init__(self) -> None:
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
