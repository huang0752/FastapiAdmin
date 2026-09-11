"""Schemas for Control application-package mappings."""

from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam
from app.core.base_schema import BaseSchema


def _normalize_package_code(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or not normalized.isalnum() or not normalized.isascii():
        raise ValueError("套餐编码仅允许小写字母和数字")
    return normalized


class ControlApplicationPackageCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: int = Field(..., gt=0)
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    target_package_code: str = Field(..., min_length=1, max_length=100)
    is_default: bool = False
    status: int = Field(default=0, ge=0, le=1)
    sort: int = Field(default=0, ge=0)

    @field_validator("code", "target_package_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return _normalize_package_code(value)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("套餐名称不能为空")
        return normalized


class ControlApplicationPackageUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    target_package_code: str | None = Field(default=None, min_length=1, max_length=100)
    is_default: bool | None = None
    status: int | None = Field(default=None, ge=0, le=1)
    sort: int | None = Field(default=None, ge=0)

    @field_validator("code", "target_package_code")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        return _normalize_package_code(value) if value is not None else None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return ControlApplicationPackageCreateSchema.normalize_name(value) if value is not None else None


class ControlApplicationPackageOutSchema(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    site_id: int
    application_id: int
    code: str
    name: str
    description: str | None = None
    target_package_code: str
    is_default: bool
    status: int
    sort: int


@dataclass
class ControlApplicationPackageQueryParam(BaseQueryParam):
    application_id: int | None = Query(default=None, gt=0)
    code: str | None = Query(default=None)
    name: str | None = Query(default=None)
    status: int | None = Query(default=None, ge=0, le=1)

    def __post_init__(self) -> None:
        if isinstance(self.application_id, int):
            self.application_id = (QueueEnum.eq.value, self.application_id)
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
