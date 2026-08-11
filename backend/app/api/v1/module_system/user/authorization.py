from __future__ import annotations

from sqlalchemy import select

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.menu.schema import MenuOutSchema
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.core.assembly import filter_menu_tree_by_assembly
from app.core.base_schema import AuthSchema
from app.utils.common_util import traversal_to_tree

from .model import UserModel, UserRolesModel


class UserAuthorizationResolver:
    """统一解析租户、套餐与 Assembly 共同允许的用户菜单。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def effective_tenant_menu_ids(self) -> set[int]:
        if self.auth.tenant_id is None:
            return set()
        package_ids = set(
            await PackageService(self.auth).get_tenant_available_menu_ids(
                self.auth.tenant_id
            )
        )
        if not package_ids:
            return set()
        stmt = (
            select(MenuModel)
            .where(
                MenuModel.id.in_(package_ids),
                MenuModel.status == 0,
                MenuModel.client == "pc",
                MenuModel.scope == "tenant",
            )
            .order_by(MenuModel.order.asc())
        )
        menus = (await self.auth.db.scalars(stmt)).all()
        tree = traversal_to_tree(
            [MenuOutSchema.model_validate(menu).model_dump() for menu in menus]
        )
        filtered = filter_menu_tree_by_assembly(tree, audience="tenant")
        return self._collect_ids(filtered)

    async def effective_menu_ids_for_user(self, user: UserModel) -> set[int]:
        allowed_ids = await self.effective_tenant_menu_ids()
        if not allowed_ids:
            return set()
        if user.is_superuser:
            return allowed_ids
        return {
            menu.id
            for role in user.roles or []
            if role.status == 0
            for menu in role.menus or []
            if menu.id in allowed_ids
            and menu.status == 0
            and menu.client == "pc"
        }

    async def authorized_federated_user_ids(self) -> set[int]:
        allowed_ids = await self.effective_tenant_menu_ids()
        if not allowed_ids or self.auth.tenant_id is None:
            return set()
        stmt = (
            select(UserRolesModel.user_id)
            .join(RoleModel, RoleModel.id == UserRolesModel.role_id)
            .join(RoleMenusModel, RoleMenusModel.role_id == RoleModel.id)
            .join(UserModel, UserModel.id == UserRolesModel.user_id)
            .where(
                UserModel.tenant_id == self.auth.tenant_id,
                UserModel.auth_source == "federated",
                RoleModel.tenant_id == self.auth.tenant_id,
                RoleModel.status == 0,
                RoleMenusModel.menu_id.in_(allowed_ids),
            )
            .distinct()
        )
        ids = set((await self.auth.db.scalars(stmt)).all())
        super_stmt = select(UserModel.id).where(
            UserModel.tenant_id == self.auth.tenant_id,
            UserModel.auth_source == "federated",
            UserModel.is_superuser.is_(True),
        )
        ids.update((await self.auth.db.scalars(super_stmt)).all())
        return ids

    async def federated_user_ids(self) -> set[int]:
        if self.auth.tenant_id is None:
            return set()
        stmt = select(UserModel.id).where(
            UserModel.tenant_id == self.auth.tenant_id,
            UserModel.auth_source == "federated",
        )
        return set((await self.auth.db.scalars(stmt)).all())

    @classmethod
    def _collect_ids(cls, items: list[dict]) -> set[int]:
        result: set[int] = set()
        for item in items:
            if isinstance(item.get("id"), int):
                result.add(item["id"])
            result.update(cls._collect_ids(item.get("children") or []))
        return result
