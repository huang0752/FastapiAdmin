"""Dry-run-first repair for package menus and tenant owner authorization drift."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_platform.tenant.service import TenantService
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.scripts.initialize import InitializeData

SEED_PATH = Path(__file__).resolve().parent / "data" / "platform_package_menu.json"
TENANT_SCOPE_MIGRATION_PERMISSIONS = frozenset({"module_platform:workspace:query"})


async def _scope_migration_plan(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(MenuModel.id, MenuModel.permission, MenuModel.scope).where(
                MenuModel.permission.in_(TENANT_SCOPE_MIGRATION_PERMISSIONS),
                MenuModel.scope != "tenant",
            )
        )
    ).all()
    return [
        {
            "menu_id": menu_id,
            "permission": permission,
            "from_scope": scope,
            "to_scope": "tenant",
        }
        for menu_id, permission, scope in rows
    ]


async def _desired_pairs(
    db: AsyncSession,
    *,
    scope_override_ids: set[int] | None = None,
) -> tuple[set[int], set[tuple[int, int]]]:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    rows = await InitializeData().resolve_package_menu_seed(
        db,
        seed,
        tenant_scope_override_ids=scope_override_ids,
    )
    package_codes = {item["package_code"] for item in seed}
    package_ids = set(
        (
            await db.execute(select(PackageModel.id).where(PackageModel.code.in_(package_codes)))
        ).scalars().all()
    )
    return package_ids, {(row["package_id"], row["menu_id"]) for row in rows}


async def build_repair_plan(db: AsyncSession) -> dict[str, Any]:
    """Inspect current state without mutation and return a JSON-serializable plan."""
    scope_updates = await _scope_migration_plan(db)
    scope_override_ids = {item["menu_id"] for item in scope_updates}
    package_ids, desired = await _desired_pairs(
        db,
        scope_override_ids=scope_override_ids,
    )
    current = set(
        (
            await db.execute(
                select(PackageMenuModel.package_id, PackageMenuModel.menu_id).where(
                    PackageMenuModel.package_id.in_(package_ids)
                )
            )
        ).all()
    )
    tenants = list(
        (
            await db.execute(
                select(TenantModel.id, TenantModel.code, TenantModel.package_id).where(
                    TenantModel.id != 1,
                    TenantModel.is_deleted.is_(False),
                )
            )
        ).all()
    )
    owner_rows = list(
        (
            await db.execute(
                select(TenantUserModel.tenant_id, TenantUserModel.user_id).where(
                    TenantUserModel.role == "owner"
                )
            )
        ).all()
    )
    owners_by_tenant: dict[int, list[int]] = {}
    for tenant_id, user_id in owner_rows:
        owners_by_tenant.setdefault(tenant_id, []).append(user_id)

    return {
        "menu_scope_updates": scope_updates,
        "package_menu_add": [list(pair) for pair in sorted(desired - current)],
        "package_menu_remove": [list(pair) for pair in sorted(current - desired)],
        "tenant_owner_repairs": sum(
            len(owners_by_tenant.get(tenant_id, [])) for tenant_id, _, _ in tenants
        ),
        "ownerless_tenants": [
            {"tenant_id": tenant_id, "tenant_code": code}
            for tenant_id, code, _ in tenants
            if not owners_by_tenant.get(tenant_id)
        ],
        "tenant_count": len(tenants),
    }


async def apply_repair(db: AsyncSession) -> dict[str, Any]:
    """Apply the stable package seed and repair explicitly marked tenant owners."""
    plan = await build_repair_plan(db)
    scope_update_ids = {item["menu_id"] for item in plan["menu_scope_updates"]}
    if scope_update_ids:
        await db.execute(
            MenuModel.__table__.update()
            .where(MenuModel.id.in_(scope_update_ids))
            .values(scope="tenant")
        )
    package_ids, desired = await _desired_pairs(db)
    await db.execute(
        delete(PackageMenuModel).where(PackageMenuModel.package_id.in_(package_ids))
    )
    for package_id, menu_id in sorted(desired):
        db.add(PackageMenuModel(package_id=package_id, menu_id=menu_id))
    await db.flush()

    tenant_ids = list(
        (
            await db.execute(
                select(TenantModel.id).where(
                    TenantModel.id != 1,
                    TenantModel.is_deleted.is_(False),
                )
            )
        ).scalars().all()
    )
    auth = AuthSchema(db=db, tenant_id=1, check_data_scope=False)
    package_service = PackageService(auth)
    for tenant_id in tenant_ids:
        owner_user_ids = list(
            (
                await db.execute(
                    select(TenantUserModel.user_id).where(
                        TenantUserModel.tenant_id == tenant_id,
                        TenantUserModel.role == "owner",
                    )
                )
            ).scalars().all()
        )
        for user_id in owner_user_ids:
            await TenantService.ensure_tenant_owner(db, tenant_id, user_id)
        available_ids = set(await package_service.get_tenant_available_menu_ids(tenant_id))
        await PackageService.sync_tenant_role_menus(db, tenant_id, available_ids)
        PackageService.invalidate_tenant_menu_cache(tenant_id, auth)
    await db.flush()
    return plan


async def run(*, apply: bool = False) -> dict[str, Any]:
    async with async_db_session() as db:
        if apply:
            plan = await apply_repair(db)
            await db.commit()
        else:
            plan = await build_repair_plan(db)
            await db.rollback()
        return {"mode": "apply" if apply else "dry-run", **plan}


def main() -> None:
    parser = argparse.ArgumentParser(description="修复租户套餐菜单和 owner 权限漂移")
    parser.add_argument("--apply", action="store_true", help="提交修复；默认仅预览")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
