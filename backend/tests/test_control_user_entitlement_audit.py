from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.scripts.audit_user_entitlements import audit_exports, main


def _control_record(**overrides) -> dict:
    record = {
        "application_code": "alpha",
        "tenant_code": "tenant-a",
        "central_user_uuid": "central-user-1",
        "desired_state": "active",
        "sync_status": "succeeded",
        "sync_version": 3,
        "central_membership_role": "member",
        "principal_role": "member",
    }
    record.update(overrides)
    return record


def _product_record(**overrides) -> dict:
    record = {
        "application_code": "alpha",
        "tenant_code": "tenant-a",
        "central_user_uuid": "central-user-1",
        "active": True,
        "applied_version": 3,
        "role_codes": ["USER"],
        "user_role_effective_menu_count": 4,
        "effective_business_menu_count": 4,
        "admin_permission_count": 0,
        "admin_permissions": [],
        "principal_role": "member",
        "is_superuser": False,
        "platform_global_role_count": 0,
        "platform_global_role_codes": [],
    }
    record.update(overrides)
    return record


def test_audit_accepts_matching_three_product_exports() -> None:
    control = [
        _control_record(application_code="alpha"),
        _control_record(application_code="beta"),
        _control_record(application_code="gamma"),
    ]
    products = {code: [_product_record(application_code=code)] for code in ("alpha", "beta", "gamma")}

    result = audit_exports(control, products)

    assert result.exit_code == 0
    assert result.issues == []


def test_audit_reports_every_fail_closed_drift_category() -> None:
    control = [
        _control_record(application_code="alpha"),
        _control_record(application_code="beta", sync_version=5),
        _control_record(application_code="gamma"),
        _control_record(application_code="missing"),
    ]
    products = {
        "alpha": [
            _product_record(
                application_code="alpha",
                role_codes=["USER"],
                user_role_effective_menu_count=0,
            ),
            _product_record(
                application_code="alpha",
                role_codes=[],
                user_role_effective_menu_count=0,
            ),
        ],
        "beta": [
            _product_record(
                application_code="beta",
                active=False,
                applied_version=4,
                admin_permission_count=1,
            )
        ],
        "gamma": [
            _product_record(
                application_code="gamma",
                role_codes=[],
                effective_business_menu_count=0,
            )
        ],
    }

    result = audit_exports(control, products)

    assert result.exit_code == 1
    categories = {issue.category for issue in result.issues}
    assert {
        "missing",
        "state_drift",
        "version_drift",
        "duplicate",
        "missing_role",
        "user_menu_missing",
        "admin_permission_leak",
    } <= categories


def test_audit_fails_closed_when_product_omits_admin_permission_evidence() -> None:
    product = _product_record()
    product.pop("admin_permission_count")

    result = audit_exports([_control_record()], {"alpha": [product]})

    assert {issue.category for issue in result.issues} == {"admin_permission_leak"}


@pytest.mark.parametrize("sync_status", ["pending", "processing", "failed"])
def test_audit_rejects_control_rows_that_have_not_succeeded(sync_status: str) -> None:
    result = audit_exports(
        [_control_record(sync_status=sync_status)],
        {"alpha": [_product_record()]},
    )

    assert "sync_status_drift" in {issue.category for issue in result.issues}


def test_user_role_menu_count_cannot_be_masked_by_custom_business_role() -> None:
    product = _product_record(
        role_codes=["USER", "WAREHOUSE_VIEWER"],
        user_role_effective_menu_count=0,
        effective_menu_count=9,
    )

    result = audit_exports([_control_record()], {"alpha": [product]})

    assert {issue.category for issue in result.issues} == {"user_menu_missing"}


@pytest.mark.parametrize("role_code", ["owner", "ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEST_ADMIN"])
def test_known_administrative_roles_are_permission_leaks(role_code: str) -> None:
    product = _product_record(role_codes=["USER", role_code])

    result = audit_exports([_control_record()], {"alpha": [product]})

    assert "admin_permission_leak" in {issue.category for issue in result.issues}


def test_custom_business_roles_are_allowed_when_user_role_is_safe() -> None:
    product = _product_record(role_codes=["USER", "WAREHOUSE_VIEWER"])

    assert audit_exports([_control_record()], {"alpha": [product]}).issues == []


def test_owner_bootstrap_with_management_role_and_permissions_is_clean() -> None:
    control = _control_record(
        central_membership_role="owner",
        principal_role="owner",
    )
    product = _product_record(
        principal_role="owner",
        role_codes=["owner"],
        user_role_effective_menu_count=0,
        effective_business_menu_count=8,
        admin_permission_count=2,
        admin_permissions=["module_system:user:query", "module_system:role:query"],
    )

    assert audit_exports([control], {"alpha": [product]}).issues == []


