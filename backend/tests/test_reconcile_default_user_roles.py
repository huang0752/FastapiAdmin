import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.federated_access.model import (
    FederatedAccessEntitlementModel,
)
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.config.setting import settings
from app.core.assembly import reset_assembly_cache
from app.core.database import async_db_session
from app.scripts.export_federated_entitlement_state import (
    ExportError,
    export_federated_entitlement_state,
)
from app.scripts.export_federated_entitlement_state import (
    parse_args as parse_export_args,
)
from app.scripts.reconcile_default_user_roles import (
    DriftDetectedError,
    build_reconciliation_plan,
    parse_args,
    reconcile_default_user_roles,
    verify_default_user_roles,
)


@contextmanager
def _trace_assembly():
    original_assembly = settings.APP_ASSEMBLY
    original_file = settings.APP_ASSEMBLY_FILE
    original_issuer = settings.CONTROL_SSO_ISSUER
    settings.APP_ASSEMBLY = "alpha"
    settings.APP_ASSEMBLY_FILE = "tests/fixtures/assemblies/alpha.toml"
    settings.CONTROL_SSO_ISSUER = "https://control.example.test"
    reset_assembly_cache()
    try:
        yield
    finally:
        settings.APP_ASSEMBLY = original_assembly
        settings.APP_ASSEMBLY_FILE = original_file
        settings.CONTROL_SSO_ISSUER = original_issuer
        reset_assembly_cache()


def test_parser_defaults_to_preview_and_supports_batched_cursor() -> None:
    with _trace_assembly():
        args = parse_args(["--all-tenants", "--batch-size", "25", "--after-id", "40"])

    assert args.apply is False
    assert args.verify is False
    assert args.batch_size == 25
    assert args.after_id == 40


@pytest.mark.parametrize(
    "argv",
    [
        ["--all-tenants", "--batch-size", "0"],
        ["--all-tenants", "--batch-size", "1001"],
        ["--all-tenants", "--after-id", "-1"],
        ["--all-tenants", "--fail-on-drift"],
        ["--all-tenants", "--apply", "--verify"],
    ],
)
def test_parser_rejects_unsafe_or_ambiguous_modes(argv: list[str]) -> None:
    with _trace_assembly(), pytest.raises(SystemExit):
        parse_args(argv)


def test_export_parser_caps_batch_size() -> None:
    with pytest.raises(SystemExit):
        parse_export_args(["--all-tenants", "--batch-size", "1001"])


def test_shared_export_fixture_uses_the_real_product_contract() -> None:
    fixture_dir = Path(__file__).parent / "fixtures"
    paths = {
        "alpha": fixture_dir / "federated_entitlement_export.json",
        "beta": fixture_dir / "federated_entitlement_export_beta.json",
        "gamma": fixture_dir / "federated_entitlement_export_gamma.json",
    }
    payloads = {code: json.loads(path.read_text(encoding="utf-8")) for code, path in paths.items()}
    payload = payloads["alpha"]
    assert set(payload) == {
        "product",
        "read_only",
        "batch_size",
        "after_id",
        "next_after_id",
        "entitlement_count",
        "entitlements",
    }
    for code, product_payload in payloads.items():
        assert product_payload["product"] == code
        assert product_payload["read_only"] is True
        assert all(row["application_code"] == code for row in product_payload["entitlements"])
        assert any(row["platform_global_role_count"] > 0 and row["platform_global_role_codes"] for row in product_payload["entitlements"])
    assert set(payload["entitlements"][0]) == {
        "application_code",
        "tenant_code",
        "central_user_uuid",
        "active",
        "applied_version",
        "default_role_mode",
        "access_state",
        "role_codes",
        "user_role_effective_menu_count",
        "effective_business_menu_count",
        "admin_permission_count",
        "admin_permissions",
        "principal_role",
        "is_superuser",
        "platform_global_role_codes",
        "platform_global_role_count",
    }
    by_principal = {(row["principal_role"], row["is_superuser"]): row for row in payload["entitlements"]}
    assert "owner" in by_principal[("owner", False)]["role_codes"]
    assert by_principal[("owner", False)]["admin_permission_count"] > 0
    assert by_principal[("owner", False)]["platform_global_role_codes"]
    assert by_principal[("owner", False)]["platform_global_role_count"] > 0
    assert "admin" in by_principal[("admin", False)]["role_codes"]
    assert by_principal[("admin", False)]["admin_permission_count"] > 0
    assert by_principal[("admin", False)]["platform_global_role_codes"]
    assert by_principal[("admin", False)]["platform_global_role_count"] > 0
    assert by_principal[("member", False)]["admin_permission_count"] == 0
    assert by_principal[("member", True)]["admin_permission_count"] > 0
    assert "PLATFORM_GLOBAL" in by_principal[("member", True)]["role_codes"]
    assert by_principal[("member", True)]["platform_global_role_codes"] == ["PLATFORM_GLOBAL"]
    assert by_principal[("member", True)]["platform_global_role_count"] == 1


