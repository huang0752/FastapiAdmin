from pydantic import BaseModel, Field


class UsageCertificateView(BaseModel):
    tenant_id: int
    certificate_no: str
    enterprise_name: str
    tenant_code: str
    social_credit_code: str
    system_name: str
    system_version: str
    start_time: str
    end_time: str
    currently_valid: bool
    status_label: str
    certificate_created_at: str
    request_ip: str | None = None
    verify_url: str | None = None
    qr_data_url: str | None = None


class UsageCertificatePreviewOut(BaseModel):
    certificate_no: str
    filename: str
    generated_at: str
    html: str


class UsageCertificatePublicOut(BaseModel):
    certificate_no: str
    enterprise_name: str
    social_credit_code: str
    system_name: str
    system_version: str
    start_time: str
    end_time: str
    currently_valid: bool
    status_label: str


class UsageCertificatePlatformItem(UsageCertificatePublicOut):
    tenant_id: int
    tenant_code: str
    certificate_created_at: str


class UsageCertificatePlatformPage(BaseModel):
    items: list[UsageCertificatePlatformItem] = Field(default_factory=list)
    total: int = 0
    page_no: int = 1
    page_size: int = 20
