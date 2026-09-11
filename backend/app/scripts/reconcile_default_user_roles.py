"""Preview or reconcile the built-in USER role for existing product tenants."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.federated_access.default_role import (
    DefaultUserRoleService,
)
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.config.setting import settings
from app.core.assembly import get_assembly
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


class ReconciliationError(RuntimeError):
    """The reconciliation cannot be committed as one complete transaction."""


class DriftDetectedError(ReconciliationError):
    """Verification found unsafe or incomplete built-in USER role state."""


MAX_BATCH_SIZE = 1000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="预览或校正当前产品租户的内置普通用户角色"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="提交校正；默认仅预览",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="只读校验内置普通用户角色",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="verify 发现漂移时以非零状态退出",
    )
    parser.add_argument("--batch-size", type=int, default=100, help="本批最多处理租户数")
    parser.add_argument("--after-id", type=int, default=0, help="只处理大于该租户 ID 的记录")
    parser.add_argument(
        "--confirm-product",
        help="apply 时必须显式确认当前产品 code",
    )
    parser.add_argument(
        "--confirm-database",
        help="apply 时必须显式确认当前数据库名",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--tenant-id",
        dest="tenant_ids",
        action="append",
        type=int,
        help="只处理指定租户；可重复传入",
    )
    scope.add_argument(
        "--all-tenants",
        action="store_true",
        help="显式选择当前产品的全部有套餐租户",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.tenant_ids and any(tenant_id <= 0 for tenant_id in args.tenant_ids):
        parser.error("--tenant-id 必须为正整数")
    if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
        parser.error(f"--batch-size 必须在 1 到 {MAX_BATCH_SIZE} 之间")
    if args.after_id < 0:
        parser.error("--after-id 不能为负数")
    if args.fail_on_drift and not args.verify:
        parser.error("--fail-on-drift 必须搭配 --verify")
    if args.apply:
        product_code = get_assembly().application_code
        if args.confirm_product != product_code:
            parser.error(
                f"--apply 必须搭配 --confirm-product {product_code}"
            )
        if args.confirm_database != settings.DATABASE_NAME:
            parser.error(
                f"--apply 必须搭配 --confirm-database {settings.DATABASE_NAME}"
            )
    return args


async def _target_tenant_ids(
    db: AsyncSession,
    tenant_ids: set[int] | None,
    *,
    batch_size: int,
    after_id: int,
) -> list[int]:
    if tenant_ids is not None:
        if not tenant_ids:
            raise ReconciliationError("显式 tenant_ids 不能为空")
        rows = (
            await db.execute(
                select(
                    TenantModel.id,
                    TenantModel.is_deleted,
                    TenantModel.package_id,
                ).where(TenantModel.id.in_(tenant_ids))
            )
        ).all()
        found_ids = {row.id for row in rows}
        missing_ids = sorted(tenant_ids - found_ids)
        deleted_ids = sorted(row.id for row in rows if row.is_deleted)
        no_package_ids = sorted(
            row.id for row in rows if row.package_id is None
        )
        errors: list[str] = []
        if missing_ids:
            errors.append(f"不存在: {missing_ids}")
        if deleted_ids:
            errors.append(f"已删除: {deleted_ids}")
        if no_package_ids:
            errors.append(f"未关联套餐: {no_package_ids}")
        if errors:
            raise ReconciliationError(
                "显式 tenant_ids 无法精确解析：" + "；".join(errors)
            )
        return sorted(tenant_id for tenant_id in tenant_ids if tenant_id > after_id)[
            :batch_size
        ]

    stmt = select(TenantModel.id).where(
        TenantModel.package_id.is_not(None),
        TenantModel.is_deleted.is_(False),
        TenantModel.id > after_id,
    ).order_by(TenantModel.id.asc()).limit(batch_size)
    return sorted((await db.execute(stmt)).scalars().all())


async def build_reconciliation_plan(
    db: AsyncSession,
    *,
    tenant_ids: set[int] | None = None,
    batch_size: int = 100,
    after_id: int = 0,
) -> dict[str, Any]:
    """Build a read-only reconciliation plan for the current Assembly."""
    service = DefaultUserRoleService(db)
    if not DefaultUserRoleService.is_applicable():
        raise ReconciliationError("当前装配未声明默认角色策略")
    rows: list[dict[str, Any]] = []
    if not 1 <= batch_size <= MAX_BATCH_SIZE or after_id < 0:
        raise ReconciliationError(
            f"batch_size 必须在 1 到 {MAX_BATCH_SIZE} 之间且 after_id 不能为负数"
        )
    target_tenant_ids = await _target_tenant_ids(
        db,
        tenant_ids,
        batch_size=batch_size,
        after_id=after_id,
    )
    site_ids = (
        sorted(
            set(
                (
                    await db.execute(
                        select(TenantModel.site_id).where(
                            TenantModel.id.in_(target_tenant_ids)
                        )
                    )
                ).scalars().all()
            )
        )
        if target_tenant_ids
        else []
    )
    for tenant_id in target_tenant_ids:
        auth = AuthSchema(
            db=db,
            tenant_id=tenant_id,
            check_data_scope=False,
        )
        allowed_ids = set(
            await PackageService(auth).get_tenant_available_menu_ids(tenant_id)
        )
        desired_menus = await service._load_assembly_business_menus(
            allowed_ids=allowed_ids,
        )
        role = (
            await db.execute(
                select(RoleModel)
                .where(
                    RoleModel.tenant_id == tenant_id,
                    RoleModel.code == service.ROLE_CODE,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        current_ids: set[int] = set()
        if role is not None:
            current_ids = set(
                (
                    await db.execute(
                        select(RoleMenusModel.menu_id).where(
                            RoleMenusModel.role_id == role.id
                        )
                    )
                ).scalars().all()
            )
        desired_ids = {menu.id for menu in desired_menus}
        current_menus = (
            list(
                (
                    await db.execute(
                        select(MenuModel).where(MenuModel.id.in_(current_ids))
                    )
                ).scalars().all()
            )
            if current_ids
            else []
        )
        management_leak = any(
            menu.scope != "tenant"
            or str(menu.permission or "").startswith(service.EXCLUDED_PREFIXES)
            for menu in current_menus
        )
        drift_reasons: list[str] = []
        if not desired_ids:
            action = "error"
            drift_reasons.append("no_effective_menus")
        elif role is None:
            action = "create"
            drift_reasons.append("missing_system_user_role")
        elif (
            current_ids != desired_ids
            or role.name != service.ROLE_NAME
            or role.status != 0
            or not role.is_system
            or role.data_scope != service.policy.data_scope
            or role.is_deleted
        ):
            action = "update"
            if current_ids != desired_ids:
                drift_reasons.append("menu_set_mismatch")
            if (
                role.name != service.ROLE_NAME
                or role.status != 0
                or not role.is_system
                or role.data_scope != service.policy.data_scope
                or role.is_deleted
            ):
                drift_reasons.append("role_attribute_mismatch")
        else:
            action = "unchanged"
        if role is None and "missing_system_user_role" not in drift_reasons:
            drift_reasons.append("missing_system_user_role")
        if management_leak:
            drift_reasons.append("management_permission_leak")
        row = {
            "tenant_id": tenant_id,
            "action": action,
            "desired_menu_count": len(desired_ids),
            "current_menu_count": len(current_ids),
            "drift_reasons": drift_reasons,
        }
        if not desired_ids:
            row["error"] = "内置普通用户角色没有有效业务菜单"
        rows.append(row)
    return {
        "mode": "preview",
        "product": get_assembly().application_code,
        "database": settings.DATABASE_NAME,
        "site_ids": site_ids,
        "site_count": len(site_ids),
        "tenant_count": len(rows),
        "batch_size": batch_size,
        "after_id": after_id,
        "next_after_id": target_tenant_ids[-1] if target_tenant_ids else None,
        "tenants": rows,
    }


async def reconcile_default_user_roles(
    db: AsyncSession,
    *,
    apply: bool = False,
    tenant_ids: set[int] | None = None,
    batch_size: int = 100,
    after_id: int = 0,
) -> dict[str, Any]:
    """Reconcile only when ``apply`` is explicit; preview is mutation-free."""
    plan = await build_reconciliation_plan(
        db,
        tenant_ids=tenant_ids,
        batch_size=batch_size,
        after_id=after_id,
    )
    planning_errors = [
        item for item in plan["tenants"] if item["action"] == "error"
    ]
    if planning_errors:
        tenant_list = ", ".join(
            str(item["tenant_id"]) for item in planning_errors
        )
        raise ReconciliationError(
            f"tenant {tenant_list} 计划失败，已取消整体校正"
        )
    if not apply:
        return plan
    service = DefaultUserRoleService(db)
    for item in plan["tenants"]:
        try:
            await service.ensure(item["tenant_id"])
        except Exception as exc:
            raise ReconciliationError(
                f"tenant {item['tenant_id']} 执行失败，已取消整体校正: {exc}"
            ) from exc
    await db.flush()
    plan["mode"] = "apply"
    return plan


async def verify_default_user_roles(
    db: AsyncSession,
    *,
    tenant_ids: set[int] | None = None,
    batch_size: int = 100,
    after_id: int = 0,
    fail_on_drift: bool = False,
) -> dict[str, Any]:
    """Read and report USER role drift without mutating any product data."""
    report = await build_reconciliation_plan(
        db,
        tenant_ids=tenant_ids,
        batch_size=batch_size,
        after_id=after_id,
    )
    report["mode"] = "verify"
    report["drift_count"] = sum(
        item["action"] != "unchanged" for item in report["tenants"]
    )
    if fail_on_drift and report["drift_count"]:
        raise DriftDetectedError(
            f"检测到 {report['drift_count']} 个租户的内置普通用户角色漂移"
        )
    return report


async def run(
    *,
    apply: bool = False,
    confirm_product: str | None = None,
    confirm_database: str | None = None,
    tenant_ids: set[int] | None = None,
    all_tenants: bool = False,
    verify: bool = False,
    fail_on_drift: bool = False,
    batch_size: int = 100,
    after_id: int = 0,
) -> dict[str, Any]:
    product_code = get_assembly().application_code
    if apply and verify:
        raise ReconciliationError("apply 与 verify 不能同时启用")
    if fail_on_drift and not verify:
        raise ReconciliationError("fail_on_drift 必须搭配 verify")
    if not 1 <= batch_size <= MAX_BATCH_SIZE or after_id < 0:
        raise ReconciliationError(
            f"batch_size 必须在 1 到 {MAX_BATCH_SIZE} 之间且 after_id 不能为负数"
        )
    if apply and confirm_product != product_code:
        raise ReconciliationError(
            f"apply 产品确认不匹配：期望 {product_code}"
        )
    if apply and confirm_database != settings.DATABASE_NAME:
        raise ReconciliationError(
            f"apply 数据库确认不匹配：期望 {settings.DATABASE_NAME}"
        )
    has_tenant_scope = tenant_ids is not None and bool(tenant_ids)
    if has_tenant_scope == all_tenants:
        raise ReconciliationError(
            "scope 必须在 tenant_ids 与 all_tenants 中二选一"
        )
    async with async_db_session() as db:
        try:
            if verify:
                result = await verify_default_user_roles(
                    db,
                    tenant_ids=tenant_ids,
                    batch_size=batch_size,
                    after_id=after_id,
                    fail_on_drift=fail_on_drift,
                )
            else:
                result = await reconcile_default_user_roles(
                    db,
                    apply=apply,
                    tenant_ids=tenant_ids,
                    batch_size=batch_size,
                    after_id=after_id,
                )
            if apply:
                await db.commit()
            else:
                await db.rollback()
            return result
        except Exception:
            await db.rollback()
            raise


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            asyncio.run(
                run(
                    apply=args.apply,
                    confirm_product=args.confirm_product,
                    confirm_database=args.confirm_database,
                    tenant_ids=set(args.tenant_ids) if args.tenant_ids else None,
                    all_tenants=args.all_tenants,
                    verify=args.verify,
                    fail_on_drift=args.fail_on_drift,
                    batch_size=args.batch_size,
                    after_id=args.after_id,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
