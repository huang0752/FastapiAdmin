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


class ControlTenantProvisionIn(BaseModel):
    code: str = Field(..., min_length=20, max_length=512)


class ControlTenantProvisionOwnerClaim(BaseModel):
    central_user_uuid: str
    username: str
    name: str
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None
    status: int


class ControlTenantProvisionClaims(BaseModel):
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
    owner: ControlTenantProvisionOwnerClaim
    issuer: str


class ControlTenantProvisionOut(BaseModel):
    result: str
    target_tenant_id: int
    target_tenant_uuid: str
    target_tenant_code: str
