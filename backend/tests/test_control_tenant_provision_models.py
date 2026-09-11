"""Control tenant-provisioning persistence and schema contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.api.v1.module_control.application_package.model import ControlApplicationPackageModel
from app.api.v1.module_control.application_package.schema import (
    ControlApplicationPackageCreateSchema,
    ControlApplicationPackageOutSchema,
    ControlApplicationPackageUpdateSchema,
)
from app.api.v1.module_control.model import ControlApplicationModel
from app.api.v1.module_control.schema import (
    ControlApplicationCreateSchema,
    ControlApplicationOutSchema,
    ControlApplicationUpdateSchema,
)
from app.api.v1.module_control.tenant_provision.model import (
    ControlTenantProvisionModel,
    ControlTenantProvisionTicketModel,
)
from app.api.v1.module_control.tenant_provision.schema import (
    ControlProvisionExchangeClaimsSchema,
    ControlTenantProvisionOutSchema,
)
from app.common.enums import EnvironmentEnum
from app.config.setting import settings


def _unique_columns(model: type) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key(model: type, column_name: str) -> tuple[str, str | None, str | None]:
    foreign_keys = list(model.__table__.c[column_name].foreign_keys)
    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    return foreign_key.target_fullname, foreign_key.ondelete, foreign_key.onupdate


def _check_sql(model: type) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    )


def _application_payload() -> dict[str, object]:
    return {
        "code": "wms",
        "name": "仓储系统",
        "base_url": "https://wms.example.com",
        "callback_url": "https://wms.example.com/web#/auth/control/callback",
        "provisioning_url": "https://wms.example.com/api/v1/system/auth/control/tenant/provision",
        "provisioning_enabled": True,
        "provisioning_timeout_seconds": 10,
    }


def test_application_exposes_provisioning_configuration() -> None:
    application = ControlApplicationModel(
        site_id=1,
        code="wms",
        name="仓储系统",
        base_url="https://wms.example.com",
        callback_url="https://wms.example.com/web#/auth/control/callback",
        client_id="wms-client",
        client_secret_hash="hashed",
    )

    assert application.provisioning_enabled is False
    assert application.provisioning_timeout_seconds == 10
    assert application.provisioning_url is None


def test_application_provisioning_schema_requires_valid_url_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.DEV)
    payload = _application_payload()
    assert ControlApplicationCreateSchema(**payload).provisioning_enabled is True

    payload["provisioning_url"] = None
    with pytest.raises(ValidationError, match="开户地址"):
        ControlApplicationCreateSchema(**payload)

    with pytest.raises(ValidationError):
        ControlApplicationUpdateSchema(provisioning_enabled=True, provisioning_url="/relative")

    assert ControlApplicationUpdateSchema(provisioning_enabled=False, provisioning_url=None).provisioning_url is None
    with pytest.raises(ValidationError):
        ControlApplicationUpdateSchema(provisioning_timeout_seconds=0)
    with pytest.raises(ValidationError):
        ControlApplicationUpdateSchema(provisioning_timeout_seconds=61)

    monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.PROD)
    payload = _application_payload()
    payload["provisioning_url"] = "http://wms.example.com/api/v1/system/auth/control/tenant/provision"
    with pytest.raises(ValidationError, match="HTTPS"):
        ControlApplicationCreateSchema(**payload)


def test_application_output_exposes_safe_provisioning_configuration() -> None:
    application = ControlApplicationModel(
        id=1,
        site_id=1,
        code="wms",
        name="仓储系统",
        base_url="https://wms.example.com",
        callback_url="https://wms.example.com/web#/auth/control/callback",
        client_id="wms-client",
        client_secret_hash="hashed",
        provisioning_url="https://wms.example.com/api/v1/system/auth/control/tenant/provision",
        provisioning_enabled=True,
        provisioning_timeout_seconds=15,
        status=0,
        sort=0,
        is_deleted=False,
    )

    output = ControlApplicationOutSchema.model_validate(application).model_dump()

    assert output["provisioning_enabled"] is True
    assert output["provisioning_timeout_seconds"] == 15
    assert "client_secret_hash" not in output


def test_application_service_rejects_enabling_without_provisioning_url(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    from app.api.v1.module_control import control_router

    original_routes = list(test_client.app.router.routes)
    test_client.app.include_router(control_router)
    try:
        payload = _application_payload()
        payload.update(
            code=f"wms-{uuid4().hex[:8]}",
            provisioning_url=None,
            provisioning_enabled=False,
        )
        create_response = test_client.post("/control/applications", json=payload, headers=auth_headers)
        assert create_response.status_code == 200, create_response.text

        application_id = create_response.json()["data"]["id"]
        update_response = test_client.put(
            f"/control/applications/{application_id}",
            json={"provisioning_enabled": True},
            headers=auth_headers,
        )
        assert update_response.status_code == 400, update_response.text
        assert "开户地址" in update_response.text
    finally:
        test_client.app.router.routes[:] = original_routes


def test_application_service_revalidates_existing_provisioning_url_when_enabling_in_prod(
    test_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.module_control import control_router

    original_routes = list(test_client.app.router.routes)
    test_client.app.include_router(control_router)
    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.DEV)
        payload = _application_payload()
        payload.update(
            code=f"wms-{uuid4().hex[:8]}",
            provisioning_url="http://wms.example.com/api/v1/system/auth/control/tenant/provision",
            provisioning_enabled=False,
        )
        create_response = test_client.post("/control/applications", json=payload, headers=auth_headers)
        assert create_response.status_code == 200, create_response.text

        monkeypatch.setattr(settings, "ENVIRONMENT", EnvironmentEnum.PROD)
        application_id = create_response.json()["data"]["id"]
        update_response = test_client.put(
            f"/control/applications/{application_id}",
            json={"provisioning_enabled": True},
            headers=auth_headers,
        )

        assert update_response.status_code == 400, update_response.text
        assert "HTTPS" in update_response.text
    finally:
        test_client.app.router.routes[:] = original_routes


def test_application_package_defines_scoped_unique_keys_and_defaults() -> None:
    assert _unique_columns(ControlApplicationPackageModel) >= {
        ("application_id", "code"),
        ("application_id", "target_package_code"),
    }
    package = ControlApplicationPackageModel(site_id=1, application_id=2, code="basic", name="基础版", target_package_code="basic")
    assert package.is_default is False
    assert package.status == 0
    assert package.sort == 0
    assert _foreign_key(ControlApplicationPackageModel, "application_id") == ("control_application.id", "RESTRICT", "CASCADE")


def test_application_package_schema_normalizes_codes_and_hides_internal_fields() -> None:
    package = ControlApplicationPackageCreateSchema(
        application_id=2,
        code=" Pro ",
        name=" 专业版 ",
        target_package_code=" ENTERPRISE ",
        is_default=True,
    )
    assert package.code == "pro"
    assert package.target_package_code == "enterprise"
    assert package.name == "专业版"

    for invalid in ("wms-pro", "wms_pro", "中文"):
        with pytest.raises(ValidationError):
            ControlApplicationPackageCreateSchema(application_id=2, code=invalid, name="套餐", target_package_code="basic")

    assert ControlApplicationPackageUpdateSchema(code=" BASIC ").code == "basic"
    output_fields = ControlApplicationPackageOutSchema.model_fields
    assert "client_secret_hash" not in output_fields
    assert "code_hash" not in output_fields


def test_tenant_provision_has_one_row_per_tenant_application() -> None:
    assert _unique_columns(ControlTenantProvisionModel) >= {
        ("tenant_id", "application_id"),
        ("provision_request_uuid",),
    }
    assert _foreign_key(ControlTenantProvisionModel, "application_package_id") == (
        "control_application_package.id",
        "RESTRICT",
        "CASCADE",
    )
    assert _foreign_key(ControlTenantProvisionModel, "owner_user_id") == ("sys_user.id", "RESTRICT", "CASCADE")
    status_check = _check_sql(ControlTenantProvisionModel)
    assert all(status in status_check for status in ("pending", "processing", "succeeded", "failed"))
    assert "max_attempts = 2" in status_check
    assert "attempt_count <= max_attempts" in status_check
    assert ControlTenantProvisionModel.__table__.c.active_execution_token.type.length == 64
    assert ControlTenantProvisionModel.__table__.c.active_execution_token.nullable

    provision = ControlTenantProvisionModel(
        site_id=1,
        tenant_id=2,
        application_id=3,
        application_package_id=4,
        owner_user_id=5,
        provision_request_uuid="11111111-1111-4111-8111-111111111111",
        desired_target_tenant_code="ACME01",
    )
    assert provision.status == "pending"
    assert provision.attempt_count == 0
    assert provision.max_attempts == 2


def test_provision_ticket_stores_hash_and_lifecycle_only() -> None:
    columns = set(ControlTenantProvisionTicketModel.__table__.c.keys())
    assert {"code_hash", "provision_id", "status", "issued_at", "expires_at", "redeemed_at"} <= columns
    assert "code" not in columns
    assert ControlTenantProvisionTicketModel.__table__.c.code_hash.unique
    assert _foreign_key(ControlTenantProvisionTicketModel, "provision_id") == (
        "control_tenant_provision.id",
        "RESTRICT",
        "CASCADE",
    )
    lifecycle_check = _check_sql(ControlTenantProvisionTicketModel)
    assert all(status in lifecycle_check for status in ("issued", "redeemed", "expired"))
    assert "expires_at > issued_at" in lifecycle_check
    assert "redeemed_at IS NOT NULL" in lifecycle_check
    ticket = ControlTenantProvisionTicketModel(
        code_hash="a" * 64,
        provision_id=1,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert ticket.status == "issued"
    assert ticket.redeemed_at is None


def test_provision_outputs_and_exchange_claims_do_not_expose_ticket_hash() -> None:
    now = datetime.now(UTC)
    provision = ControlTenantProvisionModel(
        id=10,
        site_id=1,
        tenant_id=2,
        application_id=3,
        application_package_id=4,
        owner_user_id=5,
        provision_request_uuid="11111111-1111-4111-8111-111111111111",
        desired_target_tenant_code="ACME01",
        status="failed",
        attempt_count=2,
        max_attempts=2,
        last_error_code="TARGET_TIMEOUT",
        last_error_message="目标系统响应超时",
        next_retry_at=now + timedelta(seconds=10),
        is_deleted=False,
    )
    output = ControlTenantProvisionOutSchema.model_validate(provision).model_dump()
    assert output["status"] == "failed"
    assert "code_hash" not in output

    claims = ControlProvisionExchangeClaimsSchema(
        provision_request_uuid="11111111-1111-4111-8111-111111111111",
        central_tenant_uuid="22222222-2222-4222-8222-222222222222",
        central_tenant_code="ACME01",
        tenant_name="示例企业",
        unified_social_credit_code="91350100M000100Y43",
        site_code="default",
        target_tenant_code="ACME01",
        target_package_code="wmspro",
        owner={
            "central_user_uuid": "33333333-3333-4333-8333-333333333333",
            "username": "ACME01_admin",
            "name": "示例企业管理员",
            "status": 0,
        },
        issuer="https://control.example.com",
    )
    assert "code_hash" not in claims.model_dump()
    assert "client_secret" not in claims.model_dump()
