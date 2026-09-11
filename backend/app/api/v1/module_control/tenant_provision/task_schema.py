"""Schemas for Control tenant-provision orchestration and worker tasks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.v1.module_platform.tenant.schema import TenantCreateSchema, TenantOutSchema

from .schema import ControlTenantProvisionOutSchema


class ControlTenantProvisionApplicationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: int = Field(..., gt=0)
    application_package_id: int = Field(..., gt=0)
    desired_target_tenant_code: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("desired_target_tenant_code")
    @classmethod
    def normalize_target_tenant_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or not normalized.isascii() or not normalized.isalnum():
            raise ValueError("目标租户编码仅允许字母和数字")
        return normalized


class ControlTenantWithProvisionsCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: TenantCreateSchema
    applications: list[ControlTenantProvisionApplicationSelection] = Field(..., min_length=1)

    @field_validator("tenant")
    @classmethod
    def require_central_package(cls, value: TenantCreateSchema) -> TenantCreateSchema:
        if value.package_id is None:
            raise ValueError("开通产品前请选择中控套餐")
        return value


class ControlTenantWithProvisionsOutSchema(BaseModel):
    tenant: TenantOutSchema
    provisions: list[ControlTenantProvisionOutSchema]


class ControlTenantProvisionTaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provision_id: int = Field(..., gt=0)
    mode: Literal["initial", "retry", "reconcile"]


class ControlTargetProvisionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: Literal["created", "already_exists"]
    target_tenant_id: int = Field(..., gt=0)
    target_tenant_uuid: str = Field(..., min_length=32, max_length=64)
    target_tenant_code: str = Field(..., min_length=1, max_length=100)
