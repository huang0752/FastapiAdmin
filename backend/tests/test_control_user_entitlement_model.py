"""Control user-entitlement ledger and ticket persistence contracts."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.api.v1.module_control.model import (
    ControlApplicationModel,
    ControlTenantApplicationModel,
    ControlUserApplicationGrantModel,
)
from app.api.v1.module_control.user_entitlement.model import ControlUserEntitlementTicketModel


def _check_sql(model: type) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    )


def _unique_columns(model: type) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _composite_foreign_keys(model: type) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in model.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def test_application_exposes_disabled_entitlement_sync_configuration() -> None:
    application = ControlApplicationModel(
        site_id=1,
        code="wms",
        name="仓储系统",
        base_url="https://wms.example.com",
        callback_url="https://wms.example.com/web#/auth/control/callback",
        client_id="wms-client",
        client_secret_hash="hashed",
    )

    assert application.entitlement_sync_url is None
    assert application.entitlement_sync_enabled is False
    assert application.entitlement_sync_timeout_seconds == 10
    assert ControlApplicationModel.__table__.c.entitlement_sync_enabled.server_default is not None
    assert ControlApplicationModel.__table__.c.entitlement_sync_timeout_seconds.server_default is not None
    assert "entitlement_sync_timeout_seconds >= 1" in _check_sql(ControlApplicationModel)
    assert "entitlement_sync_timeout_seconds <= 60" in _check_sql(ControlApplicationModel)


def test_grant_exposes_versioned_desired_and_applied_state() -> None:
    grant = ControlUserApplicationGrantModel(
        site_id=1,
        tenant_application_id=2,
        tenant_id=3,
        user_id=4,
    )

    assert grant.desired_state == "inactive"
    assert grant.status == 1
    assert ControlUserApplicationGrantModel.__table__.c.status.default.arg == 1
    assert grant.sync_status == "pending"
    assert grant.sync_version == 0
    assert grant.last_event_id is None
    assert grant.active_execution_token is None
    assert grant.last_error_code is None
    assert grant.last_error_message is None
    assert grant.retry_count == 0
    assert grant.next_retry_at is None
    assert grant.last_attempt_at is None
    assert grant.last_synced_at is None

    checks = _check_sql(ControlUserApplicationGrantModel)
    assert "desired_state IN ('active', 'inactive')" in checks
    assert "sync_status IN ('pending', 'processing', 'succeeded', 'failed')" in checks
    assert "sync_version >= 0" in checks
    assert "retry_count >= 0" in checks
    assert "status IN (0, 1)" in checks
    assert ControlUserApplicationGrantModel.__table__.c.desired_state.server_default is not None
    assert ControlUserApplicationGrantModel.__table__.c.sync_status.server_default is not None
    assert ControlUserApplicationGrantModel.__table__.c.sync_version.server_default is not None
    assert ControlUserApplicationGrantModel.__table__.c.retry_count.server_default is not None
    assert ("id", "tenant_application_id", "site_id") in _unique_columns(ControlUserApplicationGrantModel)

    for field_name in ("next_retry_at", "last_attempt_at", "last_synced_at"):
        assert ControlUserApplicationGrantModel.__table__.c[field_name].type.timezone is True


def test_entitlement_ticket_only_persists_hash_and_binding_coordinates() -> None:
    now = datetime.now(UTC)
    ticket = ControlUserEntitlementTicketModel(
        code_hash="a" * 64,
        grant_id=1,
        tenant_application_id=5,
        application_id=2,
        site_id=3,
        event_id="event-1",
        sync_version=4,
        desired_state="active",
        expires_at=now + timedelta(minutes=1),
    )

    assert "code" not in ControlUserEntitlementTicketModel.__table__.c
    assert ticket.code_hash == "a" * 64
    assert ticket.status == "issued"
    assert ticket.issued_at.tzinfo is not None
    assert ticket.redeemed_at is None
    assert ticket.desired_state == "active"
    assert ControlUserEntitlementTicketModel.__table__.c.desired_state.server_default is not None
    assert ControlUserEntitlementTicketModel.__table__.c.status.server_default is not None
    assert ("id", "application_id", "site_id") in _unique_columns(ControlTenantApplicationModel)
    assert _composite_foreign_keys(ControlUserEntitlementTicketModel) >= {
        (
            ("grant_id", "tenant_application_id", "site_id"),
            (
                "control_user_application_grant.id",
                "control_user_application_grant.tenant_application_id",
                "control_user_application_grant.site_id",
            ),
        ),
        (
            ("tenant_application_id", "application_id", "site_id"),
            (
                "control_tenant_application.id",
                "control_tenant_application.application_id",
                "control_tenant_application.site_id",
            ),
        ),
    }

    checks = _check_sql(ControlUserEntitlementTicketModel)
    assert "status IN ('issued', 'redeemed', 'expired')" in checks
    assert "expires_at > issued_at" in checks
    assert "sync_version > 0" in checks
    assert "desired_state IN ('active', 'inactive')" in checks