async def _seed_role_fixture():
    suffix = uuid4().hex[:10]
    async with async_db_session() as db:
        package = PackageModel(
            site_id=1,
            name=f"普通用户校正套餐{suffix}",
            code=f"rolepkg{suffix}",
            status=0,
        )
        db.add(package)
        await db.flush()
        tenant = TenantModel(
            site_id=1,
            package_id=package.id,
            name=f"普通用户校正租户{suffix}",
            code=f"roletenant{suffix}",
            status=0,
        )
        product_menu = MenuModel(
            name=f"追溯业务{suffix}",
            title=f"追溯业务{suffix}",
            type=2,
            order=1,
            permission="module_alpha:record:query",
            route_name=f"TraceRole{suffix}",
            route_path=f"/alpha/trace-role-{suffix}",
            component_path="alpha/record/index",
            client="pc",
            scope="tenant",
            status=0,
        )
        management_menu = MenuModel(
            name=f"管理权限{suffix}",
            title=f"管理权限{suffix}",
            type=3,
            order=2,
            permission="module_system:user:delete",
            client="pc",
            scope="tenant",
            status=0,
        )
        db.add_all([tenant, product_menu, management_menu])
        await db.flush()
        db.add_all(
            [
                PackageMenuModel(package_id=package.id, menu_id=product_menu.id),
                PackageMenuModel(package_id=package.id, menu_id=management_menu.id),
            ]
        )
        await db.flush()
        yield db, tenant, product_menu, management_menu
        await db.rollback()


async def _assert_plan_is_batched_after_cursor() -> None:
    async for db, tenant, _product_menu, _management_menu in _seed_role_fixture():
        plan = await build_reconciliation_plan(
            db,
            tenant_ids={tenant.id},
            batch_size=1,
            after_id=tenant.id - 1,
        )
        assert [item["tenant_id"] for item in plan["tenants"]] == [tenant.id]
        assert plan["batch_size"] == 1
        assert plan["after_id"] == tenant.id - 1
        assert plan["next_after_id"] == tenant.id


def test_reconciliation_plan_is_batched_after_cursor(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_plan_is_batched_after_cursor())


async def _assert_verify_detects_management_leak() -> None:
    async for db, tenant, product_menu, management_menu in _seed_role_fixture():
        role = RoleModel(
            tenant_id=tenant.id,
            name="普通用户",
            code="USER",
            status=0,
            is_system=True,
            data_scope=1,
        )
        db.add(role)
        await db.flush()
        db.add_all(
            [
                RoleMenusModel(role_id=role.id, menu_id=product_menu.id),
                RoleMenusModel(role_id=role.id, menu_id=management_menu.id),
            ]
        )
        await db.flush()

        report = await verify_default_user_roles(db, tenant_ids={tenant.id})
        assert report["drift_count"] == 1
        assert report["tenants"][0]["drift_reasons"] == [
            "menu_set_mismatch",
            "management_permission_leak",
        ]
        with pytest.raises(DriftDetectedError):
            await verify_default_user_roles(
                db,
                tenant_ids={tenant.id},
                fail_on_drift=True,
            )