def test_admin_with_corresponding_role_and_governance_access_is_clean() -> None:
    control = _control_record(
        central_membership_role="admin",
        principal_role="admin",
    )
    product = _product_record(
        principal_role="admin",
        role_codes=["admin"],
        user_role_effective_menu_count=0,
        effective_business_menu_count=6,
        admin_permission_count=1,
        admin_permissions=["module_system:user:query"],
    )

    assert audit_exports([control], {"alpha": [product]}).issues == []


@pytest.mark.parametrize(
    ("central_role", "principal_role", "role_codes"),
    [
        ("owner", "owner", ["ADMIN"]),
        ("admin", "admin", ["owner"]),
        ("admin", "admin", ["WAREHOUSE_VIEWER"]),
    ],
)
def test_governance_identity_requires_corresponding_product_role(
    central_role: str,
    principal_role: str,
    role_codes: list[str],
) -> None:
    control = _control_record(
        central_membership_role=central_role,
        principal_role=central_role,
    )
    product = _product_record(
        principal_role=principal_role,
        role_codes=role_codes,
        user_role_effective_menu_count=0,
        effective_business_menu_count=5,
        admin_permission_count=1,
        admin_permissions=["module_system:user:query"],
    )

    result = audit_exports([control], {"alpha": [product]})

    assert "governance_access_drift" in {issue.category for issue in result.issues}


def test_governance_identity_requires_management_permissions() -> None:
    control = _control_record(
        central_membership_role="owner",
        principal_role="owner",
    )
    product = _product_record(
        principal_role="owner",
        role_codes=["owner"],
        user_role_effective_menu_count=0,
        effective_business_menu_count=0,
        admin_permission_count=0,
        admin_permissions=[],
    )

    result = audit_exports([control], {"alpha": [product]})

    assert "governance_access_drift" in {issue.category for issue in result.issues}


@pytest.mark.parametrize("central_role", ["owner", "admin", "member"])
def test_product_superuser_is_always_a_platform_leak(central_role: str) -> None:
    role = central_role if central_role != "member" else "USER"
    control = _control_record(
        central_membership_role=central_role,
        principal_role=central_role,
    )
    product = _product_record(
        principal_role=central_role,
        role_codes=[role],
        is_superuser=True,
        admin_permission_count=1,
        admin_permissions=["module_platform:site:query"],
    )

    result = audit_exports([control], {"alpha": [product]})

    assert "admin_permission_leak" in {issue.category for issue in result.issues}


@pytest.mark.parametrize("central_role", ["owner", "admin", "member"])
def test_structured_platform_global_role_evidence_always_fails(
    central_role: str,
) -> None:
    control = _control_record(
        central_membership_role=central_role,
        principal_role=central_role,
    )
    product = _product_record(
        principal_role=central_role,
        role_codes=[central_role if central_role != "member" else "USER", "GLOBAL_X9"],
        platform_global_role_count=1,
        platform_global_role_codes=["GLOBAL_X9"],
        admin_permission_count=1 if central_role != "member" else 0,
        admin_permissions=["module_system:user:query"] if central_role != "member" else [],
    )

    result = audit_exports([control], {"alpha": [product]})

    assert "admin_permission_leak" in {issue.category for issue in result.issues}


def test_missing_platform_global_role_evidence_fails_closed() -> None:
    product = _product_record()
    product.pop("platform_global_role_count")

    result = audit_exports([_control_record()], {"alpha": [product]})

    assert "admin_permission_leak" in {issue.category for issue in result.issues}


@pytest.mark.parametrize("role_code", ["SUPER_ADMIN", "PLATFORM_ADMIN", "PLATFORM_GLOBAL", "SYSTEM_ADMIN"])
def test_tenant_owner_cannot_hold_platform_global_role(role_code: str) -> None:
    control = _control_record(
        central_membership_role="owner",
        principal_role="owner",
    )
    product = _product_record(
        principal_role="owner",
        role_codes=[role_code],
        user_role_effective_menu_count=0,
        effective_business_menu_count=8,
        admin_permission_count=1,
        admin_permissions=["module_platform:site:query"],
    )

    result = audit_exports([control], {"alpha": [product]})

    assert "admin_permission_leak" in {issue.category for issue in result.issues}


def test_member_manual_only_business_role_is_clean_without_user_role() -> None:
    product = _product_record(
        role_codes=["WAREHOUSE_VIEWER"],
        user_role_effective_menu_count=0,
        effective_business_menu_count=3,
    )

    assert audit_exports([_control_record()], {"alpha": [product]}).issues == []


