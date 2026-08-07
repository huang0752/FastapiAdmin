import io
import json
import secrets
from typing import Any

import pandas as pd
from fastapi import UploadFile, status
from redis.asyncio.client import Redis
from sqlalchemy import select

from app.api.v1.module_platform.email.service import EmailSendService
from app.api.v1.module_platform.menu.crud import MenuCRUD
from app.api.v1.module_platform.menu.schema import MenuOutSchema
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_platform.tenant.model import TenantUserModel
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.dept.crud import DeptCRUD
from app.api.v1.module_system.position.crud import PositionCRUD
from app.api.v1.module_system.role.crud import RoleCRUD
from app.core.assembly import filter_menu_tree_by_assembly
from app.core.base_schema import AuthSchema, BatchSetAvailable
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD
from app.utils.common_util import traversal_to_tree
from app.utils.excel_util import ExcelUtil
from app.utils.hash_bcrpy_util import PwdUtil

from .crud import UserCRUD
from .schema import (
    CurrentUserUpdateSchema,
    ResetPasswordSchema,
    UserChangePasswordSchema,
    UserCreateSchema,
    UserForgetPasswordEmailCodeSchema,
    UserForgetPasswordEmailResetSchema,
    UserForgetPasswordSchema,
    UserOutSchema,
    UserQueryParam,
    UserRegisterSchema,
    UserUpdateSchema,
)

PASSWORD_RESET_CODE_TTL_SECONDS = 5 * 60
PASSWORD_RESET_SEND_INTERVAL_SECONDS = 60
PASSWORD_RESET_HOURLY_LIMIT = 5
PASSWORD_RESET_TEMPLATE_CODE = "reset_password_code"