def test_verify_fails_on_management_permission_leak(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_verify_detects_management_leak())


async def _assert_verify_reports_leak_when_no_business_menu_is_available() -> None:
    async for db, tenant, product_menu, management_menu in _seed_role_fixture():
        await db.execute(
            delete(PackageMenuModel).where(
                PackageMenuModel.package_id == tenant.package_id,
                PackageMenuModel.menu_id == product_menu.id,
            )
        )
        role = RoleModel(
            tenant_id=tenant.id,
            name="普通用户",
            code="USER",
            status=0,
            is_system=True,
            data_scope=1,
        )
        db.add(role)
        await db.flush()
        db.add(RoleMenusModel(role_id=role.id, menu_id=management_menu.id))
        await db.flush()

        report = await verify_default_user_roles(db, tenant_ids={tenant.id})
        row = report["tenants"][0]
        assert row["current_menu_count"] == 1
        assert row["drift_reasons"] == [
            "no_effective_menus",
            "management_permission_leak",
        ]


def test_verify_still_audits_leaks_when_no_business_menu_exists(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_verify_reports_leak_when_no_business_menu_is_available())


async def _assert_apply_only_changes_system_user_role() -> None:
    async for db, tenant, product_menu, _management_menu in _seed_role_fixture():
        manual = RoleModel(
            tenant_id=tenant.id,
            name="人工质检角色",
            code=f"MANUAL_{uuid4().hex[:8]}",
            status=1,
            is_system=False,
            data_scope=4,
            description="人工配置不得覆盖",
        )
        db.add(manual)
        await db.flush()
        db.add(RoleMenusModel(role_id=manual.id, menu_id=product_menu.id))
        await db.flush()
        before = (manual.name, manual.status, manual.is_system, manual.data_scope)

        result = await reconcile_default_user_roles(
            db,
            apply=True,
            tenant_ids={tenant.id},
            batch_size=100,
            after_id=0,
        )
        await db.flush()
        preserved = await db.get(RoleModel, manual.id)
        user_role = (
            await db.execute(
                select(RoleModel).where(
                    RoleModel.tenant_id == tenant.id,
                    RoleModel.code == "USER",
                )
            )
        ).scalar_one()

        assert result["mode"] == "apply"
        assert (preserved.name, preserved.status, preserved.is_system, preserved.data_scope) == before
        preserved_menu_ids = set((await db.execute(select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == preserved.id))).scalars().all())
        assert preserved_menu_ids == {product_menu.id}
        assert user_role.is_system is True
        assert {menu.id for menu in user_role.menus} == {product_menu.id}


def test_apply_preserves_manual_roles(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_apply_only_changes_system_user_role())


