"""Business-task and target response contracts for entitlement synchronization."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlUserEntitlementTaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: int = Field(..., gt=0)
    event_id: str = Field(..., min_length=1, max_length=64)
    sync_version: int = Field(..., ge=1)
    mode: Literal["grant", "revoke", "retry", "backfill", "lifecycle"]


class ControlTargetUserEntitlementResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    disposition: Literal["applied", "replayed", "superseded"]
    status: Literal["active", "inactive"]
    applied_version: int = Field(..., ge=0)
    local_user_id: int | None = Field(default=None, gt=0)
    role_codes: list[str]
    effective_menu_count: int = Field(..., ge=0)
    session_cleanup_pending: bool
