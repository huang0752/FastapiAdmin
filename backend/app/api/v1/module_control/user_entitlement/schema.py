"""Contracts for one-time Control user-entitlement claim exchange."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.v1.module_control.schema import ControlUserApplicationGrantOutSchema
from app.api.v1.module_system.user.schema import UserCreateSchema, UserOutSchema


class ControlOrdinaryUserCreateIn(UserCreateSchema):
    """Required identity fields for the Control two-step workflow."""

    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=32)


class ControlUserCreateIn(BaseModel):
    """Create one ordinary central user and at least one product entitlement."""

    model_config = ConfigDict(extra="forbid")

    user: ControlOrdinaryUserCreateIn
    tenant_application_ids: list[int] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_ordinary_user_and_products(self) -> "ControlUserCreateIn":
        if self.user.is_superuser:
            raise ValueError("组合建用户仅允许创建普通用户")
        if self.user.role_ids:
            raise ValueError("组合建用户不提供角色配置")
        if self.user.tenant_id is not None:
            raise ValueError("组合建用户仅使用当前租户上下文")
        if any(item <= 0 for item in self.tenant_application_ids):
            raise ValueError("产品开通记录无效")
        if len(set(self.tenant_application_ids)) != len(self.tenant_application_ids):
            raise ValueError("同一产品不能重复选择")
        return self


class ControlUserCreateOut(BaseModel):
    user: UserOutSchema
    entitlements: list[ControlUserApplicationGrantOutSchema]


class ControlUserEntitlementExchangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Keep the raw value out of Pydantic validation errors/logs. The controller
    # validates length and type without echoing this bearer credential.
    code: Any = Field(
        ...,
        json_schema_extra={"type": "string", "minLength": 20, "maxLength": 512},
    )


class ControlUserAccessClaims(BaseModel):
    """Must remain wire-compatible with Products ControlUserAccessClaims."""

    model_config = ConfigDict(extra="forbid")

    issuer: str = Field(min_length=1, max_length=500)
    event_id: str = Field(min_length=1, max_length=64)
    sync_version: int = Field(ge=1)
    desired_state: Literal["active", "inactive"]
    application_code: str = Field(min_length=1, max_length=100)
    site_code: str = Field(min_length=1, max_length=64)
    central_tenant_uuid: str = Field(min_length=1, max_length=64)
    central_tenant_code: str = Field(min_length=1, max_length=100)
    target_tenant_code: str = Field(min_length=1, max_length=100)
    central_user_uuid: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    mobile: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=128)
    avatar: str | None = Field(default=None, max_length=500)
    user_status: Literal[0, 1]
