"""Schemas for Control tenant provisioning and one-time claim exchange."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam
from app.core.base_schema import BaseSchema


def _normalize_target_tenant_code(value: str) -> str:
    normalized = value.strip()
    if not normalized or not normalized.isalnum() or not normalized.isascii():
        raise ValueError("目标租户编码仅允许字母和数字")
    return normalized


class ControlTenantProvisionCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(..., gt=0)
    application_id: int = Field(..., gt=0)
    application_package_id: int = Field(..., gt=0)
    owner_user_id: int = Field(..., gt=0)
    provision_request_uuid: str = Field(..., min_length=32, max_length=64)
    desired_target_tenant_code: str = Field(..., min_length=1, max_length=100)

    @field_validator("desired_target_tenant_code")
    @classmethod
    def normalize_target_tenant_code(cls, value: str) -> str:
        return _normalize_target_tenant_code(value)


class ControlTenantProvisionUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_package_id: int | None = Field(default=None, gt=0)
    desired_target_tenant_code: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("desired_target_tenant_code")
    @classmethod
    def normalize_target_tenant_code(cls, value: str | None) -> str | None:
        return _normalize_target_tenant_code(value) if value is not None else None


class ControlTenantProvisionOutSchema(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    site_id: int
    tenant_id: int
    application_id: int
    application_package_id: int
    tenant_application_id: int | None = None
    owner_user_id: int
    provision_request_uuid: str
    desired_target_tenant_code: str
    target_tenant_code: str | None = None
    target_tenant_uuid: str | None = None
    status: str
    attempt_count: int
    max_attempts: int
    last_error_code: str | None = None
    last_error_message: str | None = None
    next_retry_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class ControlTenantProvisionQueryParam(BaseQueryParam):
    tenant_id: int | None = Query(default=None, gt=0)
    application_id: int | None = Query(default=None, gt=0)
    status: str | None = Query(default=None, pattern=r"^(pending|processing|succeeded|failed)$")

    def __post_init__(self) -> None:
        if isinstance(self.tenant_id, int):
            self.tenant_id = (QueueEnum.eq.value, self.tenant_id)
        if isinstance(self.application_id, int):
            self.application_id = (QueueEnum.eq.value, self.application_id)
        if self.status:
            self.status = (QueueEnum.eq.value, self.status)


class ControlTenantProvisionResultSchema(BaseModel):
    provisions: list[ControlTenantProvisionOutSchema]


class ControlProvisionExchangeInSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Any = Field(
        ...,
        json_schema_extra={"type": "string", "minLength": 20, "maxLength": 512},
    )


class ControlProvisionOwnerClaimSchema(BaseModel):
    central_user_uuid: str
    username: str
    name: str
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None
    status: int


class ControlProvisionExchangeClaimsSchema(BaseModel):
    provision_request_uuid: str
    central_tenant_uuid: str
    central_tenant_code: str
    tenant_name: str
    unified_social_credit_code: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    address: str | None = None
    site_code: str
    target_tenant_code: str
    target_package_code: str
    owner: ControlProvisionOwnerClaimSchema
    issuer: str