async def _assert_export_uses_field_allowlist() -> None:
    async for db, tenant, product_menu, management_menu in _seed_role_fixture():
        subject = str(uuid4())
        user = UserModel(
            tenant_id=tenant.id,
            username=f"export_{uuid4().hex[:10]}",
            password="secret-must-not-export",
            name="敏感姓名不得导出",
            mobile="13800000000",
            email="private@example.com",
            status=0,
        )
        role = RoleModel(
            tenant_id=tenant.id,
            name="普通用户",
            code="USER",
            status=0,
            is_system=True,
            data_scope=1,
        )
        admin_role = RoleModel(
            tenant_id=tenant.id,
            name="人工管理员",
            code="ADMIN",
            status=0,
            is_system=False,
            data_scope=1,
        )
        db.add_all([user, role, admin_role])
        await db.flush()
        db.add_all(
            [
                UserRolesModel(user_id=user.id, role_id=role.id),
                UserRolesModel(user_id=user.id, role_id=admin_role.id),
                TenantUserModel(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role="member",
                    is_default=1,
                ),
                RoleMenusModel(role_id=role.id, menu_id=product_menu.id),
                RoleMenusModel(role_id=admin_role.id, menu_id=management_menu.id),
                FederatedAccessEntitlementModel(
                    site_id=tenant.site_id,
                    tenant_id=tenant.id,
                    local_user_id=user.id,
                    issuer="https://control.example.test",
                    central_user_uuid=subject,
                    status="active",
                    applied_version=7,
                ),
                FederatedAccessEntitlementModel(
                    site_id=tenant.site_id,
                    tenant_id=tenant.id,
                    local_user_id=user.id,
                    issuer="https://evil.example.test/",
                    central_user_uuid=subject,
                    status="active",
                    applied_version=999,
                ),
            ]
        )
        await db.flush()

        output = await export_federated_entitlement_state(
            db,
            tenant_ids={tenant.id},
        )
        assert output["entitlements"] == [
            {
                "application_code": "alpha",
                "central_user_uuid": subject,
                "tenant_code": tenant.code,
                "active": True,
                "default_role_mode": "declared",
                "access_state": "blocked",
                "applied_version": 7,
                "role_codes": ["ADMIN", "USER"],
                "user_role_effective_menu_count": 1,
                "effective_business_menu_count": 1,
                "admin_permission_count": 1,
                "admin_permissions": ["module_system:user:delete"],
                "principal_role": "member",
                "is_superuser": False,
                "platform_global_role_codes": [],
                "platform_global_role_count": 0,
            }
        ]
        serialized = json.dumps(output, ensure_ascii=False)
        assert "database" not in output
        for forbidden in (
            "username",
            "password",
            "phone",
            "email",
            "token",
            "issuer",
            "敏感姓名",
            "13800000000",
            "private@example.com",
            "secret-must-not-export",
        ):
            assert forbidden not in serialized


def test_export_is_read_only_and_contains_no_pii_or_secrets(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_export_uses_field_allowlist())


async def _assert_missing_trusted_issuer_fails_closed() -> None:
    async with async_db_session() as db:
        with pytest.raises(ExportError, match="CONTROL_SSO_ISSUER"):
            await export_federated_entitlement_state(db, tenant_ids={2})


def test_export_fails_closed_without_trusted_issuer(test_client) -> None:
    original_issuer = settings.CONTROL_SSO_ISSUER
    settings.CONTROL_SSO_ISSUER = " /// "
    try:
        asyncio.run(_assert_missing_trusted_issuer_fails_closed())
    finally:
        settings.CONTROL_SSO_ISSUER = original_issuer


async def _assert_manual_business_menu_is_reported(
    *,
    include_empty_user_role: bool,
) -> None:
    async for db, tenant, product_menu, _management_menu in _seed_role_fixture():
        user = UserModel(
            tenant_id=tenant.id,
            username=f"zero_menu_{uuid4().hex[:10]}",
            password="not-exported",
            name="零菜单用户",
            status=0,
        )
        user_role = RoleModel(
            tenant_id=tenant.id,
            name="普通用户",
            code="USER",
            status=0,
            is_system=True,
            data_scope=1,
        )
        manual_role = RoleModel(
            tenant_id=tenant.id,
            name="人工业务角色",
            code=f"MANUAL_{uuid4().hex[:8]}",
            status=0,
            is_system=False,
            data_scope=1,
        )
        db.add_all([user, manual_role, *([user_role] if include_empty_user_role else [])])
        await db.flush()
        bindings = [
            UserRolesModel(user_id=user.id, role_id=manual_role.id),
            TenantUserModel(
                user_id=user.id,
                tenant_id=tenant.id,
                role="member",
                is_default=1,
            ),
            RoleMenusModel(role_id=manual_role.id, menu_id=product_menu.id),
            FederatedAccessEntitlementModel(
                site_id=tenant.site_id,
                tenant_id=tenant.id,
                local_user_id=user.id,
                issuer="https://control.example.test",
                central_user_uuid=str(uuid4()),
                status="active",
                applied_version=1,
            ),
        ]
        if include_empty_user_role:
            bindings.append(UserRolesModel(user_id=user.id, role_id=user_role.id))
        db.add_all(bindings)
        await db.flush()

        output = await export_federated_entitlement_state(db, tenant_ids={tenant.id})
        row = output["entitlements"][0]
        assert ("USER" in row["role_codes"]) is include_empty_user_role
        assert any(code.startswith("MANUAL_") for code in row["role_codes"])
        assert row["user_role_effective_menu_count"] == 0
        assert row["effective_business_menu_count"] == 1
        assert row["admin_permission_count"] == 0
        assert row["admin_permissions"] == []


