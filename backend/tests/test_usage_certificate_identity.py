from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.api.v1.module_platform.tenant.model import TenantStatus
from app.api.v1.module_platform.usage_certificate.identity import build_usage_certificate_identity
from app.api.v1.module_platform.usage_certificate.service import UsageCertificateService


def test_build_usage_certificate_identity_is_product_neutral_and_unique() -> None:
    first = build_usage_certificate_identity("ACME")
    second = build_usage_certificate_identity("ACME")

    assert first.number.startswith("FA-SW-ACME-")
    assert len(first.number.rsplit("-", 1)[1]) == 6
    assert len(first.token) >= 43
    assert "=" not in first.token
    assert first != second


def test_usage_certificate_migration_backfills_before_non_null() -> None:
    path = Path(__file__).parents[1] / "app/alembic/versions/20260812_01_add_tenant_usage_certificate.py"
    source = path.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "20260811_01"' in source
    assert "usage_certificate_no" in source
    assert "usage_certificate_token" in source
    assert "usage_certificate_created_at" in source
    assert source.index("UPDATE platform_tenant") < source.index("nullable=False")


def test_build_view_uses_live_fields_and_optional_request_ip() -> None:
    now = datetime.now()
    tenant = SimpleNamespace(
        id=2,
        name="示例企业",
        code="ACME",
        unified_social_credit_code=None,
        start_time=now - timedelta(days=1),
        end_time=now + timedelta(days=1),
        status=TenantStatus.ACTIVE,
        is_deleted=False,
        usage_certificate_no="FA-SW-ACME-A7K9Q2",
        usage_certificate_token="token",
        usage_certificate_created_at=now,
    )

    view = UsageCertificateService.build_view(
        tenant,
        now=now,
        request_ip="192.0.2.10",
        include_request_ip=True,
        public_origin="",
        system_name="FastapiAdmin",
        system_version="1.0.0",
    )

    assert view.enterprise_name == "示例企业"
    assert view.social_credit_code == "-"
    assert view.request_ip == "192.0.2.10"
    assert view.currently_valid is True
    assert view.qr_data_url is None


def test_build_view_marks_suspended_tenant_invalid_and_hides_ip() -> None:
    now = datetime.now()
    tenant = SimpleNamespace(
        id=2,
        name="示例企业",
        code="ACME",
        unified_social_credit_code="91110000XXXXXXXXXX",
        start_time=None,
        end_time=None,
        status=TenantStatus.SUSPENDED,
        is_deleted=False,
        usage_certificate_no="FA-SW-ACME-A7K9Q2",
        usage_certificate_token="token",
        usage_certificate_created_at=now,
    )

    view = UsageCertificateService.build_view(
        tenant,
        now=now,
        request_ip="192.0.2.10",
        include_request_ip=False,
        public_origin="https://example.test",
        system_name="FastapiAdmin",
        system_version="1.0.0",
    )

    assert view.currently_valid is False
    assert view.start_time == "-"
    assert view.end_time == "-"
    assert view.request_ip is None
    assert view.verify_url == "https://example.test/#/certificate/verify/token"