def test_active_entitlement_without_any_role_fails() -> None:
    product = _product_record(
        role_codes=[],
        user_role_effective_menu_count=0,
        effective_business_menu_count=0,
    )

    result = audit_exports([_control_record()], {"alpha": [product]})

    assert "missing_role" in {issue.category for issue in result.issues}


def test_member_with_owner_principal_or_admin_role_fails() -> None:
    product = _product_record(
        principal_role="owner",
        role_codes=["ADMIN"],
        user_role_effective_menu_count=0,
        effective_business_menu_count=6,
    )

    result = audit_exports([_control_record()], {"alpha": [product]})

    categories = {issue.category for issue in result.issues}
    assert "principal_role_drift" in categories
    assert "admin_permission_leak" in categories


def test_inactive_entitlement_still_rejects_administrative_access() -> None:
    result = audit_exports(
        [_control_record(desired_state="inactive")],
        {
            "alpha": [
                _product_record(
                    active=False,
                    role_codes=["ADMIN"],
                    user_role_effective_menu_count=0,
                    admin_permission_count=1,
                )
            ]
        },
    )

    assert "admin_permission_leak" in {issue.category for issue in result.issues}


def test_audit_consumes_products_real_export_fixture() -> None:
    fixture_dir = Path(__file__).parent / "fixtures"
    fixture_paths = [
        fixture_dir / "federated_entitlement_export.json",
        fixture_dir / "federated_entitlement_export_beta.json",
        fixture_dir / "federated_entitlement_export_gamma.json",
    ]
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in fixture_paths]
    required_keys = {
        "application_code",
        "tenant_code",
        "central_user_uuid",
        "applied_version",
        "default_role_mode",
        "access_state",
        "principal_role",
        "role_codes",
        "user_role_effective_menu_count",
        "effective_business_menu_count",
        "admin_permission_count",
        "admin_permissions",
        "is_superuser",
        "platform_global_role_count",
        "platform_global_role_codes",
    }
    product_exports: dict[str, list[dict]] = {}
    control_records: list[dict] = []
    for payload in payloads:
        assert payload["product"] in {"alpha", "beta", "gamma"}
        product_exports[payload["product"]] = payload["entitlements"]
        for product_record in payload["entitlements"]:
            assert required_keys <= product_record.keys()
            control_records.append(
                _control_record(
                    application_code=product_record["application_code"],
                    tenant_code=product_record["tenant_code"],
                    central_user_uuid=product_record["central_user_uuid"],
                    sync_version=product_record["applied_version"],
                    central_membership_role=product_record["principal_role"],
                    principal_role=product_record["principal_role"],
                )
            )

    result = audit_exports(control_records, product_exports)

    assert set(product_exports) == {"alpha", "beta", "gamma"}
    assert {record["principal_role"] for record in product_exports["alpha"]} == {
        "owner",
        "admin",
        "member",
    }
    assert result.exit_code == 1
    expected_leak_keys = {
        (
            record["application_code"],
            record["tenant_code"],
            record["central_user_uuid"],
        )
        for records in product_exports.values()
        for record in records
        if record["platform_global_role_count"] > 0 or record["is_superuser"]
    }
    assert {issue.key for issue in result.issues} == expected_leak_keys
    assert (
        "alpha",
        "tenant-member",
        "33333333-3333-4333-8333-333333333333",
    ) not in expected_leak_keys
    assert "admin_permission_leak" in {issue.category for issue in result.issues}


def test_audit_cli_returns_nonzero_on_drift(tmp_path) -> None:
    control_path = tmp_path / "control.json"
    alpha_path = tmp_path / "alpha.json"
    control_path.write_text(json.dumps([_control_record()]), encoding="utf-8")
    alpha_path.write_text(json.dumps([]), encoding="utf-8")
    beta_path = tmp_path / "beta.json"
    gamma_path = tmp_path / "gamma.json"
    beta_path.write_text(json.dumps([]), encoding="utf-8")
    gamma_path.write_text(json.dumps([]), encoding="utf-8")

    exit_code = main(
        [
            "--control",
            str(control_path),
            "--product",
            f"alpha={alpha_path}",
            "--product",
            f"beta={beta_path}",
            "--product",
            f"gamma={gamma_path}",
            "--fail-on-drift",
        ]
    )

    assert exit_code == 1


def test_audit_cli_requires_explicit_scope_for_empty_control(tmp_path) -> None:
    control_path = tmp_path / "control.json"
    alpha_path = tmp_path / "alpha.json"
    control_path.write_text(json.dumps([]), encoding="utf-8")
    alpha_path.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(SystemExit):
        main(["--control", str(control_path), "--product", f"alpha={alpha_path}"])