def test_manual_role_menu_does_not_mask_empty_user_role_drift(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_manual_business_menu_is_reported(include_empty_user_role=True))


def test_manual_only_role_reports_effective_business_menu(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_manual_business_menu_is_reported(include_empty_user_role=False))


async def _assert_governance_minimum_menu_is_effective_outside_package(
    role_code: str,
) -> None:
    async for db, tenant, _product_menu, _management_menu in _seed_role_fixture():
        owner_menu = (await db.execute(select(MenuModel).where(MenuModel.permission == "module_system:user:query").order_by(MenuModel.id.asc()).limit(1))).scalar_one()
        package_link = (
            await db.execute(
                select(PackageMenuModel.id).where(
                    PackageMenuModel.package_id == tenant.package_id,
                    PackageMenuModel.menu_id == owner_menu.id,
                )
            )
        ).scalar_one_or_none()
        assert package_link is None

        user = UserModel(
            tenant_id=tenant.id,
            username=f"owner_export_{uuid4().hex[:10]}",
            password="not-exported",
            name="Owner 对账",
            status=0,
        )
        owner_role = RoleModel(
            tenant_id=tenant.id,
            name="租户拥有者",
            code=role_code,
            status=0,
            is_system=True,
            data_scope=1,
        )
        platform_role = RoleModel(
            tenant_id=1,
            name="平台全局异常角色",
            code=f"GLOBAL_{role_code}_{uuid4().hex[:8]}",
            status=0,
            is_system=False,
            data_scope=1,
        )
        db.add_all([user, owner_role, platform_role])
        await db.flush()
        db.add_all(
            [
                UserRolesModel(user_id=user.id, role_id=owner_role.id),
                UserRolesModel(user_id=user.id, role_id=platform_role.id),
                RoleMenusModel(role_id=owner_role.id, menu_id=owner_menu.id),
                TenantUserModel(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role=role_code,
                    is_default=1,
                ),
                FederatedAccessEntitlementModel(
                    site_id=tenant.site_id,
                    tenant_id=tenant.id,
                    local_user_id=user.id,
                    issuer="https://control.example.test",
                    central_user_uuid=str(uuid4()),
                    status="active",
                    applied_version=1,
                ),
            ]
        )
        await db.flush()

        output = await export_federated_entitlement_state(db, tenant_ids={tenant.id})
        row = output["entitlements"][0]
        assert row["principal_role"] == role_code
        assert role_code in row["role_codes"]
        assert row["platform_global_role_codes"] == [platform_role.code]
        assert row["platform_global_role_count"] == 1
        assert row["is_superuser"] is False
        assert row["admin_permission_count"] >= 1
        assert "module_system:user:query" in row["admin_permissions"]


def test_owner_minimum_admin_menu_is_exported_outside_package(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_governance_minimum_menu_is_effective_outside_package("owner"))


def test_admin_minimum_admin_menu_is_exported_outside_package(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_governance_minimum_menu_is_effective_outside_package("admin"))


