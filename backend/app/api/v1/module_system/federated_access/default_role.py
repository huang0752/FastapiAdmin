from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.menu.schema import MenuOutSchema
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserRolesModel
from app.core.assembly import filter_menu_tree_by_assembly, get_assembly
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.utils.common_util import traversal_to_tree

from .tenant_role_lock import lock_tenant_role_assignment


class DefaultUserRoleService:
    """维护当前产品装配的内置普通用户角色。"""

    ROLE_CODE = "USER"
    ROLE_NAME = "普通用户"
    EXCLUDED_PREFIXES = (
        "module_system:",
        "module_platform:",
        "module_control:",
        "module_ai:",
    )

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.policy = get_assembly().federation_default_role
        self.ROLE_CODE = self.policy.code
        self.ROLE_NAME = self.policy.name

    @staticmethod
    def is_applicable() -> bool:
        return get_assembly().federation_default_role.mode == "declared"

    async def ensure(self, tenant_id: int) -> RoleModel | None:
        if not self.is_applicable():
            return None
        async with lock_tenant_role_assignment(self.db, tenant_id):
            return await self._ensure_locked(tenant_id)

    async def _ensure_locked(self, tenant_id: int) -> RoleModel:
        allowed_ids = set(
            await PackageService(
                AuthSchema(
                    db=self.db,
                    tenant_id=tenant_id,
                    check_data_scope=False,
                )
            ).get_tenant_available_menu_ids(tenant_id)
        )
        menus = await self._load_assembly_business_menus(
            allowed_ids=allowed_ids,
        )
        if not menus:
            raise CustomException(
                msg="内置普通用户角色没有有效业务菜单",
                status_code=409,
            )

        role = await self._ensure_role_row(tenant_id)
        await self._replace_role_menus(role.id, {menu.id for menu in menus})
        await self.db.refresh(role, attribute_names=["menus"])
        return role

    async def bind_if_user_has_no_active_role(
        self,
        tenant_id: int,
        user_id: int,
    ) -> RoleModel | None:
        if not self.is_applicable():
            return None
        async with lock_tenant_role_assignment(self.db, tenant_id):
            role = await self._ensure_locked(tenant_id)
            existing = await self._active_tenant_role_ids(tenant_id, user_id)
            if existing:
                return None
            await self._insert_do_nothing(
                UserRolesModel,
                {"user_id": user_id, "role_id": role.id},
                index_elements=("user_id", "role_id"),
            )
            await self.db.flush()
            return role

    async def _load_assembly_business_menus(
        self,
        *,
        allowed_ids: set[int],
    ) -> list[MenuModel]:
        if not allowed_ids:
            return []
        menus = list(
            (
                await self.db.execute(
                    select(MenuModel)
                    .where(
                        MenuModel.id.in_(allowed_ids),
                        MenuModel.scope == "tenant",
                        MenuModel.client == "pc",
                        MenuModel.status == 0,
                        MenuModel.is_deleted.is_(False),
                    )
                    .order_by(MenuModel.order.asc(), MenuModel.id.asc())
                )
            ).scalars().all()
        )
        if not menus:
            return []

        tree = traversal_to_tree(
            [MenuOutSchema.model_validate(menu).model_dump() for menu in menus]
        )
        assembly_tree = filter_menu_tree_by_assembly(tree, audience="tenant")
        selected_ids = self._collect_business_menu_ids(
            assembly_tree,
            permission_codes=set(self.policy.permission_codes),
            permission_prefixes=tuple(self.policy.permission_prefixes),
        )
        return [menu for menu in menus if menu.id in selected_ids]

    @classmethod
    def _collect_business_menu_ids(
        cls,
        items: list[dict],
        *,
        permission_codes: set[str] | None = None,
        permission_prefixes: tuple[str, ...] = (),
    ) -> set[int]:
        """Select only declared permissions; never traverse a forbidden boundary."""
        selected: set[int] = set()
        codes = permission_codes or set()
        prefixes = tuple(prefix.rstrip(":") + ":" for prefix in permission_prefixes)
        for item in items:
            permission = str(item.get("permission") or "")
            allowed = bool(permission) and not permission.startswith(cls.EXCLUDED_PREFIXES) and (
                permission in codes or permission.startswith(prefixes)
            )
            if permission and not allowed:
                continue
            child_ids = cls._collect_business_menu_ids(
                item.get("children") or [],
                permission_codes=codes,
                permission_prefixes=permission_prefixes,
            )
            if allowed or child_ids:
                menu_id = item.get("id")
                if isinstance(menu_id, int):
                    selected.add(menu_id)
                selected.update(child_ids)
        return selected

    async def _ensure_role_row(self, tenant_id: int) -> RoleModel:
        role = (
            await self.db.execute(
                select(RoleModel)
                .where(
                    RoleModel.tenant_id == tenant_id,
                    RoleModel.code == self.ROLE_CODE,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if role is None:
            await self._insert_do_nothing(
                RoleModel,
                {
                    "tenant_id": tenant_id,
                    "name": self.ROLE_NAME,
                    "code": self.ROLE_CODE,
                    "order": 999,
                    "status": 0,
                    "is_system": True,
                    "data_scope": self.policy.data_scope,
                    "description": "产品内置普通用户角色",
                },
                index_elements=("tenant_id", "code"),
            )
            await self.db.flush()
            role = (
                await self.db.execute(
                    select(RoleModel)
                    .where(
                        RoleModel.tenant_id == tenant_id,
                        RoleModel.code == self.ROLE_CODE,
                    )
                    .limit(1)
                )
            ).scalar_one()

        role.name = self.ROLE_NAME
        role.status = 0
        role.is_system = True
        role.data_scope = self.policy.data_scope
        role.is_deleted = False
        role.deleted_time = None
        await self.db.flush()
        return role

    async def _replace_role_menus(self, role_id: int, menu_ids: set[int]) -> None:
        current_ids = set(
            (
                await self.db.execute(
                    select(RoleMenusModel.menu_id).where(
                        RoleMenusModel.role_id == role_id
                    )
                )
            ).scalars().all()
        )
        obsolete_ids = current_ids - menu_ids
        if obsolete_ids:
            await self.db.execute(
                delete(RoleMenusModel).where(
                    RoleMenusModel.role_id == role_id,
                    RoleMenusModel.menu_id.in_(obsolete_ids),
                )
            )
        for menu_id in sorted(menu_ids - current_ids):
            await self._insert_do_nothing(
                RoleMenusModel,
                {"role_id": role_id, "menu_id": menu_id},
                index_elements=("role_id", "menu_id"),
            )
        await self.db.flush()

    async def _active_tenant_role_ids(self, tenant_id: int, user_id: int) -> set[int]:
        return set(
            (
                await self.db.execute(
                    select(RoleModel.id)
                    .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                    .where(
                        UserRolesModel.user_id == user_id,
                        RoleModel.tenant_id == tenant_id,
                        RoleModel.status == 0,
                        RoleModel.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
        )

    async def _insert_do_nothing(
        self,
        model,
        values: dict,
        *,
        index_elements: tuple[str, ...],
    ) -> None:
        dialect_name = self.db.get_bind().dialect.name
        if dialect_name == "postgresql":
            stmt = postgresql_insert(model).values(**values).on_conflict_do_nothing(
                index_elements=index_elements
            )
        elif dialect_name == "sqlite":
            stmt = sqlite_insert(model).values(**values).on_conflict_do_nothing(
                index_elements=index_elements
            )
        else:
            # Products 生产与测试分别使用 PostgreSQL/SQLite；其他方言仍保留普通插入语义。
            stmt = model.__table__.insert().values(**values)
        await self.db.execute(stmt)