@pytest.mark.parametrize("codes", [("alpha",), ("alpha", "beta"), ("alpha", "beta", "gamma")])
def test_audit_cli_accepts_declared_control_product_scope(tmp_path, codes) -> None:
    control_path = tmp_path / "control.json"
    control_path.write_text(json.dumps([_control_record(application_code=code) for code in codes]))
    args = ["--control", str(control_path), "--fail-on-drift"]
    for code in codes:
        product_path = tmp_path / f"{code}.json"
        product_path.write_text(json.dumps([_product_record(application_code=code)]))
        args.extend(["--product", f"{code}={product_path}"])
    assert main(args) == 0


def test_audit_cli_rejects_missing_product_export_even_without_rows_for_other_products(tmp_path) -> None:
    control_path = tmp_path / "control.json"
    control_path.write_text(json.dumps([_control_record(application_code="alpha"), _control_record(application_code="beta")]))
    product_path = tmp_path / "alpha.json"
    product_path.write_text(json.dumps([_product_record()]))
    with pytest.raises(SystemExit):
        main(["--control", str(control_path), "--product", f"alpha={product_path}"])


def test_audit_cli_empty_control_requires_explicit_product_scope(tmp_path) -> None:
    control_path = tmp_path / "control.json"
    control_path.write_text("[]")
    product_path = tmp_path / "alpha.json"
    product_path.write_text("[]")
    args = ["--control", str(control_path), "--product", f"alpha={product_path}", "--fail-on-drift"]
    with pytest.raises(SystemExit):
        main(args)
    assert main([*args, "--required-product", "alpha"]) == 0
    with pytest.raises(SystemExit):
        main([*args, "--required-product", "beta"])


def test_audit_cli_explicit_scope_cannot_hide_control_products(tmp_path) -> None:
    control_path = tmp_path / "control.json"
    control_path.write_text(json.dumps([_control_record(application_code="beta")]))
    product_path = tmp_path / "alpha.json"
    product_path.write_text("[]")
    with pytest.raises(SystemExit):
        main(["--control", str(control_path), "--product", f"alpha={product_path}", "--required-product", "alpha"])


def test_audit_cli_extra_product_rows_remain_visible_as_orphans(tmp_path) -> None:
    control_path = tmp_path / "control.json"
    control_path.write_text("[]")
    product_path = tmp_path / "alpha.json"
    product_path.write_text(json.dumps([_product_record()]))
    assert main(["--control", str(control_path), "--product", f"alpha={product_path}", "--required-product", "alpha", "--fail-on-drift"]) == 1


def _manual_waiting_product(**overrides):
    return _product_record(
        **{
            "default_role_mode": "manual",
            "access_state": "awaiting_role",
            "role_codes": [],
            "user_role_effective_menu_count": 0,
            "effective_business_menu_count": 0,
            **overrides,
        }
    )


def test_manual_waiting_entitlement_is_synced_but_not_ready_for_use():
    result = audit_exports([_control_record()], {"alpha": [_manual_waiting_product()]})
    assert result.exit_code == 0
    assert result.export()["awaiting_authorization_count"] == 1
    assert result.export()["access_ready"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"default_role_mode": "declared"},
        {"default_role_mode": None},
        {"access_state": "ready"},
        {"access_state": None},
        {"effective_business_menu_count": 1},
        {"effective_business_menu_count": None},
        {"role_codes": None},
        {"role_codes": ["WAREHOUSE_VIEWER"]},
    ],
)
def test_manual_waiting_exception_requires_complete_zero_access_evidence(overrides):
    result = audit_exports([_control_record()], {"alpha": [_manual_waiting_product(**overrides)]})
    assert result.exit_code == 1
    assert result.export()["awaiting_authorization_count"] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_superuser": True},
        {"admin_permission_count": 1},
        {"admin_permissions": ["module_system:user:delete"]},
        {"platform_global_role_count": 1, "platform_global_role_codes": ["PLATFORM_GLOBAL"]},
    ],
)
def test_manual_waiting_does_not_bypass_privilege_leak_checks(overrides):
    result = audit_exports([_control_record()], {"alpha": [_manual_waiting_product(**overrides)]})
    assert result.exit_code == 1
    assert "admin_permission_leak" in {issue.category for issue in result.issues}
    assert result.export()["awaiting_authorization_count"] == 0


def test_manual_waiting_does_not_accept_missing_owner_role():
    control = _control_record(central_membership_role="owner", principal_role="owner")
    result = audit_exports([control], {"alpha": [_manual_waiting_product(principal_role="owner")]})
    assert result.exit_code == 1
    assert result.export()["awaiting_authorization_count"] == 0
