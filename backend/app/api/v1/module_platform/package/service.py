
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.core.assembly import get_assembly
from app.core.base_schema import AuthSchema
from app.core.dependencies import require_superadmin
from app.core.exceptions import CustomException
from app.core.logger import logger

from .crud import PackageCRUD
from .model import PackageMenuModel, PackageModel, PackagePluginModel
from .schema import (
    PackageCreateSchema,
    PackageMenuSetSchema,
    PackageOutSchema,
    PackagePluginSetSchema,
    PackageQueryParam,
    PackageUpdateSchema,
)

OWNER_REQUIRED_MENU_PERMISSIONS = frozenset(
    {
        "module_platform:workspace:query",
        "module_platform:workspace:update",
        "module_system:dept:create",
        "module_system:dept:delete",
        "module_system:dept:detail",
        "module_system:dept:patch",
        "module_system:dept:query",
        "module_system:dept:update",
        "module_system:position:create",
        "module_system:position:delete",
        "module_system:position:detail",
        "module_system:position:patch",
        "module_system:position:query",
        "module_system:position:update",
        "module_system:role:create",
        "module_system:role:delete",
        "module_system:role:detail",
        "module_system:role:patch",
        "module_system:role:permission",
        "module_system:role:query",
        "module_system:role:update",
        "module_system:user:create",
        "module_system:user:delete",
        "module_system:user:detail",
        "module_system:user:patch",
        "module_system:user:query",
        "module_system:user:update",
    }
)


