
import json
import secrets
import string
from dataclasses import dataclass

import sqlalchemy as sa
from redis.asyncio.client import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import RedisInitKeyConfig
from app.core.base_schema import AuthSchema
from app.core.dependencies import require_platform_admin
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD
from app.utils.hash_bcrpy_util import PwdUtil

from .crud import TenantCRUD
from .model import TenantModel, TenantStatus, TenantStorageUsageModel, TenantUserModel
from .schema import (
    PackageChangePreviewOut,
    TenantBatchStatusSchema,
    TenantConfigItem,
    TenantConfigOutSchema,
    TenantCreateSchema,
    TenantInitialAdminSchema,
    TenantOutSchema,
    TenantQueryParam,
    TenantUpdateSchema,
    TenantUserAddSchema,
    TenantUserOutSchema,
)

TENANT_CONFIG_FIELDS = [
    "name", "description", "version", "logo_url", "favicon",
    "login_bg", "copyright", "keep_record", "help_doc", "privacy",
    "clause", "git_code",
]

TENANT_SELF_BRAND_CONFIG_FIELDS = {
    "name", "logo_url", "favicon", "login_bg", "copyright",
    "keep_record", "help_doc", "privacy", "clause",
}

TENANT_BRAND_CONFIG_ALIASES = {
    "tenant_name": "name",
    "tenant_version": "version",
    "tenant_logo": "logo_url",
}

TENANT_BRAND_CONFIG_FIELDS = {field: alias for alias, field in TENANT_BRAND_CONFIG_ALIASES.items()}


@dataclass(frozen=True)
class PackageChangePlan:
    """套餐变更的纯计算结果，预览与实际应用共享。"""

    current_menu_ids: set[int]
    final_menu_ids: set[int]
    removed_menu_ids: set[int]
    added_menu_ids: set[int]


