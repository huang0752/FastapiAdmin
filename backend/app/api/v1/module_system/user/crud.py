from datetime import datetime

from sqlalchemy import delete, select

from app.api.v1.module_platform.tenant.model import TenantUserModel
from app.api.v1.module_system.federated_access.tenant_role_lock import (
    lock_tenant_role_assignment,
)
from app.api.v1.module_system.position.crud import PositionCRUD
from app.api.v1.module_system.position.model import PositionModel
from app.api.v1.module_system.role.constants import (
    GOVERNANCE_ROLE_CODES,
    user_assignment_protected_codes,
)
from app.api.v1.module_system.role.crud import RoleCRUD
from app.api.v1.module_system.role.model import RoleModel
from app.core.assembly import get_assembly
from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException

from .login_identifier import sync_user_login_identifiers
from .model import UserModel, UserPositionsModel, UserRolesModel
from .schema import (
    UserCreateSchema,
    UserOutSchema,
    UserUpdateSchema,
)


class UserCRUD(CRUDBase[UserModel, UserCreateSchema, UserUpdateSchema]):
    """用户模块数据层"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=UserModel, auth=auth)

    async def create(self, data) -> UserModel:
        user = await super().create(data=data)
        await sync_user_login_identifiers(self.db, user)
        return user

    async def update(self, id: int, data) -> UserModel:
        user = await super().update(id=id, data=data)
        changed_fields = set(data) if isinstance(data, dict) else set(getattr(data, "model_fields_set", set()))
        if changed_fields & {"username", "email", "mobile"}:
            await sync_user_login_identifiers(self.db, user)
        return user

    async def page_with_user_ids(
        self,
        *,
        offset: int,
        limit: int,
        order_by: list[dict[str, str]],
        search: dict,
        include_ids: set[int] | None = None,
    ):
        """在数据库分页前按用户 ID 集合收窄查询。"""
        scoped = dict(search)
        if include_ids is not None:
            scoped["id"] = ("in", sorted(include_ids) or [-1])
        return await self.page(
            offset=offset,
            limit=limit,
            order_by=order_by,
            search=scoped,
            out_schema=UserOutSchema,
        )

    async def update_last_login(self, id: int) -> None:
        """
        更新用户最后登录时间

        参数:
        - id (int): 用户ID
        """
        await self.set([id], last_login=datetime.now())

    async def set_user_roles(self, user_ids: list[int], role_ids: list[int]) -> None:
        """
        批量设置用户角色

        参数:
        - user_ids (list[int]): 用户ID列表
        - role_ids (list[int]): 角色ID列表

        返回:
        - None
        """
        tenant_id = self.auth.tenant_id
        if tenant_id is None:
            raise CustomException(msg="租户上下文缺失", status_code=403)
        async with lock_tenant_role_assignment(self.auth.db, tenant_id):
            # Re-read relationships after acquiring the tenant lock so a
            # concurrent default binding cannot survive a manual replacement.
            user_objs = await self.get_list(search={"id": ("in", user_ids)})
            for obj in user_objs:
                await self.auth.db.refresh(obj, attribute_names=["roles"])
            if role_ids:
                role_objs = await RoleCRUD(self.auth).get_list(search={"id": ("in", role_ids)})
                if len(role_objs) != len(set(role_ids)):
                    raise CustomException(msg="部分角色不存在于当前租户", status_code=400)
                self.ensure_roles_assignable(role_objs)
            else:
                role_objs = []

            for obj in user_objs:
                relationship = obj.roles
                preserved_roles = [
                    role
                    for role in relationship
                    if role.tenant_id != tenant_id
                    or (
                        role.code != get_assembly().federation_default_role.code
                        and (
                            role.is_system
                            or role.code in GOVERNANCE_ROLE_CODES
                        )
                    )
                ]
                relationship.clear()
                relationship.extend([*preserved_roles, *role_objs])
            await self.auth.db.flush()

    @staticmethod
    def ensure_roles_assignable(role_objs) -> None:
        reserved = sorted(
            role.code
            for role in role_objs
            if role.is_system or role.code in user_assignment_protected_codes()
        )
        if reserved:
            raise CustomException(
                msg=f"系统管理角色不能作为普通用户角色直接分配: {reserved}",
                status_code=400,
            )

    async def set_user_positions(self, user_ids: list[int], position_ids: list[int]) -> None:
        """
        批量设置用户岗位

        参数:
        - user_ids (list[int]): 用户ID列表
        - position_ids (list[int]): 岗位ID列表

        返回:
        - None
        """
        user_objs = await self.get_list(search={"id": ("in", user_ids)})
        if position_ids:
            position_objs = await PositionCRUD(self.auth).get_list(search={"id": ("in", position_ids)})
        else:
            position_objs = []

        for obj in user_objs:
            relationship = obj.positions
            relationship.clear()
            relationship.extend(position_objs)
        await self.auth.db.flush()

    async def unbind_users_for_delete(self, user_ids: list[int]) -> None:
        """删除前解除用户在当前租户的角色、岗位与成员关系。

        调用方必须先完成整批删除权限、身份、成员关系和 owner 校验。
        本方法不查询、不删除其他租户的任何关联。
        """
        if not user_ids:
            return
        tenant_id = self.auth.tenant_id
        if tenant_id is None:
            raise CustomException(msg="租户上下文缺失", status_code=403)
        held_tenant_locks = self.auth.db.sync_session.info.get(
            "tenant_role_assignment_locks",
            {},
        )
        if tenant_id not in held_tenant_locks:
            raise RuntimeError("删除用户关联前必须持有租户角色分配锁")
        role_ids = set(
            (
                await self.auth.db.execute(
                    select(RoleModel.id).where(
                        RoleModel.tenant_id == tenant_id
                    )
                )
            ).scalars().all()
        )
        if role_ids:
            await self.auth.db.execute(
                delete(UserRolesModel).where(
                    UserRolesModel.user_id.in_(user_ids),
                    UserRolesModel.role_id.in_(role_ids),
                )
            )
        position_ids = set(
            (
                await self.auth.db.execute(
                    select(PositionModel.id).where(
                        PositionModel.tenant_id == tenant_id
                    )
                )
            ).scalars().all()
        )
        if position_ids:
            await self.auth.db.execute(
                delete(UserPositionsModel).where(
                    UserPositionsModel.user_id.in_(user_ids),
                    UserPositionsModel.position_id.in_(position_ids),
                )
            )
        await self.auth.db.execute(
            delete(TenantUserModel).where(
                TenantUserModel.user_id.in_(user_ids),
                TenantUserModel.tenant_id == tenant_id,
            )
        )
        await self.auth.db.flush()

    async def change_password(self, id: int, password_hash: str) -> UserModel:
        """
        修改用户密码

        参数:
        - id (int): 用户ID
        - password_hash (str): 密码哈希值

        返回:
        - UserModel: 更新后的用户信息
        """
        return await self.update(id=id, data=UserUpdateSchema(password=password_hash))

    async def forget_password(self, id: int, password_hash: str) -> UserModel:
        """重置密码（与 change_password 逻辑相同）"""
        return await self.change_password(id=id, password_hash=password_hash)
