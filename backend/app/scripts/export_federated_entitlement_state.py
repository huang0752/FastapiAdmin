"""Export a read-only, privacy-minimized federated entitlement snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.federated_access.default_role import (
    DefaultUserRoleService,
)
from app.api.v1.module_system.federated_access.model import (
    FederatedAccessEntitlementModel,
)
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.authorization import UserAuthorizationResolver
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.config.setting import settings
from app.core.assembly import get_assembly
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


class ExportError(RuntimeError):
    """The requested read-only export scope is invalid."""


MAX_BATCH_SIZE = 1000
PLATFORM_TENANT_ID = 1


def trusted_control_issuer() -> str:
    """Return the canonical configured issuer or fail closed."""
    issuer = settings.CONTROL_SSO_ISSUER.strip().rstrip("/")
    if not issuer:
        raise ExportError("CONTROL_SSO_ISSUER 未配置，拒绝导出联邦授权")
    return issuer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读导出联邦授权对账状态")
    parser.add_argument("--tenant-id", dest="tenant_ids", action="append", type=int)
    parser.add_argument("--all-tenants", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0, help="授权记录 ID 游标")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.tenant_ids) == args.all_tenants:
        parser.error("scope 必须在 --tenant-id 与 --all-tenants 中二选一")
    if args.tenant_ids and any(value <= 0 for value in args.tenant_ids):
        parser.error("--tenant-id 必须为正整数")
    if not 1 <= args.batch_size <= MAX_BATCH_SIZE or args.after_id < 0:
        parser.error(f"--batch-size 必须在 1 到 {MAX_BATCH_SIZE} 之间且 --after-id 不能为负数")
    return args


async def export_federated_entitlement_state(
    db: AsyncSession,
    *,
    tenant_ids: set[int] | None = None,
    batch_size: int = 100,
    after_id: int = 0,
) -> dict[str, Any]:
    """Return only the identifiers and effective authorization needed for audit."""
    if not 1 <= batch_size <= MAX_BATCH_SIZE or after_id < 0:
        raise ExportError(f"batch_size 必须在 1 到 {MAX_BATCH_SIZE} 之间且 after_id 不能为负数")

    issuer = trusted_control_issuer()
    stmt = (
        select(FederatedAccessEntitlementModel)
        .where(
            FederatedAccessEntitlementModel.id > after_id,
            func.rtrim(FederatedAccessEntitlementModel.issuer, "/") == issuer,
        )
        .order_by(FederatedAccessEntitlementModel.id.asc())
        .limit(batch_size)
    )
    if tenant_ids is not None:
        if not tenant_ids:
            raise ExportError("显式 tenant_ids 不能为空")
        stmt = stmt.where(FederatedAccessEntitlementModel.tenant_id.in_(tenant_ids))
    entitlements = list((await db.execute(stmt)).scalars().all())
    rows: list[dict[str, Any]] = []
    assembly = get_assembly()
    application_code = assembly.application_code
    default_role_mode = assembly.federation_default_role.mode
    if not application_code:
        raise ExportError("当前装配未配置 application_code")
    for entitlement in entitlements:
        tenant = await db.get(TenantModel, entitlement.tenant_id)
        if tenant is None:
            raise ExportError(f"授权记录 {entitlement.id} 关联的租户不存在")
        role_codes: list[str] = []
        user_role_effective_menu_count = 0
        effective_business_menu_count = 0
        admin_permission_count = 0
        admin_permissions: list[str] = []
        is_superuser = False
        platform_global_role_codes: list[str] = []
        principal_role = (
            await db.execute(
                select(TenantUserModel.role)
                .where(
                    TenantUserModel.user_id == entitlement.local_user_id,
                    TenantUserModel.tenant_id == entitlement.tenant_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if entitlement.local_user_id is not None:
            user = await db.get(UserModel, entitlement.local_user_id)
            # Export the stored privilege bit itself so audit remains fail-closed
            # even when some other user lifecycle field is concurrently drifting.
            is_superuser = bool(user is not None and user.is_superuser)
            assigned_roles = list(
                (
                    await db.execute(
                        select(RoleModel)
                        .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                        .where(
                            UserRolesModel.user_id == entitlement.local_user_id,
                            RoleModel.status == 0,
                            RoleModel.is_deleted.is_(False),
                        )
                        .order_by(RoleModel.code.asc())
                    )
                )
                .scalars()
                .all()
            )
            role_codes = [role.code for role in assigned_roles]
            platform_global_role_codes = sorted({role.code for role in assigned_roles if role.tenant_id == PLATFORM_TENANT_ID})
            roles = [role for role in assigned_roles if role.tenant_id == entitlement.tenant_id]
            role_ids = [role.id for role in roles]
            user_role_ids = [role.id for role in roles if role.code == DefaultUserRoleService.ROLE_CODE]
            auth = AuthSchema(
                db=db,
                tenant_id=tenant.id,
                check_data_scope=False,
            )
            allowed_ids = await UserAuthorizationResolver(auth).effective_tenant_menu_ids()
            assigned_role_menus = (
                list(
                    (
                        await db.execute(
                            select(RoleMenusModel.role_id, MenuModel)
                            .join(MenuModel, MenuModel.id == RoleMenusModel.menu_id)
                            .where(
                                RoleMenusModel.role_id.in_(role_ids),
                                RoleMenusModel.menu_id.in_(allowed_ids),
                            )
                        )
                    ).all()
                )
                if role_ids and allowed_ids
                else []
            )
            if allowed_ids:
                effective_menus = list((await db.execute(select(MenuModel).where(MenuModel.id.in_(allowed_ids)))).scalars().all()) if is_superuser else [menu for _role_id, menu in assigned_role_menus]
                admin_menu_ids = {menu.id for menu in effective_menus if menu.scope != "tenant" or str(menu.permission or "").startswith(DefaultUserRoleService.EXCLUDED_PREFIXES)}
                admin_permission_count = len(admin_menu_ids)
                admin_permissions = sorted({str(menu.permission) for menu in effective_menus if menu.id in admin_menu_ids and menu.permission})
                if default_role_mode == "manual":
                    # Manual roles are not constrained to the automatic default-role declaration.
                    business_menus = [menu for menu in effective_menus if menu.scope == "tenant" and menu.permission and not str(menu.permission).startswith(DefaultUserRoleService.EXCLUDED_PREFIXES)]
                else:
                    business_menus = await DefaultUserRoleService(db)._load_assembly_business_menus(allowed_ids=allowed_ids)
                business_menu_ids = {menu.id for menu in business_menus}
                effective_menu_ids = {menu.id for menu in effective_menus}
                effective_business_menu_count = len(business_menu_ids & effective_menu_ids)
                if user_role_ids:
                    user_assigned_ids = {menu.id for role_id, menu in assigned_role_menus if role_id in user_role_ids}
                    user_role_effective_menu_count = len(business_menu_ids & user_assigned_ids)
        active = entitlement.status == "active"
        access_state = "inactive" if not active else "blocked"
        if active and not is_superuser and not platform_global_role_codes:
            if principal_role == "member" and admin_permission_count == 0 and not admin_permissions:
                if default_role_mode == "manual" and not role_codes and effective_business_menu_count == 0:
                    access_state = "awaiting_role"
                elif effective_business_menu_count > 0:
                    access_state = "ready"
            elif principal_role in {"owner", "admin"} and principal_role.upper() in {code.upper() for code in role_codes} and admin_permission_count > 0:
                access_state = "ready"
        rows.append(
            {
                "application_code": application_code,
                "central_user_uuid": entitlement.central_user_uuid,
                "tenant_code": tenant.code,
                "active": active,
                "default_role_mode": default_role_mode,
                "access_state": access_state,
                "applied_version": entitlement.applied_version,
                "role_codes": role_codes,
                "user_role_effective_menu_count": user_role_effective_menu_count,
                "effective_business_menu_count": effective_business_menu_count,
                "admin_permission_count": admin_permission_count,
                "admin_permissions": admin_permissions,
                "principal_role": principal_role,
                "is_superuser": is_superuser,
                "platform_global_role_count": len(platform_global_role_codes),
                "platform_global_role_codes": platform_global_role_codes,
            }
        )

    return {
        "product": application_code,
        "read_only": True,
        "batch_size": batch_size,
        "after_id": after_id,
        "next_after_id": entitlements[-1].id if entitlements else None,
        "entitlement_count": len(rows),
        "entitlements": rows,
    }


async def run(
    *,
    tenant_ids: set[int] | None,
    all_tenants: bool,
    batch_size: int,
    after_id: int,
) -> dict[str, Any]:
    if bool(tenant_ids) == all_tenants:
        raise ExportError("scope 必须在 tenant_ids 与 all_tenants 中二选一")
    async with async_db_session() as db:
        result = await export_federated_entitlement_state(
            db,
            tenant_ids=tenant_ids,
            batch_size=batch_size,
            after_id=after_id,
        )
        await db.rollback()
        return result


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            asyncio.run(
                run(
                    tenant_ids=set(args.tenant_ids) if args.tenant_ids else None,
                    all_tenants=args.all_tenants,
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
