from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam
from app.core.base_schema import BaseSchema

from .host import normalize_host


class SiteDomainSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    host: str = Field(..., max_length=255)
    is_primary: bool = False

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        return normalize_host(value)


class SiteBaseSchema(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    domains: list[SiteDomainSchema] = Field(..., min_length=1)
    logo_url: str | None = Field(default=None, max_length=500)
    favicon: str | None = Field(default=None, max_length=500)
    login_bg: str | None = Field(default=None, max_length=500)
    copyright: str | None = Field(default=None, max_length=255)
    keep_record: str | None = Field(default=None, max_length=100)
    help_doc: str | None = Field(default=None, max_length=500)
    privacy: str | None = Field(default=None, max_length=500)
    clause: str | None = Field(default=None, max_length=500)
    status: int = Field(default=0, ge=0, le=1)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not normalized.replace("_", "").isalnum():
            raise ValueError("站点编码仅允许字母、数字和下划线")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("站点名称不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_domains(self):
        hosts = [item.host for item in self.domains]
        if len(hosts) != len(set(hosts)):
            raise ValueError("同一站点不能配置重复域名")
        if sum(item.is_primary for item in self.domains) != 1:
            raise ValueError("站点必须且只能设置一个主域名")
        return self


class SiteCreateSchema(SiteBaseSchema):
    pass


class SiteUpdateSchema(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    domains: list[SiteDomainSchema] | None = Field(default=None, min_length=1)
    logo_url: str | None = Field(default=None, max_length=500)
    favicon: str | None = Field(default=None, max_length=500)
    login_bg: str | None = Field(default=None, max_length=500)
    copyright: str | None = Field(default=None, max_length=255)
    keep_record: str | None = Field(default=None, max_length=100)
    help_doc: str | None = Field(default=None, max_length=500)
    privacy: str | None = Field(default=None, max_length=500)
    clause: str | None = Field(default=None, max_length=500)
    status: int | None = Field(default=None, ge=0, le=1)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return SiteBaseSchema.validate_code(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return SiteBaseSchema.validate_name(value)

    @model_validator(mode="after")
    def validate_domains(self):
        if self.domains is None:
            return self
        hosts = [item.host for item in self.domains]
        if len(hosts) != len(set(hosts)):
            raise ValueError("同一站点不能配置重复域名")
        if sum(item.is_primary for item in self.domains) != 1:
            raise ValueError("站点必须且只能设置一个主域名")
        return self


class SiteOutSchema(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    site_code: str = Field(validation_alias="code")
    name: str
    domains: list[SiteDomainSchema]
    logo_url: str | None = None
    favicon: str | None = None
    login_bg: str | None = None
    copyright: str | None = None
    keep_record: str | None = None
    help_doc: str | None = None
    privacy: str | None = None
    clause: str | None = None
    status: int


class SitePublicConfigSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    site_code: str = Field(validation_alias="code")
    name: str
    logo_url: str | None = None
    favicon: str | None = None
    login_bg: str | None = None
    copyright: str | None = None
    keep_record: str | None = None
    help_doc: str | None = None
    privacy: str | None = None
    clause: str | None = None
    status: int


@dataclass
class SiteQueryParam(BaseQueryParam):
    name: str | None = Query(None)
    code: str | None = Query(None)
    status: int | None = Query(None, ge=0, le=1)

    def __post_init__(self) -> None:
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
