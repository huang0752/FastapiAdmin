from pydantic import BaseModel, Field


class ControlExchangeIn(BaseModel):
    code: str = Field(min_length=20, max_length=512)


class ControlIdentityClaims(BaseModel):
    issuer: str
    central_user_uuid: str
    name: str
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None
    status: int
    site_code: str
    central_tenant_code: str
    target_tenant_code: str