async def _assert_member_superuser_bypass_is_exported() -> None:
    async for db, tenant, _product_menu, _management_menu in _seed_role_fixture():
        user = UserModel(
            tenant_id=tenant.id,
            username=f"super_export_{uuid4().hex[:10]}",
            password="not-exported",
            name="异常超管成员",
            status=0,
            is_superuser=True,
        )
        platform_role = RoleModel(
            tenant_id=1,
            name="平台全局异常角色",
            code=f"PLATFORM_GLOBAL_{uuid4().hex[:8]}",
            status=0,
            is_system=False,
            data_scope=1,
        )
        db.add_all([user, platform_role])
        await db.flush()
        db.add_all(
            [
                TenantUserModel(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role="member",
                    is_default=1,
                ),
                UserRolesModel(user_id=user.id, role_id=platform_role.id),
                FederatedAccessEntitlementModel(
                    site_id=tenant.site_id,
                    tenant_id=tenant.id,
                    local_user_id=user.id,
                    issuer="https://control.example.test/",
                    central_user_uuid=str(uuid4()),
                    status="active",
                    applied_version=1,
                ),
            ]
        )
        await db.flush()

        output = await export_federated_entitlement_state(db, tenant_ids={tenant.id})
        row = output["entitlements"][0]
        assert row["principal_role"] == "member"
        assert row["is_superuser"] is True
        assert any(code.startswith("PLATFORM_GLOBAL_") for code in row["role_codes"])
        assert row["platform_global_role_codes"] == [platform_role.code]
        assert row["platform_global_role_count"] == 1
        assert row["admin_permission_count"] > 0
        assert row["admin_permissions"]


def test_member_superuser_bypass_is_exported_for_fail_closed_audit(test_client) -> None:
    with _trace_assembly():
        asyncio.run(_assert_member_superuser_bypass_is_exported())


async def _assert_manual_export_distinguishes_waiting_and_ready() -> None:
    async for db, tenant, product_menu, _management_menu in _seed_role_fixture():
        user = UserModel(tenant_id=tenant.id, username=f"manual_export_{uuid4().hex[:10]}", password="unusable", name="Manual", status=0)
        db.add(user)
        await db.flush()
        db.add_all(
            [
                TenantUserModel(user_id=user.id, tenant_id=tenant.id, role="member", is_default=1),
                FederatedAccessEntitlementModel(
                    site_id=tenant.site_id, tenant_id=tenant.id, local_user_id=user.id, issuer="https://control.example.test", central_user_uuid=str(uuid4()), status="active", applied_version=1
                ),
            ]
        )
        await db.flush()
        output = await export_federated_entitlement_state(db, tenant_ids={tenant.id})
        row = output["entitlements"][0]
        assert row["default_role_mode"] == "manual"
        assert row["access_state"] == "awaiting_role"
        assert row["role_codes"] == []
        assert row["effective_business_menu_count"] == 0
        role = RoleModel(tenant_id=tenant.id, name="Business viewer", code="BUSINESS_VIEWER", status=0, is_system=False, data_scope=1)
        db.add(role)
        await db.flush()
        db.add_all([UserRolesModel(user_id=user.id, role_id=role.id), RoleMenusModel(role_id=role.id, menu_id=product_menu.id)])
        await db.flush()
        row = (await export_federated_entitlement_state(db, tenant_ids={tenant.id}))["entitlements"][0]
        assert row["access_state"] == "ready"
        assert row["effective_business_menu_count"] == 1


def test_manual_export_distinguishes_waiting_and_ready(test_client, tmp_path) -> None:
    template = Path(__file__).parent / "fixtures/assemblies/alpha.toml"
    manual_config = tmp_path / "manual-alpha.toml"
    manual_config.write_text(template.read_text().replace('mode = "declared"', 'mode = "manual"'))
    with _trace_assembly():
        settings.APP_ASSEMBLY_FILE = str(manual_config)
        reset_assembly_cache()
        asyncio.run(_assert_manual_export_distinguishes_waiting_and_ready())