class TenantStorageQuotaService:
    """租户私有文件存储配额账本。"""

    BYTES_PER_MB = 1024 * 1024

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    def _database(self) -> AsyncSession:
        if self.auth.db is None:
            raise CustomException(msg="数据库会话不可用")
        if not self.auth.tenant_id:
            raise CustomException(msg="缺少有效租户信息", code=10403, status_code=403)
        return self.auth.db

    async def _lock_usage(self) -> TenantStorageUsageModel | None:
        """锁定租户行后取得用量行；系统租户不记账。"""
        if self.auth.tenant_id == 1:
            return None
        db = self._database()
        tenant = (
            await db.execute(
                sa.select(TenantModel)
                .where(TenantModel.id == self.auth.tenant_id, TenantModel.is_deleted.is_(False))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if tenant is None:
            raise CustomException(msg="租户不存在", status_code=404)

        usage = await db.get(TenantStorageUsageModel, self.auth.tenant_id)
        if usage is None:
            usage = TenantStorageUsageModel(tenant_id=self.auth.tenant_id, used_bytes=0, reserved_bytes=0)
            db.add(usage)
            await db.flush()
        return usage

    async def _storage_limit_bytes(self) -> int | None:
        db = self._database()
        tenant = await db.get(TenantModel, self.auth.tenant_id)
        if tenant is None or tenant.package_id is None:
            return 0
        from app.api.v1.module_platform.package.model import PackageModel

        package = await db.get(PackageModel, tenant.package_id)
        if package is None or package.is_deleted or package.status != 0:
            return 0
        if package.max_storage_mb == 0:
            return None
        return package.max_storage_mb * self.BYTES_PER_MB

    @staticmethod
    def _validate_size(size_bytes: int) -> None:
        if size_bytes <= 0:
            raise CustomException(msg="文件大小必须大于 0", status_code=400)

    async def reserve(self, size_bytes: int) -> None:
        """写盘前预占配额；租户行锁保证并发请求串行核算。"""
        self._validate_size(size_bytes)
        usage = await self._lock_usage()
        if usage is None:
            return
        limit_bytes = await self._storage_limit_bytes()
        if limit_bytes is not None and usage.used_bytes + usage.reserved_bytes + size_bytes > limit_bytes:
            raise CustomException(msg="租户存储空间已达套餐上限", code=10429, status_code=413)
        usage.reserved_bytes += size_bytes
        await self._database().flush()

    async def commit_reservation(self, size_bytes: int) -> None:
        """文件写盘成功后将预占转为已用。"""
        self._validate_size(size_bytes)
        usage = await self._lock_usage()
        if usage is None:
            return
        if usage.reserved_bytes < size_bytes:
            raise CustomException(msg="存储配额预占记录不足", status_code=409)
        usage.reserved_bytes -= size_bytes
        usage.used_bytes += size_bytes
        await self._database().flush()

    async def release_reservation(self, size_bytes: int) -> None:
        """写盘失败时释放预占。"""
        self._validate_size(size_bytes)
        usage = await self._lock_usage()
        if usage is None:
            return
        usage.reserved_bytes = max(0, usage.reserved_bytes - size_bytes)
        await self._database().flush()

    async def release_used(self, size_bytes: int) -> None:
        """私有文件删除后释放已用空间。"""
        self._validate_size(size_bytes)
        usage = await self._lock_usage()
        if usage is None:
            return
        usage.used_bytes = max(0, usage.used_bytes - size_bytes)
        await self._database().flush()


class TenantService:
    """
    租户管理服务（跨租户管理仅平台管理员可用，租户侧使用自助品牌接口）

    设计：实例方法承载「当前用户上下文 (auth)」，``redis`` 仍是方法参数。
    内部跨方法调用从 ``cls.xxx(auth, ...)`` 改为 ``self.xxx(...)``。
    定时任务方法与静态工具方法保持 ``@staticmethod``（无 auth）。
    """

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def _ensure_uscc_available(
        self,
        *,
        site_id: int,
        unified_social_credit_code: str | None,
        exclude_tenant_id: int | None = None,
    ) -> None:
        """在数据库约束前返回稳定、可理解的同站点信用代码冲突。"""
        if unified_social_credit_code is None:
            return
        conditions = [
            TenantModel.site_id == site_id,
            TenantModel.unified_social_credit_code == unified_social_credit_code,
        ]
        if exclude_tenant_id is not None:
            conditions.append(TenantModel.id != exclude_tenant_id)
        existing_id = (
            await self.auth.db.execute(sa.select(TenantModel.id).where(*conditions).limit(1))
        ).scalar_one_or_none()
        if existing_id is not None:
            raise CustomException(
                msg="统一社会信用代码在当前站点已存在",
                status_code=400,
            )

    @staticmethod
    def _is_uscc_unique_conflict(exc: BaseException) -> bool:
        """识别数据库约束兜底产生的本站点企业标识冲突。"""
        pending: list[BaseException] = [exc]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            detail = str(current)
            if "uq_platform_tenant_site_uscc" in detail or (
                "platform_tenant.site_id" in detail
                and "platform_tenant.unified_social_credit_code" in detail
            ):
                return True
            for nested in (current.__cause__, current.__context__, getattr(current, "orig", None)):
                if isinstance(nested, BaseException):
                    pending.append(nested)
        return False

    @classmethod
    def _raise_stable_uscc_conflict(cls, exc: CustomException) -> None:
        if cls._is_uscc_unique_conflict(exc):
            raise CustomException(
                msg="统一社会信用代码在当前站点已存在",
                status_code=409,
            ) from exc
        raise exc

    async def _replace_tenant_member_rbac(self, tenant_id: int, user_id: int, role_code: str) -> None:
        """让成员身份成为租户内 RBAC 绑定的唯一事实来源。"""
        from app.api.v1.module_platform.package.service import PackageService
        from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
        from app.api.v1.module_system.user.model import UserRolesModel

        tenant_role_ids = set(
            (
                await self.auth.db.execute(
                    sa.select(RoleModel.id).where(RoleModel.tenant_id == tenant_id)
                )
            ).scalars().all()
        )
        if tenant_role_ids:
            await self.auth.db.execute(
                sa.delete(UserRolesModel).where(
                    UserRolesModel.user_id == user_id,
                    UserRolesModel.role_id.in_(tenant_role_ids),
                )
            )

        role_meta = {
            "owner": ("租户管理员", 1, 4, "租户 owner 角色"),
            "admin": ("租户管理员", 2, 4, "租户 admin 角色"),
            "member": ("租户成员", 999, 1, "租户 member 角色"),
        }
        name, order, data_scope, description = role_meta[role_code]
        role = (
            await self.auth.db.execute(
                sa.select(RoleModel)
                .where(RoleModel.tenant_id == tenant_id, RoleModel.code == role_code)
                .limit(1)
            )
        ).scalar_one_or_none()
        if role is None:
            role = RoleModel(
                name=name,
                code=role_code,
                tenant_id=tenant_id,
                order=order,
                status=0,
                data_scope=data_scope,
                description=description,
            )
            self.auth.db.add(role)
            await self.auth.db.flush()
        else:
            role.status = 0
            role.data_scope = data_scope

        self.auth.db.add(UserRolesModel(user_id=user_id, role_id=role.id))

        if role_code in {"owner", "admin"}:
            available_ids = set(
                await PackageService(self.auth).get_tenant_available_menu_ids(tenant_id)
            )
            current_ids = set(
                (
                    await self.auth.db.execute(
                        sa.select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == role.id)
                    )
                ).scalars().all()
            )
            for menu_id in available_ids - current_ids:
                self.auth.db.add(RoleMenusModel(role_id=role.id, menu_id=menu_id))
        await self.auth.db.flush()

    async def _remove_tenant_member_rbac(self, tenant_id: int, user_id: int) -> None:
        """撤销用户在指定租户内的全部角色，不影响其他租户。"""
        from app.api.v1.module_system.role.model import RoleModel
        from app.api.v1.module_system.user.model import UserRolesModel

        tenant_role_ids = set(
            (
                await self.auth.db.execute(
                    sa.select(RoleModel.id).where(RoleModel.tenant_id == tenant_id)
                )
            ).scalars().all()
        )
        if tenant_role_ids:
            await self.auth.db.execute(
                sa.delete(UserRolesModel).where(
                    UserRolesModel.user_id == user_id,
                    UserRolesModel.role_id.in_(tenant_role_ids),
                )
            )
        await self.auth.db.flush()

    @staticmethod
    def _plan_package_change(
        *,
        current_menu_ids: list[int] | set[int],
        package_menu_ids: list[int] | set[int],
        owner_minimum_menu_ids: set[int],
    ) -> PackageChangePlan:
        """计算套餐变更菜单差异，始终保留 owner 最低管理权限。"""
        current_ids = set(current_menu_ids)
        final_ids = set(package_menu_ids) | set(owner_minimum_menu_ids)
        return PackageChangePlan(
            current_menu_ids=current_ids,
            final_menu_ids=final_ids,
            removed_menu_ids=current_ids - final_ids,
            added_menu_ids=final_ids - current_ids,
        )

    async def plan_package_change(self, tenant_id: int, new_package_id: int) -> PackageChangePlan:
        """加载当前授权并生成可供预览和执行复用的套餐变更计划。"""
        from app.api.v1.module_platform.package.model import PackageModel
        from app.api.v1.module_platform.package.service import PackageService

        if self.auth.db is None:
            raise CustomException(msg="数据库会话不存在")
        tenant = await self.auth.db.get(TenantModel, tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")
        package = await self.auth.db.get(PackageModel, new_package_id)
        if not package:
            raise CustomException(msg="该数据不存在")
        if package.status != 0:
            raise CustomException(msg="目标套餐已停用")
        if getattr(tenant, "site_id", None) != getattr(package, "site_id", None):
            raise CustomException(msg="禁止跨站点配置套餐")

        package_service = PackageService(self.auth)
        current_menu_ids = await package_service.get_tenant_available_menu_ids(tenant_id)
        package_menu_ids = await package_service.get_package_menu_ids(new_package_id)
        owner_minimum_menu_ids = await PackageService.get_owner_minimum_menu_ids(self.auth.db)
        return self._plan_package_change(
            current_menu_ids=current_menu_ids,
            package_menu_ids=package_menu_ids,
            owner_minimum_menu_ids=owner_minimum_menu_ids,
        )

    async def apply_package_change(self, tenant_id: int, new_package_id: int) -> PackageChangePlan:
        """原子应用套餐与角色菜单授权，并立即失效权限缓存。"""
        from app.api.v1.module_platform.package.service import PackageService

        if self.auth.db is None:
            raise CustomException(msg="数据库会话不存在")
        plan = await self.plan_package_change(tenant_id, new_package_id)
        tenant = await self.auth.db.get(TenantModel, tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")

        tenant.package_id = new_package_id
        await PackageService.sync_tenant_plugins(
            self.auth.db,
            tenant_id,
            new_package_id,
        )
        await PackageService.sync_tenant_role_menus(
            self.auth.db,
            tenant_id,
            plan.final_menu_ids,
            owner_menu_ids=plan.final_menu_ids,
        )
        PackageService.invalidate_tenant_menu_cache(tenant_id, self.auth)
        from app.core.http_limit import TenantPackageRateLimiter

        TenantPackageRateLimiter.clear_cache()
        await self.auth.db.flush()
        logger.info(
            f"租户[{tenant_id}]套餐变更：package_id={new_package_id}, "
            f"available_menus={len(plan.final_menu_ids)}"
        )
        return plan

    @staticmethod
    async def ensure_tenant_owner(
        db: AsyncSession,
        tenant_id: int,
        user_id: int,
        *,
        is_default: int = 1,
    ) -> None:
        """幂等创建租户 owner 角色、成员关系、用户绑定和最低菜单授权。"""
        from app.api.v1.module_platform.menu.model import MenuModel
        from app.api.v1.module_platform.package.model import PackageMenuModel
        from app.api.v1.module_platform.package.service import PackageService
        from app.api.v1.module_system.role.model import RoleModel
        from app.api.v1.module_system.user.model import UserRolesModel

        membership = (
            await db.execute(
                sa.select(TenantUserModel)
                .where(
                    TenantUserModel.tenant_id == tenant_id,
                    TenantUserModel.user_id == user_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        membership_created = membership is None
        if membership is None:
            db.add(
                TenantUserModel(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role="owner",
                    is_default=is_default,
                )
            )
        else:
            membership.role = "owner"
            membership.is_default = is_default

        owner_role = (
            await db.execute(
                sa.select(RoleModel)
                .where(RoleModel.tenant_id == tenant_id, RoleModel.code == "owner")
                .limit(1)
            )
        ).scalar_one_or_none()
        if owner_role is None:
            owner_role = RoleModel(
                name="租户管理员",
                code="owner",
                tenant_id=tenant_id,
                order=1,
                status=0,
                data_scope=4,
                description="租户 owner 角色",
            )
            db.add(owner_role)
            await db.flush()
        else:
            owner_role.status = 0
            owner_role.data_scope = 4

        user_role = (
            await db.execute(
                sa.select(UserRolesModel)
                .where(
                    UserRolesModel.user_id == user_id,
                    UserRolesModel.role_id == owner_role.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if user_role is None:
            db.add(UserRolesModel(user_id=user_id, role_id=owner_role.id))
        await db.flush()

        owner_menu_ids = await PackageService.get_owner_minimum_menu_ids(db)
        tenant = (
            await db.execute(sa.select(TenantModel).where(TenantModel.id == tenant_id).limit(1))
        ).scalar_one_or_none()
        available_ids = set(owner_menu_ids)
        if tenant and tenant.package_id:
            package_menu_ids = set(
                (
                    await db.execute(
                        sa.select(PackageMenuModel.menu_id)
                        .join(MenuModel, MenuModel.id == PackageMenuModel.menu_id)
                        .where(
                            PackageMenuModel.package_id == tenant.package_id,
                            MenuModel.scope == "tenant",
                            MenuModel.status == 0,
                        )
                    )
                ).scalars().all()
            )
            available_ids.update(package_menu_ids)
        await PackageService.sync_tenant_role_menus(
            db,
            tenant_id,
            available_ids,
            owner_menu_ids=available_ids,
        )
        PackageService.invalidate_tenant_menu_cache(tenant_id)
        from app.api.v1.module_system.user.login_identifier import sync_user_login_identifiers
        from app.api.v1.module_system.user.model import UserModel

        user = await db.get(UserModel, user_id)
        if membership_created and user is not None:
            await sync_user_login_identifiers(db, user)

    @require_platform_admin
    async def detail(self, id: int) -> TenantOutSchema:
        """
        租户详情

        参数:
        - id (int): 租户ID

        返回:
        - TenantOutSchema: 租户详情
        """
        return await TenantCRUD(self.auth).get_or_404(id=id, out_schema=TenantOutSchema)

    @require_platform_admin
    async def page(
        self,
        page_no: int,
        page_size: int,
        search: TenantQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict:
        return await TenantCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=vars(search) if search else None,
            out_schema=TenantOutSchema,
        )

    @require_platform_admin
    async def create(self, data: TenantCreateSchema) -> TenantOutSchema:
        tenant_obj = await self.create_tenant_record(data)

        # 创建租户初始管理员
        # 1. 生成初始管理员用户名
        # 2. 检查用户名是否已存在
        # 3. 创建初始管理员用户
        username = f"{tenant_obj.code}_admin"
        from app.api.v1.module_system.user.crud import UserCRUD

        if await UserCRUD(self.auth).get(username=username):
            raise CustomException(msg=f"初始管理员用户名已存在: {username}，请更换租户编码后重试")

        password_length = 12
        characters = string.ascii_letters + string.digits + "!@#$%^&*"
        password = "".join(secrets.choice(characters) for _ in range(password_length))
        admin_data = {
            "username": username,
            "password": PwdUtil.hash_password(password=password),
            "name": f"{tenant_obj.name}管理员",
            "tenant_id": tenant_obj.id,
            "status": 0,
            "is_superuser": False,
        }
        try:
            user_obj = await UserCRUD(self.auth).create(data=admin_data)
            if not user_obj:
                raise CustomException(msg="创建租户初始管理员失败")
            await self.ensure_tenant_owner(
                self.auth.db,
                tenant_obj.id,
                user_obj.id,
            )
            if tenant_obj.package_id is not None:
                from app.api.v1.module_platform.package.service import PackageService

                await PackageService.sync_tenant_plugins(
                    self.auth.db,
                    tenant_obj.id,
                    tenant_obj.package_id,
                )
        except CustomException:
            raise
        except Exception as e:
            logger.error(f"为租户[{tenant_obj.name}]创建初始管理员失败: {e!s}")
            raise CustomException(msg="创建租户初始管理员失败") from e

        logger.info(f"为租户[{tenant_obj.name}]创建初始管理员成功，用户名: {username}")

        await self.auth.db.refresh(tenant_obj)
        result = TenantOutSchema.model_validate(tenant_obj)
        result.initial_admin = TenantInitialAdminSchema(username=username, password=password)

        return result

    async def create_tenant_record(
        self,
        data: TenantCreateSchema,
        *,
        preserve_integrity_error: bool = False,
    ) -> TenantModel:
        """只创建租户记录；调用方负责在同一外层事务中装配身份和权限。"""
        from app.api.v1.module_platform.package.model import PackageModel
        from app.api.v1.module_platform.site.model import SiteModel

        site = await self.auth.db.get(SiteModel, data.site_id)
        if site is None or site.is_deleted or site.status != 0:
            raise CustomException(msg="所属站点不存在或已停用")
        if data.package_id is not None:
            package = await self.auth.db.get(PackageModel, data.package_id)
            if package is None or package.is_deleted or package.status != 0:
                raise CustomException(msg="关联套餐不存在或已停用")
            if package.site_id != data.site_id:
                raise CustomException(msg="禁止为租户配置其他站点的套餐")
        if await TenantCRUD(self.auth).get(name=data.name):
            raise CustomException(msg="创建失败，名称已存在")
        if await TenantCRUD(self.auth).get(code=data.code):
            raise CustomException(msg="创建失败，编码已存在")
        await self._ensure_uscc_available(
            site_id=data.site_id,
            unified_social_credit_code=data.unified_social_credit_code,
        )

        if not preserve_integrity_error:
            try:
                return await TenantCRUD(self.auth).create(data=data)
            except CustomException as exc:
                self._raise_stable_uscc_conflict(exc)

        tenant_obj = TenantModel(**data.model_dump())
        self.auth.db.add(tenant_obj)
        try:
            await self.auth.db.flush()
        except IntegrityError as exc:
            if self._is_uscc_unique_conflict(exc):
                raise CustomException(
                    msg="统一社会信用代码在当前站点已存在",
                    status_code=409,
                ) from exc
            raise
        return tenant_obj

    @require_platform_admin
    async def update(self, id: int, data: TenantUpdateSchema) -> TenantOutSchema:
        """
        更新租户

        参数:
        - id (int): 租户ID
        - data (TenantUpdateSchema): 租户更新模型

        返回:
        - TenantOutSchema: 租户详情
        """
        obj = await TenantCRUD(self.auth).get_or_404(id=id)

        old_package_id = obj.package_id

        from app.api.v1.module_platform.package.model import PackageModel
        from app.api.v1.module_platform.site.model import SiteModel

        target_site_id = data.site_id if data.site_id is not None else obj.site_id
        target_package_id = data.package_id if data.package_id is not None else old_package_id

        site = await self.auth.db.get(SiteModel, target_site_id)
        if site is None or site.is_deleted or site.status != 0:
            raise CustomException(msg="所属站点不存在或已停用")
        if target_package_id is not None:
            package = await self.auth.db.get(PackageModel, target_package_id)
            if package is None or package.is_deleted or package.status != 0:
                raise CustomException(msg="关联套餐不存在或已停用")
            if package.site_id != target_site_id:
                raise CustomException(msg="租户与套餐必须属于同一站点", status_code=409)

        if id == 1:
            if data.code is not None and data.code != obj.code:
                raise CustomException(msg="系统租户编码不可修改")
            if target_site_id != obj.site_id:
                raise CustomException(msg="系统租户所属站点不可修改", status_code=409)

        # 套餐变更：仅超管可操作，防止租户管理员自行升级/降级套餐
        if data.package_id is not None and data.package_id != old_package_id:
            if not self.auth.user or not self.auth.user.is_superuser:
                raise CustomException(msg="仅平台管理员可变更租户套餐")

        if data.name is not None:
            exist = await TenantCRUD(self.auth).get(name=data.name)
            if exist and exist.id != id:
                raise CustomException(msg="更新失败，名称重复")
        if data.code is not None:
            exist = await TenantCRUD(self.auth).get(code=data.code)
            if exist and exist.id != id:
                raise CustomException(msg="更新失败，编码重复")
        target_uscc = (
            data.unified_social_credit_code
            if "unified_social_credit_code" in data.model_fields_set
            else getattr(obj, "unified_social_credit_code", None)
        )
        await self._ensure_uscc_available(
            site_id=target_site_id,
            unified_social_credit_code=target_uscc,
            exclude_tenant_id=id,
        )

        update_data = data.model_dump(exclude_unset=True, exclude={"package_id"})
        try:
            updated = await TenantCRUD(self.auth).update(id=id, data=update_data)
        except CustomException as exc:
            self._raise_stable_uscc_conflict(exc)
        if not updated:
            raise CustomException(msg="更新失败")

        # 套餐变更统一走同一应用入口，避免平台修改与支付激活行为漂移。
        if data.package_id is not None and data.package_id != old_package_id:
            await self.apply_package_change(id, data.package_id)

        result = TenantOutSchema.model_validate(updated)
        return result

    @require_platform_admin
    async def delete(self, ids: list[int]) -> None:
        """
        批量删除租户（含级联资源检查：用户/部门/角色/岗位）

        参数:
        - ids (list[int]): 租户ID列表

        返回:
        - None
        """
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        if 1 in ids:
            raise CustomException(msg="系统租户不允许删除")
        from app.api.v1.module_system.dept.crud import DeptCRUD
        from app.api.v1.module_system.position.crud import PositionCRUD
        from app.api.v1.module_system.role.crud import RoleCRUD
        from app.api.v1.module_system.user.crud import UserCRUD

        for tid in ids:
            reasons: list[str] = []
            if await UserCRUD(self.auth).get_list(search={"tenant_id": tid}):
                reasons.append("用户")
            if await DeptCRUD(self.auth).get_list(search={"tenant_id": tid}):
                reasons.append("部门")
            if await RoleCRUD(self.auth).get_list(search={"tenant_id": tid}):
                reasons.append("角色")
            if await PositionCRUD(self.auth).get_list(search={"tenant_id": tid}):
                reasons.append("岗位")
            if reasons:
                raise CustomException(msg=f"租户下已存在{'/'.join(reasons)}，操作失败")

        await TenantCRUD(self.auth).delete(ids=ids)

    @require_platform_admin
    async def set_available(self, data: TenantBatchStatusSchema) -> None:
        """
        批量设置租户状态

        参数:
        - data (TenantBatchStatusSchema): 批量正常/暂停状态设置

        返回:
        - None
        """
        if data.status != TenantStatus.ACTIVE and 1 in data.ids:
            raise CustomException(msg="系统租户必须保持正常状态")
        await TenantCRUD(self.auth).set(ids=data.ids, status=data.status)

    @require_platform_admin
    async def toggle_status(self, id: int) -> None:
        """
        切换单个租户的正常/暂停状态

        参数:
        - id (int): 租户ID

        返回:
        - None
        """
        obj = await TenantCRUD(self.auth).get_or_404(id=id)
        if id == 1:
            raise CustomException(msg="系统租户不允许禁用")
        if obj.status not in {TenantStatus.ACTIVE, TenantStatus.SUSPENDED}:
            raise CustomException(msg="当前生命周期状态不支持手工启用/暂停，请先续期或恢复")
        new_status = (
            TenantStatus.SUSPENDED
            if obj.status == TenantStatus.ACTIVE
            else TenantStatus.ACTIVE
        )
        await TenantCRUD(self.auth).set(ids=[id], status=new_status)

    @require_platform_admin
    async def get_tenant_users(self, tenant_id: int) -> list[TenantUserOutSchema]:
        """获取租户下的用户列表"""
        from sqlalchemy import select

        from app.api.v1.module_system.user.model import UserModel

        stmt = (
            select(TenantUserModel, UserModel)
            .join(UserModel, UserModel.id == TenantUserModel.user_id)
            .where(TenantUserModel.tenant_id == tenant_id)
            .order_by(TenantUserModel.is_default.desc(), TenantUserModel.id)
        )
        result = await self.auth.db.execute(stmt)
        rows = result.all()

        users = []
        for tu, u in rows:
            users.append(
                TenantUserOutSchema(
                    id=tu.id,
                    user_id=tu.user_id,
                    tenant_id=tu.tenant_id,
                    role=tu.role,
                    is_default=tu.is_default,
                    create_time=tu.create_time,
                    username=u.username,
                    name=u.name,
                )
            )
        return users

    @require_platform_admin
    async def add_tenant_user(self, tenant_id: int, data: TenantUserAddSchema) -> None:
        """
        向租户添加用户

        参数:
        - tenant_id (int): 租户ID
        - data (TenantUserAddSchema): 用户添加参数

        返回:
        - None
        """
        # 验证租户存在
        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")

        # 验证用户存在
        from app.api.v1.module_system.user.crud import UserCRUD

        user = await UserCRUD(self.auth).get(id=data.user_id)
        if not user:
            raise CustomException(msg="该数据不存在")

        # 检查是否已关联
        from sqlalchemy import select

        exist_stmt = (
            select(TenantUserModel)
            .where(
                TenantUserModel.user_id == data.user_id,
                TenantUserModel.tenant_id == tenant_id,
            )
            .limit(1)
        )
        result = await self.auth.db.execute(exist_stmt)
        if result.scalar_one_or_none():
            raise CustomException(msg="该用户已关联此租户")

        # 如果设为默认租户，先取消其他默认
        if data.is_default == 1:
            await self.auth.db.execute(
                sa.update(TenantUserModel).where(TenantUserModel.user_id == data.user_id).values(is_default=0)
            )
        elif data.is_default == 0:
            # 检查是否是该用户的第一个租户关联
            count_result = await self.auth.db.execute(
                select(sa.func.count()).select_from(TenantUserModel).where(TenantUserModel.user_id == data.user_id)
            )
            count = count_result.scalar()
            if count == 0:
                # 第一个租户自动设为默认
                data.is_default = 1

        from datetime import datetime

        tu = TenantUserModel(
            user_id=data.user_id,
            tenant_id=tenant_id,
            role=data.role,
            is_default=data.is_default,
            create_time=datetime.now(),
        )
        self.auth.db.add(tu)
        await self.auth.db.flush()
        from app.api.v1.module_system.user.login_identifier import sync_user_login_identifiers

        await sync_user_login_identifiers(self.auth.db, user)
        await self._replace_tenant_member_rbac(tenant_id, data.user_id, data.role)

        logger.info(f"向租户[{tenant.name}]添加用户[{user.username}]成功, role={data.role}")

    @require_platform_admin
    async def remove_tenant_user(self, tenant_id: int, user_id: int) -> None:
        """
        从租户移除用户

        参数:
        - tenant_id (int): 租户ID
        - user_id (int): 用户ID

        返回:
        - None
        """
        from sqlalchemy import select

        # 查找关联记录
        exist_stmt = (
            select(TenantUserModel)
            .where(
                TenantUserModel.user_id == user_id,
                TenantUserModel.tenant_id == tenant_id,
            )
            .limit(1)
        )
        result = await self.auth.db.execute(exist_stmt)
        tu = result.scalar_one_or_none()
        if not tu:
            raise CustomException(msg="该用户未关联此租户")

        # 不允许移除租户最后一个 owner
        if tu.role == "owner":
            count_result = await self.auth.db.execute(
                select(sa.func.count())
                .select_from(TenantUserModel)
                .where(
                    TenantUserModel.tenant_id == tenant_id,
                    TenantUserModel.role == "owner",
                )
            )
            owner_count = count_result.scalar()
            if owner_count <= 1:
                raise CustomException(msg="租户至少需要保留一个拥有者(owner)")

        await self._remove_tenant_member_rbac(tenant_id, user_id)
        await self.auth.db.delete(tu)
        await self.auth.db.flush()
        from app.api.v1.module_system.user.login_identifier import sync_user_login_identifiers
        from app.api.v1.module_system.user.model import UserModel

        user = await self.auth.db.get(UserModel, user_id)
        if user is not None:
            await sync_user_login_identifiers(self.auth.db, user)

        logger.info(f"从租户[{tenant_id}]移除用户[{user_id}]成功")

    async def get_quota(self, tenant_id: int) -> dict:
        """
        获取租户配额（从关联套餐读取，系统租户返回无限配额）

        参数:
        - tenant_id (int): 租户ID

        返回:
        - dict: 配额信息
        """
        if tenant_id == 1:
            return {
                "tenant_id": 1,
                "max_users": 999999,
                "max_roles": 999999,
                "max_storage_mb": 999999,
                "max_depts": 999999,
                "package_name": "系统租户(无限)",
            }
        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")
        if not tenant.package_id:
            return {
                "tenant_id": tenant.id,
                "max_users": 0,
                "max_roles": 0,
                "max_storage_mb": 0,
                "max_depts": 0,
                "package_name": "未绑定套餐",
            }
        from app.api.v1.module_platform.package.crud import PackageCRUD

        pkg = await PackageCRUD(self.auth).get(id=tenant.package_id)
        if not pkg:
            return {
                "tenant_id": tenant.id,
                "max_users": 0,
                "max_roles": 0,
                "max_storage_mb": 0,
                "max_depts": 0,
                "package_name": "套餐已删除",
            }
        return {
            "tenant_id": tenant.id,
            "max_users": pkg.max_users,
            "max_roles": pkg.max_roles,
            "max_storage_mb": getattr(pkg, "max_storage_mb", 0),
            "max_depts": pkg.max_depts,
            "package_name": pkg.name,
        }

    async def check_quota(self, tenant_id: int, resource_type: str) -> None:
        """检查租户配额是否充足，不足时抛出异常（系统租户跳过检查）"""
        if tenant_id == 1:
            return
        from sqlalchemy import func, select

        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant or not tenant.package_id:
            return

        from app.api.v1.module_platform.package.crud import PackageCRUD

        pkg = await PackageCRUD(self.auth).get(id=tenant.package_id)
        if not pkg:
            return

        field_map = {
            "user": "max_users",
            "role": "max_roles",
            "dept": "max_depts",
            "storage": "max_storage_mb",
        }
        if resource_type not in field_map:
            return

        max_field = field_map[resource_type]
        max_limit = getattr(pkg, max_field, None)
        if max_limit is None or max_limit == 0:
            return

        if resource_type == "user":
            from app.api.v1.module_system.user.model import UserModel

            count_stmt = (
                select(func.count())
                .select_from(UserModel)
                .where(
                    UserModel.tenant_id == tenant_id,
                    UserModel.is_deleted.is_(False),
                )
            )
        elif resource_type == "role":
            from app.api.v1.module_system.role.model import RoleModel

            count_stmt = (
                select(func.count())
                .select_from(RoleModel)
                .where(
                    RoleModel.tenant_id == tenant_id,
                    RoleModel.is_deleted.is_(False),
                )
            )
        elif resource_type == "dept":
            from app.api.v1.module_system.dept.model import DeptModel

            count_stmt = (
                select(func.count())
                .select_from(DeptModel)
                .where(
                    DeptModel.tenant_id == tenant_id,
                    DeptModel.is_deleted.is_(False),
                )
            )
        elif resource_type == "storage":
            # storage 的实际容量校验在文件上传时进行，此处仅检查是否有配额
            return

        result = await self.auth.db.execute(count_stmt)
        current_count = result.scalar() or 0

        if current_count >= max_limit:
            resource_labels = {"user": "用户", "role": "角色", "dept": "部门"}
            raise CustomException(
                msg=f"租户{resource_labels.get(resource_type, resource_type)}数量已达套餐上限（{max_limit}），无法继续创建"
            )

    async def get_config(self, tenant_id: int) -> dict:
        """
        获取租户所有配置（从租户主表读取，返回原始 dict 供内部使用）

        参数:
        - tenant_id (int): 租户ID

        返回:
        - dict: 配置字典
        """
        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")

        config = {field: getattr(tenant, field, None) for field in TENANT_CONFIG_FIELDS}
        return config

    @staticmethod
    def _normalize_config_input(config: dict | list[TenantConfigItem]) -> dict:
        """把前端列表契约和历史 dict 契约统一成租户主表字段名。"""
        if isinstance(config, list):
            raw_config = {item.key: item.value for item in config}
        else:
            raw_config = config

        normalized: dict = {}
        for key, value in raw_config.items():
            field = TENANT_BRAND_CONFIG_ALIASES.get(key, key)
            if field in TENANT_CONFIG_FIELDS:
                normalized[field] = value
        return normalized

    @staticmethod
    def _config_to_items(config: dict) -> list[TenantConfigOutSchema]:
        items = [
            TenantConfigOutSchema(config_key=k, config_value=str(v) if v is not None else None)
            for k, v in config.items()
        ]
        for field, alias in TENANT_BRAND_CONFIG_FIELDS.items():
            value = config.get(field)
            items.append(TenantConfigOutSchema(config_key=alias, config_value=str(value) if value is not None else None))
        return items

    @require_platform_admin
    async def get_config_items(self, tenant_id: int) -> list[TenantConfigOutSchema]:
        """获取租户所有配置（对外接口，返回结构化列表）"""
        config = await self.get_config(tenant_id)
        return self._config_to_items(config)

    async def get_self_brand_config_items(self) -> list[TenantConfigOutSchema]:
        """获取当前登录租户可自助维护的品牌配置。"""
        if not self.auth.tenant_id:
            raise CustomException(msg="当前会话缺少租户信息")
        config = await self.get_config(self.auth.tenant_id)
        brand_config = {field: config.get(field) for field in TENANT_SELF_BRAND_CONFIG_FIELDS}
        return self._config_to_items(brand_config)

    @staticmethod
    async def get_config_cache(redis: Redis, tenant_id: int) -> dict:
        """
        从 Redis 缓存获取租户配置，缓存未命中则从 DB 加载并回写缓存

        参数:
        - redis (Redis): Redis 客户端实例
        - tenant_id (int): 租户ID

        返回:
        - dict: 租户配置字典
        """
        redis_key = f"{RedisInitKeyConfig.TENANT_CONFIG.key}:{tenant_id}"
        redis_config = await RedisCURD(redis).get(key=redis_key)

        if redis_config:
            try:
                return json.loads(redis_config)
            except Exception as e:
                logger.error(f"解析租户配置数据失败: {e}")

        logger.info(f"Redis 中没有租户[{tenant_id}]配置数据，从数据库中加载")
        from app.core.database import async_db_session

        async with async_db_session() as session:
            async with session.begin():
                from app.core.base_schema import AuthSchema as _AuthSchema

                _auth = _AuthSchema.for_platform_global_read(session)
                svc = TenantService(_auth)
                config = await svc.get_config(tenant_id)
                await TenantService._sync_configs_to_redis(redis, tenant_id, config)
                logger.info("✅ 已从数据库加载租户配置到缓存")

        return config

    @staticmethod
    async def get_config_cache_items(redis: Redis, tenant_id: int) -> list[TenantConfigOutSchema]:
        """获取租户缓存配置（对外接口，返回结构化列表）"""
        config = await TenantService.get_config_cache(redis, tenant_id)
        return TenantService._config_to_items(config)

    @staticmethod
    async def _sync_configs_to_redis(redis: Redis, tenant_id: int, config: dict) -> None:
        """将租户配置写入 Redis 缓存"""
        redis_key = f"{RedisInitKeyConfig.TENANT_CONFIG.key}:{tenant_id}"
        value = json.dumps(config, ensure_ascii=False)
        await RedisCURD(redis).set(key=redis_key, value=value, expire=None)

    @staticmethod
    async def _del_configs_from_redis(redis: Redis, tenant_id: int) -> None:
        """删除租户配置的 Redis 缓存"""
        redis_key = f"{RedisInitKeyConfig.TENANT_CONFIG.key}:{tenant_id}"
        await RedisCURD(redis).delete(redis_key)

    @require_platform_admin
    async def update_config(self, redis: Redis, tenant_id: int, config: dict | list[TenantConfigItem]) -> list[TenantConfigOutSchema]:
        """
        更新租户配置（同步 Redis 缓存）

        参数:
        - redis (Redis): Redis 客户端
        - tenant_id (int): 租户ID
        - config (dict): 配置字典

        返回:
        - list[TenantConfigOutSchema]: 更新后的配置项列表
        """
        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")

        normalized_config = self._normalize_config_input(config)
        for field, value in normalized_config.items():
            setattr(tenant, field, value)

        await self.auth.db.flush()

        # 刷新 DB 数据并同步到 Redis
        new_config = await self.get_config(tenant_id)
        await TenantService._sync_configs_to_redis(redis, tenant_id, new_config)
        logger.info(f"租户[{tenant_id}]配置已更新")
        return self._config_to_items(new_config)

    async def update_self_brand_config(
        self,
        redis: Redis,
        config: dict | list[TenantConfigItem],
    ) -> list[TenantConfigOutSchema]:
        """当前租户自助更新品牌配置。

        只允许写入展示品牌与法务链接字段，不允许通过自助入口修改套餐、状态、版本、
        源码地址等平台治理字段。
        """
        if not self.auth.tenant_id:
            raise CustomException(msg="当前会话缺少租户信息")

        tenant_id = self.auth.tenant_id
        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")

        normalized_config = {
            field: value
            for field, value in self._normalize_config_input(config).items()
            if field in TENANT_SELF_BRAND_CONFIG_FIELDS
        }
        for field, value in normalized_config.items():
            setattr(tenant, field, value)

        await self.auth.db.flush()

        new_config = await self.get_config(tenant_id)
        await TenantService._sync_configs_to_redis(redis, tenant_id, new_config)
        brand_config = {field: new_config.get(field) for field in TENANT_SELF_BRAND_CONFIG_FIELDS}
        logger.info(f"租户[{tenant_id}]自助品牌配置已更新")
        return self._config_to_items(brand_config)

    @staticmethod
    async def init_cache(redis: Redis) -> None:
        """
        初始化所有租户配置到 Redis 缓存（应用启动时调用）。

        参数:
        - redis (Redis): Redis 客户端实例

        返回:
        - None
        """
        from sqlalchemy import select

        from app.core.database import async_db_session

        async with async_db_session() as session:
            async with session.begin():
                stmt = select(TenantModel)
                result = await session.execute(stmt)
                tenants = result.scalars().all()

                for tenant in tenants:
                    config_fields = [
                        "name", "description", "version", "logo_url", "favicon",
                        "login_bg", "copyright", "keep_record", "help_doc", "privacy",
                        "clause", "git_code",
                    ]
                    config = {field: getattr(tenant, field, None) for field in config_fields}

                    await TenantService._sync_configs_to_redis(redis, tenant.id, config)
                    logger.info(f"✅ 租户[{tenant.name}](id={tenant.id}) 配置已缓存到 Redis")

    @require_platform_admin
    async def renew(self, tenant_id: int, end_time: str) -> TenantOutSchema:
        """租户续期：延长 end_time 并恢复为 active 状态

        仅 active(0)/grace(1)/suspended(2) 状态可续期。
        expired(4)/frozen(3)/archived(5) 不可续期。

        参数:
        - tenant_id (int): 租户ID
        - end_time (str): 新的结束时间

        返回:
        - dict: 更新后的租户信息
        """
        from datetime import datetime

        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")

        if tenant.status not in (0, 1, 2):
            status_labels = {0: "正常", 1: "宽限期", 2: "暂停", 3: "冻结", 4: "过期", 5: "归档"}
            current_label = status_labels.get(tenant.status, str(tenant.status))
            raise CustomException(msg=f"当前租户状态为「{current_label}」，仅正常/宽限期/暂停状态可续期")

        new_end = datetime.fromisoformat(end_time) if isinstance(end_time, str) else end_time
        if new_end <= datetime.now():
            raise CustomException(msg="续期结束时间必须晚于当前时间")

        tenant.end_time = new_end
        tenant.status = 0
        tenant.grace_start_time = None

        await self.auth.db.flush()
        logger.info(f"租户[{tenant.name}]续期成功, 新的结束时间: {end_time}")

        return TenantOutSchema.model_validate(tenant)

    @require_platform_admin
    async def package_change_preview(self, tenant_id: int, new_package_id: int) -> PackageChangePreviewOut:
        """
        套餐变更影响预览

        返回受影响角色、菜单清单、配额对比等，供超管确认后再执行变更。

        参数:
        - tenant_id (int): 租户ID
        - new_package_id (int): 目标套餐ID

        返回:
        - PackageChangePreviewOut: 预览结果
        """
        from sqlalchemy import func, select

        from app.api.v1.module_platform.menu.model import MenuModel
        from app.api.v1.module_platform.package.crud import PackageCRUD
        from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
        from app.api.v1.module_system.user.model import UserModel

        tenant = await TenantCRUD(self.auth).get(id=tenant_id)
        if not tenant:
            raise CustomException(msg="该数据不存在")

        new_package = await PackageCRUD(self.auth).get(id=new_package_id)
        if not new_package:
            raise CustomException(msg="该数据不存在")

        plan = await self.plan_package_change(tenant_id, new_package_id)
        removed_ids = plan.removed_menu_ids
        added_ids = plan.added_menu_ids

        removed_menus = []
        added_menus = []
        if removed_ids:
            menu_stmt = select(MenuModel).where(MenuModel.id.in_(removed_ids))
            menu_result = await self.auth.db.execute(menu_stmt)
            removed_menus = [{"id": m.id, "name": m.name, "route_path": m.route_path} for m in menu_result.scalars().all()]
        if added_ids:
            menu_stmt = select(MenuModel).where(MenuModel.id.in_(added_ids))
            menu_result = await self.auth.db.execute(menu_stmt)
            added_menus = [{"id": m.id, "name": m.name, "route_path": m.route_path} for m in menu_result.scalars().all()]

        # 受影响角色
        role_stmt = select(RoleModel).where(RoleModel.tenant_id == tenant_id)
        role_result = await self.auth.db.execute(role_stmt)
        roles = role_result.scalars().all()

        affected_roles = []
        total_affected_users = 0
        for role in roles:
            # 查该角色下有多少菜单会被移除
            role_menu_stmt = select(RoleMenusModel.menu_id).where(RoleMenusModel.role_id == role.id)
            rm_result = await self.auth.db.execute(role_menu_stmt)
            role_menu_ids = {row[0] for row in rm_result.all()}
            affected_menu_count = len(role_menu_ids & removed_ids)

            # 查该角色下用户数
            user_count_stmt = select(func.count()).select_from(UserModel).join(UserModel.roles).where(RoleModel.id == role.id)
            uc_result = await self.auth.db.execute(user_count_stmt)
            user_count = uc_result.scalar() or 0

            affected_roles.append(
                {
                    "id": role.id,
                    "name": role.name,
                    "code": role.code,
                    "affected_menu_count": affected_menu_count,
                    "user_count": user_count,
                }
            )
            total_affected_users += user_count

        # 配额对比（从套餐读取）
        old_pkg = None
        if tenant.package_id:
            old_pkg = await PackageCRUD(self.auth).get(id=tenant.package_id)
        quota_changes = {
            "max_users": {
                "current": old_pkg.max_users if old_pkg else 0,
                "new": new_package.max_users,
            },
            "max_roles": {
                "current": old_pkg.max_roles if old_pkg else 0,
                "new": new_package.max_roles,
            },
            "max_depts": {
                "current": old_pkg.max_depts if old_pkg else 0,
                "new": new_package.max_depts,
            },
        }

        return PackageChangePreviewOut(
            new_package_id=new_package.id,
            new_package_name=new_package.name,
            affected_roles=affected_roles,
            removed_menus=removed_menus,
            added_menus=added_menus,
            quota_changes=quota_changes,
            total_affected_users=total_affected_users,
        )

    @staticmethod
    async def check_tenant_expiry() -> None:
        """定时任务：多阶段租户到期自动处理

        PRD §9 到期阶段：
          grace(1)   → 到期后第 1-7 天，仅提醒
          suspended(2) → 到期后第 8-14 天，禁用登录
          frozen(3)     → 到期后第 15-30 天，禁止登录与访问
          expired(4)    → 第 31 天起，归档候选
        """
        from datetime import datetime

        from sqlalchemy import text

        from app.core.database import async_db_session

        now = datetime.now()

        async with async_db_session() as session:
            # 持续扫描所有未终结状态，保证宽限/暂停/冻结继续向后迁移。
            rows = await session.execute(
                text(
                    "SELECT id, name, contact_email, contact_name, end_time, status "
                    "FROM platform_tenant WHERE status IN ('0', '1', '2', '3') "
                    "AND end_time IS NOT NULL AND end_time < :now"
                ),
                {"now": now},
            )
            expired_tenants = rows.fetchall()

            for t in expired_tenants:
                tenant_id, tenant_name, email, contact_name, end_time, cur_status = t
                if isinstance(end_time, str):
                    end_time = datetime.fromisoformat(end_time)
                days_past = (now - end_time).days if end_time else 0

                if days_past <= 7:
                    new_status, label = 1, "宽限期"
                elif days_past <= 14:
                    new_status, label = 2, "已停用"
                elif days_past <= 30:
                    new_status, label = 3, "已冻结"
                else:
                    new_status, label = 4, "已过期"

                if new_status == cur_status:
                    continue

                await session.execute(
                    text("UPDATE platform_tenant SET status = :s WHERE id = :tid"),
                    {"s": new_status, "tid": tenant_id},
                )
                logger.info(f"租户状态切换: id={tenant_id} name={tenant_name} status={cur_status}→{new_status} ({label})")

                # 发送通知邮件
                if email:
                    await TenantService._send_expiry_email(
                        tenant_name, contact_name, email, end_time, days_past, label
                    )

            await session.commit()

        logger.info(f"到期检查完成，处理了 {len(expired_tenants)} 个过期租户")

    @staticmethod
    async def send_grace_reminders() -> None:
        """定时任务：向宽限期租户发送续费提醒邮件（每天 09:00）"""
        from datetime import datetime

        from sqlalchemy import text

        from app.core.database import async_db_session

        async with async_db_session() as session:
            rows = await session.execute(
                text(
                    "SELECT id, name, contact_email, contact_name, end_time FROM platform_tenant "
                    "WHERE status = '1' AND contact_email IS NOT NULL AND contact_email != ''"
                )
            )
            grace_tenants = rows.fetchall()

            sent = 0
            for t in grace_tenants:
                tenant_id, name, email, contact_name, end_time = t
                days_past = (datetime.now() - end_time).days if end_time else 0

                try:
                    ok = await TenantService._send_renew_email(name, contact_name, email, days_past)
                    if ok:
                        sent += 1
                except Exception as e:
                    logger.warning(f"续费提醒邮件发送失败: tenant_id={tenant_id}, err={e}")

            logger.info(f"续费提醒完成，共发送 {sent}/{len(grace_tenants)} 封邮件")

    @staticmethod
    async def clean_expired_tenants() -> None:
        """定时任务：将过期超 90 天租户归档，清理旧审计日志（每月 1 号 02:00）"""
        from datetime import datetime, timedelta

        from sqlalchemy import text

        from app.core.database import async_db_session

        cutoff = datetime.now() - timedelta(days=90)

        async with async_db_session() as session:
            # 归档过期租户
            result = await session.execute(
                text("SELECT COUNT(*) FROM platform_tenant WHERE status = '4' AND end_time < :cutoff"),
                {"cutoff": cutoff},
            )
            count = result.scalar() or 0
            if count > 0:
                await session.execute(
                    text("UPDATE platform_tenant SET status = '5' WHERE status = '4' AND end_time < :cutoff"),
                    {"cutoff": cutoff},
                )
                await session.commit()
                logger.info(f"已将 {count} 个过期超过 90 天的租户标记为归档")

            await session.commit()

    @staticmethod
    async def _send_expiry_email(
        tenant_name: str,
        contact_name: str | None,
        email: str,
        end_time: object,
        days_past: int,
        label: str,
    ) -> None:
        """发送租户状态变更通知邮件"""
        from app.config.setting import settings
        from app.utils.email_util import render_template, send_email

        if not settings.SMTP_HOST:
            return

        smtp_config = {
            "host": settings.SMTP_HOST,
            "port": settings.SMTP_PORT,
            "username": settings.SMTP_USER,
            "password": settings.SMTP_PASSWORD,
            "from_addr": settings.SMTP_FROM,
            "starttls": settings.SMTP_TLS,
            "ssl_tls": settings.SMTP_SSL,
        }
        end_str = end_time.strftime("%Y-%m-%d") if hasattr(end_time, "strftime") else str(end_time)

        try:
            html_body = await render_template(
                "email/tenant_expiry_notice.html",
                tenant_name=tenant_name,
                contact_name=contact_name or "管理员",
                days_expired=days_past,
                status_name=label,
                end_time=end_str,
                site_url=getattr(settings, "SITE_URL", ""),
            )
            await send_email(
                smtp_config=smtp_config,
                recipients=[email],
                subject=f"【重要】租户 {tenant_name} 服务状态变更通知",
                body=html_body,
                html=True,
            )
        except Exception as e:
            logger.warning(f"到期通知邮件发送失败: tenant={tenant_name}, err={e}")

    @staticmethod
    async def _send_renew_email(name: str, contact_name: str | None, email: str, days_past: int) -> bool:
        """发送续费提醒邮件"""
        from app.config.setting import settings
        from app.utils.email_util import render_template, send_email

        if not settings.SMTP_HOST:
            return False

        smtp_config = {
            "host": settings.SMTP_HOST,
            "port": settings.SMTP_PORT,
            "username": settings.SMTP_USER,
            "password": settings.SMTP_PASSWORD,
            "from_addr": settings.SMTP_FROM,
            "starttls": settings.SMTP_TLS,
            "ssl_tls": settings.SMTP_SSL,
        }

        html_body = await render_template(
            "email/tenant_renew_reminder.html",
            tenant_name=name,
            contact_name=contact_name or "管理员",
            days_expired=days_past,
            renew_url=f"{getattr(settings, 'SITE_URL', '')}/tenant/order/create",
        )
        return await send_email(
            smtp_config=smtp_config,
            recipients=[email],
            subject=f"【续费提醒】租户 {name} 服务即将到期",
            body=html_body,
            html=True,
        )