class PackageService:
    """套餐管理服务（仅超级管理员可操作）"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    @staticmethod
    def owner_required_menu_permissions() -> frozenset[str]:
        permissions = set(OWNER_REQUIRED_MENU_PERMISSIONS)
        if not get_assembly().is_feature_enabled("tenant_workspace", True):
            permissions.difference_update(
                {
                    "module_platform:workspace:query",
                    "module_platform:workspace:update",
                }
            )
        return frozenset(permissions)

    async def _validate_site(self, site_id: int) -> None:
        site = await self.auth.db.get(SiteModel, site_id)
        if site is None or site.is_deleted or site.status != 0:
            raise CustomException(msg="所属站点不存在或已停用")

    @require_superadmin
    async def detail(self, id: int) -> PackageOutSchema:
        return await PackageCRUD(self.auth).get_or_404(id=id, out_schema=PackageOutSchema, msg="该数据不存在")

    @require_superadmin
    async def page(
        self,
        page_no: int,
        page_size: int,
        search: PackageQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict:
        return await PackageCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"sort": "asc"}, {"id": "asc"}],
            search=vars(search) if search else None,
            out_schema=PackageOutSchema,
        )

    @require_superadmin
    async def create(self, data: PackageCreateSchema) -> PackageOutSchema:
        await self._validate_site(data.site_id)
        if await PackageCRUD(self.auth).get(site_id=data.site_id, name=data.name):
            raise CustomException(msg="创建失败，套餐名称已存在")
        if await PackageCRUD(self.auth).get(site_id=data.site_id, code=data.code):
            raise CustomException(msg="创建失败，套餐编码已存在")

        obj = await PackageCRUD(self.auth).create(data=data)
        result = PackageOutSchema.model_validate(obj)
        logger.info(f"创建套餐成功: {result.name}")
        return result

    @require_superadmin
    async def update(self, id: int, data: PackageUpdateSchema) -> PackageOutSchema:
        obj = await PackageCRUD(self.auth).get_or_404(id=id)
        target_site_id = data.site_id if data.site_id is not None else obj.site_id
        await self._validate_site(target_site_id)

        if target_site_id != obj.site_id:
            tenant_count = (
                await self.auth.db.execute(
                    select(func.count()).select_from(TenantModel).where(TenantModel.package_id == id)
                )
            ).scalar()
            if tenant_count:
                raise CustomException(msg="套餐已有租户使用，不能变更所属站点", status_code=409)

        if data.name is not None:
            exist = await PackageCRUD(self.auth).get(site_id=target_site_id, name=data.name)
            if exist and exist.id != id:
                raise CustomException(msg="更新失败，名称重复")
        if data.code is not None:
            exist = await PackageCRUD(self.auth).get(site_id=target_site_id, code=data.code)
            if exist and exist.id != id:
                raise CustomException(msg="更新失败，编码重复")

        if data.status is not None and data.status == 1 and obj.status == 0:
            await self.disable_cascade(package_id=id)

        updated = await PackageCRUD(self.auth).update(id=id, data=data)
        return PackageOutSchema.model_validate(updated)

    @require_superadmin
    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")

        for pid in ids:
            stmt = select(func.count()).select_from(TenantModel).where(TenantModel.package_id == pid)
            result = await self.auth.db.execute(stmt)
            count = result.scalar()
            if count and count > 0:
                raise CustomException(msg=f"套餐 ID={pid} 已被 {count} 个租户使用，无法删除")

        await PackageCRUD(self.auth).delete(ids=ids)

    async def disable_cascade(self, package_id: int) -> None:
        stmt = select(TenantModel.id, TenantModel.name).where(
            TenantModel.package_id == package_id,
            TenantModel.status == 0,
        )
        result = await self.auth.db.execute(stmt)
        rows = result.all()
        if rows:
            tenant_ids = [row[0] for row in rows]
            logger.warning(f"套餐[{package_id}]已禁用，影响租户: {tenant_ids}")

    async def get_menus(self, package_id: int) -> list[int]:
        stmt = select(PackageMenuModel.menu_id).where(PackageMenuModel.package_id == package_id)
        result = await self.auth.db.execute(stmt)
        return [row[0] for row in result.all()]

    @staticmethod
    async def sync_tenant_plugins(
        db: AsyncSession,
        tenant_id: int,
        package_id: int,
    ) -> None:
        """把套餐包含的插件幂等装配到租户。"""
        from app.api.v1.module_platform.plugin.model import TenantPluginModel

        plugin_ids = set(
            (
                await db.execute(
                    select(PackagePluginModel.plugin_id).where(
                        PackagePluginModel.package_id == package_id
                    )
                )
            )
            .scalars()
            .all()
        )
        existing = {
            item.plugin_id: item
            for item in (
                await db.execute(
                    select(TenantPluginModel).where(
                        TenantPluginModel.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        }
        for plugin_id, tenant_plugin in existing.items():
            if plugin_id in plugin_ids:
                tenant_plugin.enabled = True
                tenant_plugin.purchased = True
            else:
                tenant_plugin.enabled = False
                tenant_plugin.purchased = False

        for plugin_id in plugin_ids:
            tenant_plugin = existing.get(plugin_id)
            if tenant_plugin is None:
                db.add(
                    TenantPluginModel(
                        tenant_id=tenant_id,
                        plugin_id=plugin_id,
                        enabled=True,
                        purchased=True,
                        installed_time=datetime.now(),
                    )
                )
        await db.flush()

    @staticmethod
    async def expand_tenant_menu_ids(db: AsyncSession, menu_ids: set[int] | list[int]) -> set[int]:
        """校验租户菜单范围并递归补齐父级目录。"""
        pending = set(menu_ids)
        resolved: set[int] = set()
        while pending:
            menus = (
                await db.execute(select(MenuModel).where(MenuModel.id.in_(pending)))
            ).scalars().all()
            found_ids = {menu.id for menu in menus}
            missing_ids = pending - found_ids
            if missing_ids:
                raise CustomException(msg=f"菜单不存在: {sorted(missing_ids)}")
            platform_ids = sorted(menu.id for menu in menus if menu.scope != "tenant")
            if platform_ids:
                raise CustomException(msg=f"套餐不可包含 platform scope 菜单: {platform_ids}")
            resolved.update(found_ids)
            pending = {
                menu.parent_id
                for menu in menus
                if menu.parent_id is not None and menu.parent_id not in resolved
            }
        return resolved

    @staticmethod
    async def get_owner_minimum_menu_ids(db: AsyncSession) -> set[int]:
        """解析租户 owner 必备的组织管理与自助服务菜单。"""
        required_permissions = PackageService.owner_required_menu_permissions()
        menus = (
            await db.execute(
                select(MenuModel).where(
                    MenuModel.permission.in_(required_permissions),
                    MenuModel.status == 0,
                )
            )
        ).scalars().all()
        found_permissions = {menu.permission for menu in menus}
        missing = sorted(required_permissions - found_permissions)
        if missing:
            raise CustomException(msg=f"租户 owner 必备菜单缺失: {missing}")
        return await PackageService.expand_tenant_menu_ids(db, {menu.id for menu in menus})

    @staticmethod
    def invalidate_tenant_menu_cache(tenant_id: int, auth: AuthSchema | None = None) -> None:
        """套餐或租户套餐变更后立即失效进程级与请求级菜单缓存。"""
        from app.core.dependencies import _package_menu_cache

        _package_menu_cache.pop(tenant_id, None)
        if auth is not None and hasattr(auth, "_cached_package_menu_ids"):
            delattr(auth, "_cached_package_menu_ids")

    @staticmethod
    async def sync_tenant_role_menus(
        db: AsyncSession,
        tenant_id: int,
        available_ids: set[int],
        owner_menu_ids: set[int] | None = None,
    ) -> None:
        """移除所有角色越权菜单，并把新增可用菜单同步给 owner/admin。"""
        from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel

        role_ids = set(
            (
                await db.execute(select(RoleModel.id).where(RoleModel.tenant_id == tenant_id))
            ).scalars().all()
        )
        if role_ids:
            delete_stmt = sa.delete(RoleMenusModel).where(RoleMenusModel.role_id.in_(role_ids))
            if available_ids:
                delete_stmt = delete_stmt.where(RoleMenusModel.menu_id.notin_(available_ids))
            await db.execute(delete_stmt)

        governance_roles = (
            await db.execute(
                select(RoleModel)
                .where(
                    RoleModel.tenant_id == tenant_id,
                    RoleModel.code.in_({"owner", "admin"}),
                )
            )
        ).scalars().all()
        for governance_role in governance_roles:
            target_ids = owner_menu_ids if owner_menu_ids is not None else available_ids
            current_ids = set(
                (
                    await db.execute(
                        select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == governance_role.id)
                    )
                ).scalars().all()
            )
            for menu_id in target_ids - current_ids:
                db.add(RoleMenusModel(role_id=governance_role.id, menu_id=menu_id))
        await db.flush()

    @require_superadmin
    async def set_menus(self, package_id: int, data: PackageMenuSetSchema) -> None:
        await PackageCRUD(self.auth).get_or_404(id=package_id)
        resolved_ids = await self.expand_tenant_menu_ids(self.auth.db, data.menu_ids)
        await self.auth.db.execute(sa.delete(PackageMenuModel).where(PackageMenuModel.package_id == package_id))
        for menu_id in resolved_ids:
            self.auth.db.add(PackageMenuModel(package_id=package_id, menu_id=menu_id))
        await self.auth.db.flush()

        tenant_ids = set(
            (
                await self.auth.db.execute(
                    select(TenantModel.id).where(TenantModel.package_id == package_id)
                )
            ).scalars().all()
        )
        owner_minimum_ids = await self.get_owner_minimum_menu_ids(self.auth.db)
        available_ids = resolved_ids | owner_minimum_ids
        for tenant_id in tenant_ids:
            await self.sync_tenant_role_menus(
                self.auth.db,
                tenant_id,
                available_ids,
                owner_menu_ids=available_ids,
            )
            self.invalidate_tenant_menu_cache(tenant_id, self.auth)
        logger.info(f"套餐[{package_id}]菜单权限已设置, count={len(resolved_ids)}, tenants={len(tenant_ids)}")

    async def get_package_menu_ids(self, package_id: int) -> list[int]:
        stmt = (
            select(PackageMenuModel.menu_id)
            .join(MenuModel, MenuModel.id == PackageMenuModel.menu_id)
            .where(
                PackageMenuModel.package_id == package_id,
                MenuModel.scope == "tenant",
                MenuModel.status == 0,
            )
        )
        result = await self.auth.db.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_tenant_available_menu_ids(self, tenant_id: int) -> list[int]:
        auth = self.auth if isinstance(self, PackageService) else self

        if tenant_id == 1:
            menu_stmt = select(MenuModel.id).where(MenuModel.status == 0)
            result = await auth.db.execute(menu_stmt)
            return [row[0] for row in result.all()]

        stmt = select(TenantModel).where(TenantModel.id == tenant_id).limit(1)
        result = await auth.db.execute(stmt)
        tenant = result.scalar_one_or_none()
        if not tenant:
            return []

        owner_minimum_ids = await PackageService.get_owner_minimum_menu_ids(auth.db)
        if not tenant.package_id:
            return sorted(owner_minimum_ids)

        pkg_stmt = select(PackageModel.status).where(PackageModel.id == tenant.package_id).limit(1)
        pkg_result = await auth.db.execute(pkg_stmt)
        pkg_status = pkg_result.scalar_one_or_none()
        if pkg_status != 0:
            return sorted(owner_minimum_ids)

        menu_stmt = (
            select(PackageMenuModel.menu_id)
            .join(MenuModel, MenuModel.id == PackageMenuModel.menu_id)
            .where(
                PackageMenuModel.package_id == tenant.package_id,
                MenuModel.scope == "tenant",
                MenuModel.status == 0,
            )
        )
        result = await auth.db.execute(menu_stmt)
        return sorted(owner_minimum_ids | set(result.scalars().all()))

    async def get_tenant_available_plugin_ids(self, tenant_id: int) -> list[int]:
        from app.api.v1.module_platform.tenant.model import TenantModel

        stmt = select(TenantModel).where(TenantModel.id == tenant_id).limit(1)
        result = await self.auth.db.execute(stmt)
        tenant = result.scalar_one_or_none()
        if not tenant or not tenant.package_id:
            return []

        pkg_stmt = select(PackageModel.status).where(PackageModel.id == tenant.package_id).limit(1)
        pkg_result = await self.auth.db.execute(pkg_stmt)
        pkg_status = pkg_result.scalar_one_or_none()
        if pkg_status != 0:
            return []

        plugin_stmt = select(PackagePluginModel.plugin_id).where(PackagePluginModel.package_id == tenant.package_id)
        result = await self.auth.db.execute(plugin_stmt)
        return [row[0] for row in result.all()]

    async def get_plugins(self, package_id: int) -> list[int]:
        stmt = select(PackagePluginModel.plugin_id).where(PackagePluginModel.package_id == package_id)
        result = await self.auth.db.execute(stmt)
        return [row[0] for row in result.all()]

    @require_superadmin
    async def set_plugins(self, package_id: int, data: PackagePluginSetSchema) -> None:
        await self.auth.db.execute(sa.delete(PackagePluginModel).where(PackagePluginModel.package_id == package_id))
        for plugin_id in data.plugin_ids:
            self.auth.db.add(PackagePluginModel(package_id=package_id, plugin_id=plugin_id))
        await self.auth.db.flush()

        tenant_ids = set(
            (
                await self.auth.db.execute(
                    select(TenantModel.id).where(TenantModel.package_id == package_id)
                )
            )
            .scalars()
            .all()
        )
        for tenant_id in tenant_ids:
            await PackageService.sync_tenant_plugins(
                self.auth.db,
                tenant_id,
                package_id,
            )
        logger.info(
            f"套餐[{package_id}]插件权限已设置, count={len(data.plugin_ids)}, "
            f"tenants={len(tenant_ids)}"
        )
