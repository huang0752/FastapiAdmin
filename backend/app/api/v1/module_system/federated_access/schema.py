from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlUserAccessSyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=20, max_length=512)


class ControlUserAccessClaims(BaseModel):
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


class ControlUserAccessSyncOut(BaseModel):
    disposition: Literal["applied", "replayed", "superseded"]
    status: Literal["active", "inactive"]
    applied_version: int
    local_user_id: int | None
    role_codes: list[str]
    effective_menu_count: int
    session_cleanup_pending: bool