class UserService:
    """用户管理服务"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def _get_unique_user_by_username(self, username: str):
        """认证前账号查询必须在全局范围内唯一，避免跨租户误命中。"""
        from app.api.v1.module_system.auth.service import get_unique_user_by_username

        return await get_unique_user_by_username(
            self.auth.db,
            username,
            self.auth.site_id,
        )

    def _auth_for_user(self, user) -> AuthSchema:
        """为认证前唯一解析出的账号建立显式租户写入上下文。"""
        return AuthSchema(
            db=self.auth.db,
            user=user,
            tenant_id=user.tenant_id,
            check_data_scope=False,
        )

    async def _ensure_tenant_membership(self, user_id: int, tenant_id: int | None, role: str = "member") -> None:
        """确保用户和租户关系存在，用于登录后的租户上下文校验。"""
        if not tenant_id:
            return
        stmt = (
            select(TenantUserModel)
            .where(TenantUserModel.user_id == user_id, TenantUserModel.tenant_id == tenant_id)
            .limit(1)
        )
        result = await self.auth.db.execute(stmt)
        if result.scalar_one_or_none():
            return
        has_any_stmt = select(TenantUserModel).where(TenantUserModel.user_id == user_id).limit(1)
        has_any_result = await self.auth.db.execute(has_any_stmt)
        self.auth.db.add(
            TenantUserModel(
                user_id=user_id,
                tenant_id=tenant_id,
                role=role,
                is_default=0 if has_any_result.scalar_one_or_none() else 1,
            )
        )
        await self.auth.db.flush()
        from .login_identifier import sync_user_login_identifiers
        from .model import UserModel

        user = await self.auth.db.get(UserModel, user_id)
        if user is not None:
            await sync_user_login_identifiers(self.auth.db, user)

    async def detail(self, id: int) -> UserOutSchema:
        user = await UserCRUD(self.auth).get_or_404(id=id)
        result = UserOutSchema.model_validate(user)
        if user.dept_id:
            dept = await DeptCRUD(self.auth).get(id=user.dept_id)
            result.dept_name = dept.name if dept else None
        return result

    async def get_list(
        self,
        search: UserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> list[UserOutSchema]:
        user_list = await UserCRUD(self.auth).get_list(search=vars(search) if search else None, order_by=order_by)
        return [UserOutSchema.model_validate(user) for user in user_list]

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: UserQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict:
        offset = (page_no - 1) * page_size
        return await UserCRUD(self.auth).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=vars(search) if search else None,
            out_schema=UserOutSchema,
        )

    async def create(self, data: UserCreateSchema) -> UserOutSchema:
        if not data.username:
            raise CustomException(msg="用户名不能为空")
        if data.is_superuser:
            raise CustomException(msg="不允许创建超级管理员")
        user = await UserCRUD(self.auth).get(username=data.username)
        if user:
            raise CustomException(msg="已存在相同用户名称的账号")

        if data.dept_id:
            dept = await DeptCRUD(self.auth).get(id=data.dept_id)
            if not dept:
                raise CustomException(msg="该数据不存在")

        await TenantService(self.auth).check_quota(self.auth.tenant_id, "user")

        if data.password:
            data.password = PwdUtil.hash_password(password=data.password)
        user_dict = data.model_dump(exclude_unset=True, exclude={"role_ids", "position_ids"})
        new_user = await UserCRUD(self.auth).create(data=user_dict)
        await self._ensure_tenant_membership(new_user.id, new_user.tenant_id)
        if data.role_ids and len(data.role_ids) > 0:
            await UserCRUD(self.auth).set_user_roles(user_ids=[new_user.id], role_ids=data.role_ids)
        if data.position_ids and len(data.position_ids) > 0:
            await UserCRUD(self.auth).set_user_positions(user_ids=[new_user.id], position_ids=data.position_ids)
        return await self.detail(new_user.id)

    async def update(self, id: int, data: UserUpdateSchema) -> UserOutSchema:
        if not data.username:
            raise CustomException(msg="账号不能为空")

        user = await UserCRUD(self.auth).get_or_404(id=id)
        if user.is_superuser:
            raise CustomException(msg="超级管理员不允许修改")

        exist_user = await UserCRUD(self.auth).get(username=data.username)
        if exist_user and exist_user.id != id:
            raise CustomException(msg="更新失败，账号已存在")
        if data.mobile:
            exist_mobile_user = await UserCRUD(self.auth).get(mobile=data.mobile)
            if exist_mobile_user and exist_mobile_user.id != id:
                raise CustomException(msg="该数据已存在")
        if data.email:
            exist_email_user = await UserCRUD(self.auth).get(email=data.email)
            if exist_email_user and exist_email_user.id != id:
                raise CustomException(msg="该数据已存在")
        if data.dept_id:
            dept = await DeptCRUD(self.auth).get(id=data.dept_id)
            if not dept:
                raise CustomException(msg="该数据不存在")
            if dept.status == 1:
                raise CustomException(msg="部门已被禁用")

        new_user = await UserCRUD(self.auth).update(id=id, data=data)

        if data.role_ids and len(data.role_ids) > 0:
            roles = await RoleCRUD(self.auth).get_list(search={"id": ("in", data.role_ids)})
            if len(roles) != len(data.role_ids):
                raise CustomException(msg="更新失败，部分角色不存在")
            if not all(role.status == 0 for role in roles):
                raise CustomException(msg="更新失败，部分角色已被禁用")
            await UserCRUD(self.auth).set_user_roles(user_ids=[id], role_ids=data.role_ids)

        if data.position_ids and len(data.position_ids) > 0:
            positions = await PositionCRUD(self.auth).get_list(search={"id": ("in", data.position_ids)})
            if len(positions) != len(data.position_ids):
                raise CustomException(msg="更新失败，部分岗位不存在")
            if not all(position.status == 0 for position in positions):
                raise CustomException(msg="更新失败，部分岗位已被禁用")
            await UserCRUD(self.auth).set_user_positions(user_ids=[id], position_ids=data.position_ids)

        return UserOutSchema.model_validate(new_user)

    async def delete(self, ids: list[int]) -> None:
        if len(ids) < 1:
            raise CustomException(msg="删除失败，删除对象不能为空")
        users = await UserCRUD(self.auth).get_list(search={"id": ("in", ids)})
        user_map = {u.id: u for u in users}
        for uid in ids:
            user = user_map.get(uid)
            if not user:
                raise CustomException(msg="该数据不存在")
            if user.is_superuser:
                raise CustomException(msg="超级管理员不能删除")
            if user.status == 0:
                raise CustomException(msg="用户已启用,不能删除")
            if self.auth.user and self.auth.user.id == uid:
                raise CustomException(msg="不能删除当前登陆用户")

        await UserCRUD(self.auth).set_user_roles(user_ids=ids, role_ids=[])
        await UserCRUD(self.auth).set_user_positions(user_ids=ids, position_ids=[])
        await UserCRUD(self.auth).delete(ids=ids)

    async def current_info(self) -> UserOutSchema:
        if not self.auth.user or not self.auth.user.id:
            raise CustomException(msg="该数据不存在")
        # 认证依赖已经按全局用户 ID 加载并按当前租户收敛了组织关系。
        # 多租户成员切换后，用户主记录仍可能归属其初始租户；此处若再走
        # UserCRUD，会被当前租户条件过滤为 None，导致当前用户信息序列化失败。
        user = self.auth.user
        user_dict = UserOutSchema.model_validate(user)
        dept = getattr(user, "dept", None)
        if dept:
            user_dict.dept_name = dept.name

        _pc_only = {"client": "pc"}
        if self.auth.is_platform_global:
            menu_all = await MenuCRUD(self.auth).tree_list(
                search={"type": ("in", [1, 2, 3, 4]), "status": 0, **_pc_only},
                order_by=[{"order": "asc"}],
            )
            menus = [MenuOutSchema.model_validate(menu) for menu in menu_all]
        else:
            if self.auth.user.is_superuser and self.auth.tenant_id:
                allowed_ids = await PackageService(self.auth).get_tenant_available_menu_ids(self.auth.tenant_id)
                menu_ids = set(allowed_ids)
            else:
                menu_ids = {menu.id for role in self.auth.user.roles or [] for menu in role.menus if menu.status == 0 and getattr(menu, "client", "pc") == "pc"}

                if menu_ids and self.auth.tenant_id:
                    allowed_ids = await PackageService(self.auth).get_tenant_available_menu_ids(self.auth.tenant_id)
                    allowed_set = set(allowed_ids)
                    menu_ids = menu_ids & allowed_set

            menus = (
                [
                    MenuOutSchema.model_validate(menu)
                    for menu in await MenuCRUD(self.auth).tree_list(
                        search={"id": ("in", sorted(menu_ids)), **_pc_only},
                        order_by=[{"order": "asc"}],
                    )
                ]
                if menu_ids
                else []
            )
        menu_tree = traversal_to_tree([menu.model_dump() for menu in menus])
        user_dict.menus = filter_menu_tree_by_assembly(
            menu_tree,
            audience="platform" if self.auth.is_platform_global else "tenant",
        )
        return user_dict

    async def update_current_info(self, data: CurrentUserUpdateSchema) -> UserOutSchema:
        if not self.auth.user or not self.auth.user.id:
            raise CustomException(msg="该数据不存在")
        user_crud = UserCRUD(self._auth_for_user(self.auth.user))
        user = await user_crud.get(id=self.auth.user.id)
        if not user:
            raise CustomException(msg="该数据不存在")
        if data.mobile:
            exist_mobile_user = await user_crud.get(mobile=data.mobile)
            if exist_mobile_user and exist_mobile_user.id != self.auth.user.id:
                raise CustomException(msg="该数据已存在", status_code=status.HTTP_409_CONFLICT)
        if data.email:
            exist_email_user = await user_crud.get(email=data.email)
            if exist_email_user and exist_email_user.id != self.auth.user.id:
                raise CustomException(msg="该数据已存在", status_code=status.HTTP_409_CONFLICT)
        user_update_data = UserUpdateSchema(**data.model_dump(exclude_unset=True))
        new_user = await user_crud.update(id=self.auth.user.id, data=user_update_data)
        return UserOutSchema.model_validate(new_user)

    async def set_available(self, data: BatchSetAvailable) -> None:
        for mid in data.ids:
            user = await UserCRUD(self.auth).get_or_404(id=mid)
            if user.is_superuser:
                raise CustomException(msg="超级管理员状态不能修改")
        await UserCRUD(self.auth).set(ids=data.ids, status=data.status)

    async def change_password(self, data: UserChangePasswordSchema) -> UserOutSchema:
        if not self.auth.user or not self.auth.user.id:
            raise CustomException(msg="该数据不存在")
        if not data.old_password or not data.new_password:
            raise CustomException(msg="密码不能为空", status_code=status.HTTP_400_BAD_REQUEST)

        user_crud = UserCRUD(self._auth_for_user(self.auth.user))
        user = await user_crud.get(id=self.auth.user.id)
        if not user:
            raise CustomException(msg="该数据不存在")
        if not PwdUtil.verify_password(plain_password=data.old_password, password_hash=user.password):
            raise CustomException(msg="原密码输入错误", status_code=status.HTTP_400_BAD_REQUEST)

        new_password_hash = PwdUtil.hash_password(password=data.new_password)
        new_user = await user_crud.change_password(id=user.id, password_hash=new_password_hash)
        return UserOutSchema.model_validate(new_user)

    async def reset_password(self, data: ResetPasswordSchema) -> UserOutSchema:
        if not data.password:
            raise CustomException(msg="密码不能为空")

        user = await UserCRUD(self.auth).get(id=data.id)
        if not user:
            raise CustomException(msg="该数据不存在")

        if user.is_superuser:
            raise CustomException(msg="超级管理员密码不能重置")

        new_password_hash = PwdUtil.hash_password(password=data.password)
        new_user = await UserCRUD(self.auth).change_password(id=data.id, password_hash=new_password_hash)
        return UserOutSchema.model_validate(new_user)

    async def register(self, data: UserRegisterSchema) -> UserOutSchema:
        username_ok = await UserCRUD(self.auth).get(username=data.username)
        if username_ok:
            raise CustomException(msg="该数据已存在")

        data.password = PwdUtil.hash_password(password=data.password)
        data.name = data.username
        create_dict = data.model_dump(exclude_unset=True, exclude={"role_ids", "position_ids"})

        if self.auth.user and self.auth.user.id:
            create_dict["created_id"] = self.auth.user.id

        result = await UserCRUD(self.auth).create(data=create_dict)
        await self._ensure_tenant_membership(result.id, result.tenant_id)
        if data.role_ids:
            await UserCRUD(self.auth).set_user_roles(user_ids=[result.id], role_ids=data.role_ids)
        return await self.detail(result.id)

    async def forget_password(self, data: UserForgetPasswordSchema) -> UserOutSchema:
        user = await self._get_unique_user_by_username(data.username)
        if not user:
            raise CustomException(msg="该数据不存在")
        if user.status == 1:
            raise CustomException(msg="用户已停用")

        if user.is_superuser:
            raise CustomException(msg="超级管理员密码不能重置")

        if not data.mobile:
            raise CustomException(msg="手机号不能为空", status_code=400)

        if user.mobile != data.mobile:
            raise CustomException(msg="手机号不匹配")

        new_password_hash = PwdUtil.hash_password(password=data.new_password)
        new_user = await UserCRUD(self._auth_for_user(user)).forget_password(
            id=user.id,
            password_hash=new_password_hash,
        )
        return UserOutSchema.model_validate(new_user)

    @staticmethod
    def _password_reset_key(username: str, email: str) -> str:
        return f"password_reset:email_code:{username.lower()}:{email.lower()}"

    @staticmethod
    def _password_reset_interval_key(username: str, email: str) -> str:
        return f"password_reset:send_interval:{username.lower()}:{email.lower()}"

    @staticmethod
    def _password_reset_hourly_key(username: str, email: str) -> str:
        return f"password_reset:send_hourly:{username.lower()}:{email.lower()}"

    @staticmethod
    def _generate_email_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    async def send_forget_password_email_code(
        self,
        redis: Redis,
        data: UserForgetPasswordEmailCodeSchema,
    ) -> dict[str, bool]:
        redis_curd = RedisCURD(redis)
        username = data.username.strip()
        email = str(data.email).strip().lower()
        interval_key = self._password_reset_interval_key(username, email)
        hourly_key = self._password_reset_hourly_key(username, email)

        if await redis_curd.exists(interval_key):
            raise CustomException(msg="验证码发送过于频繁，请 60 秒后再试", status_code=status.HTTP_400_BAD_REQUEST)

        hourly_raw = await redis_curd.get(hourly_key)
        hourly_count = int(hourly_raw.decode("utf-8") if isinstance(hourly_raw, bytes) else hourly_raw or 0)
        if hourly_count >= PASSWORD_RESET_HOURLY_LIMIT:
            raise CustomException(msg="验证码请求次数过多，请稍后再试", status_code=status.HTTP_400_BAD_REQUEST)

        try:
            user = await self._get_unique_user_by_username(username)
        except CustomException:
            # 发送验证码接口保持防枚举语义；歧义账号不得发送，也不向外披露原因。
            user = None
        should_send = (
            user is not None
            and user.status != 1
            and not user.is_superuser
            and bool(user.email)
            and str(user.email).strip().lower() == email
        )

        await redis_curd.set(interval_key, "1", expire=PASSWORD_RESET_SEND_INTERVAL_SECONDS)
        await redis_curd.set(hourly_key, hourly_count + 1, expire=60 * 60)

        if should_send:
            code = self._generate_email_code()
            reset_key = self._password_reset_key(username, email)
            await redis_curd.set(
                reset_key,
                {
                    "user_id": user.id,
                    "username": username,
                    "email": email,
                    "code": code,
                },
                expire=PASSWORD_RESET_CODE_TTL_SECONDS,
            )
            await EmailSendService(self.auth).send_by_template(
                to_email=email,
                to_name=user.name or user.username,
                template_code=PASSWORD_RESET_TEMPLATE_CODE,
                variables={"username": user.username, "code": code},
                biz_type="reset_password",
                tenant_id=user.tenant_id,
            )

        return {"sent": True}

    async def forget_password_by_email_code(
        self,
        redis: Redis,
        data: UserForgetPasswordEmailResetSchema,
    ) -> UserOutSchema:
        username = data.username.strip()
        email = str(data.email).strip().lower()
        reset_key = self._password_reset_key(username, email)
        redis_curd = RedisCURD(redis)
        raw = await redis_curd.get(reset_key)
        if not raw:
            raise CustomException(msg="验证码已过期或不存在", status_code=status.HTTP_400_BAD_REQUEST)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            await redis_curd.delete(reset_key)
            raise CustomException(msg="验证码已过期或不存在", status_code=status.HTTP_400_BAD_REQUEST) from exc

        if str(payload.get("code")) != data.code:
            raise CustomException(msg="验证码错误", status_code=status.HTTP_400_BAD_REQUEST)

        user = await self._get_unique_user_by_username(username)
        if not user or user.id != payload.get("user_id") or str(user.email or "").strip().lower() != email:
            await redis_curd.delete(reset_key)
            raise CustomException(msg="验证码已过期或不存在", status_code=status.HTTP_400_BAD_REQUEST)
        if user.status == 1:
            raise CustomException(msg="用户已停用", status_code=status.HTTP_400_BAD_REQUEST)
        if user.is_superuser:
            raise CustomException(msg="超级管理员密码不能重置", status_code=status.HTTP_400_BAD_REQUEST)

        new_password_hash = PwdUtil.hash_password(password=data.new_password)
        user_crud = UserCRUD(self._auth_for_user(user))
        await user_crud.forget_password(id=user.id, password_hash=new_password_hash)
        new_user = await user_crud.get_or_404(id=user.id)
        await redis_curd.delete(reset_key)
        response = UserOutSchema.model_validate(new_user)
        await self.auth.db.commit()
        return response

    async def batch_import(self, file: UploadFile, update_support: bool = False) -> str:
        header_dict = {
            "部门编号": "dept_id",
            "账号": "username",
            "昵称": "name",
            "邮箱": "email",
            "手机号": "mobile",
            "性别": "gender",
            "状态": "status",
        }

        try:
            contents = await file.read()
            df = pd.read_excel(io.BytesIO(contents))
            await file.close()

            if df.empty:
                raise CustomException(msg="导入文件为空")

            missing_headers = [header for header in header_dict if header not in df.columns]
            if missing_headers:
                raise CustomException(msg=f"导入文件缺少必要的列: {', '.join(missing_headers)}")

            df.rename(columns=header_dict, inplace=True)

            required_fields = ["username", "name", "dept_id"]
            errors = []
            for field in required_fields:
                if df[field].isnull().any():
                    missing_count = df[field].isnull().sum()
                    errors.append(f"字段'{field}'有{missing_count}行缺少数据")

            if errors:
                raise CustomException(msg="\n".join(errors))

            success_count = 0
            error_msgs = []

            for i, (_, row) in enumerate(df.iterrows(), start=2):
                try:
                    username = str(row["username"]).strip() if pd.notna(row["username"]) else ""
                    name = str(row["name"]).strip() if pd.notna(row["name"]) else ""
                    if not username:
                        error_msgs.append(f"第{i}行: 账号不能为空")
                        continue
                    if not name:
                        error_msgs.append(f"第{i}行: 昵称不能为空")
                        continue

                    user_data = {
                        "username": username,
                        "name": name,
                        "email": str(row["email"]).strip() if pd.notna(row["email"]) else None,
                        "mobile": str(row["mobile"]).strip() if pd.notna(row["mobile"]) else None,
                        "gender": str(row["gender"]).strip() if pd.notna(row["gender"]) else "1",
                        "status": 0 if str(row["status"]).strip() == "正常" else 1,
                        "dept_id": int(row["dept_id"]),
                        "password": PwdUtil.hash_password(password="123456"),
                    }

                    exists_user = await UserCRUD(self.auth).get(username=user_data["username"])
                    if exists_user:
                        if exists_user.is_superuser:
                            error_msgs.append(f"第{i}行: 超级管理员不允许修改")
                            continue
                        if update_support:
                            user_update_data = UserUpdateSchema(**user_data)
                            await UserCRUD(self.auth).update(id=exists_user.id, data=user_update_data)
                            success_count += 1
                        else:
                            error_msgs.append(f"第{i}行: 用户 {user_data['username']} 已存在")
                    else:
                        user_create_schema = UserCreateSchema(**user_data)
                        user_create_data = user_create_schema.model_dump(exclude_unset=True, exclude={"role_ids", "position_ids"})
                        new_user = await UserCRUD(self.auth).create(data=user_create_data)
                        if user_create_schema.role_ids and len(user_create_schema.role_ids) > 0:
                            await UserCRUD(self.auth).set_user_roles(user_ids=[new_user.id], role_ids=user_create_schema.role_ids)
                        if user_create_schema.position_ids and len(user_create_schema.position_ids) > 0:
                            await UserCRUD(self.auth).set_user_positions(user_ids=[new_user.id], position_ids=user_create_schema.position_ids)
                        success_count += 1

                except Exception as e:
                    error_msgs.append(f"第{i}行: 异常{e!s}")
                    continue

            result = f"成功导入 {success_count} 条数据"
            if error_msgs:
                result += "\n错误信息:\n" + "\n".join(error_msgs)
            return result

        except Exception as e:
            logger.error(f"批量导入用户失败: {e!s}")
            raise CustomException(msg=f"导入失败: {e!s}") from e

    @staticmethod
    def get_import_template() -> bytes:
        header_list = [
            "部门编号",
            "账号",
            "昵称",
            "邮箱",
            "手机号",
            "性别",
            "状态",
        ]
        selector_header_list = ["性别", "状态"]
        option_list = [
            {"性别": ["男", "女", "未知"]},
            {"状态": ["正常", "停用"]},
        ]
        return ExcelUtil.get_excel_template(
            header_list=header_list,
            selector_header_list=selector_header_list,
            option_list=option_list,
        )

    @staticmethod
    def export_list(user_list: list[dict[str, Any]]) -> bytes:
        if not user_list:
            raise CustomException(msg="没有数据可导出")

        mapping_dict = {
            "id": "用户编号",
            "avatar": "头像",
            "username": "用户名称",
            "name": "用户昵称",
            "dept_name": "部门",
            "email": "邮箱",
            "mobile": "手机号",
            "gender": "性别",
            "status": "状态",
            "is_superuser": "是否超级管理员",
            "last_login": "最后登录时间",
            "description": "备注",
            "created_time": "创建时间",
            "updated_time": "更新时间",
            "updated_id": "更新者ID",
        }

        data = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in user_list
        ]
        for item in data:
            item["status"] = "启用" if item.get("status") == 0 else "停用"
            gender = item.get("gender")
            item["gender"] = "男" if gender == "1" else ("女" if gender == "2" else "未知")
            item["is_superuser"] = "是" if item.get("is_superuser") else "否"
            item["creator"] = item.get("created_by", {}).get("name", "未知") if isinstance(item.get("created_by"), dict) else "未知"

        return ExcelUtil.export_list2excel(list_data=data, mapping_dict=mapping_dict)
