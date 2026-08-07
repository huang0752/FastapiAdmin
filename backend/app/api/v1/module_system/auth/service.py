
import json
import uuid
from datetime import datetime, timedelta
from typing import NewType

import ua_parser
from fastapi import BackgroundTasks, Request
from redis.asyncio.client import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_monitor.online.schema import OnlineOutSchema
from app.api.v1.module_platform.tenant.model import TenantUserModel
from app.api.v1.module_system.user.crud import UserCRUD
from app.api.v1.module_system.user.model import UserModel
from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.base_schema import (
    AuthSchema,
    JWTOutSchema,
    JWTPayloadSchema,
    LogoutPayloadSchema,
    RefreshTokenPayloadSchema,
)
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD
from app.core.security import (
    CustomOAuth2PasswordRequestForm,
    create_access_token,
    decode_access_token,
)
from app.utils.captcha_util import CaptchaUtil
from app.utils.common_util import get_random_character
from app.utils.hash_bcrpy_util import PwdUtil
from app.utils.ip_local_util import IpLocalUtil, get_client_ip

from .schema import (
    AutoLoginTokenSchema,
    AutoLoginUserSchema,
    CaptchaOutSchema,
    LoginWithTenantsSchema,
    SelectTenantOutSchema,
    TenantOptionSchema,
    TenantRegisterOutSchema,
)

CaptchaKey = NewType("CaptchaKey", str)
CaptchaBase64 = NewType("CaptchaBase64", str)


async def resolve_request_site(db: AsyncSession, request: Request):
    """按请求 Host 解析启用中的品牌站点，未知 Host 一律拒绝。"""
    from app.api.v1.module_platform.site.service import SiteService

    site = await SiteService.resolve_by_host(db, request.headers.get("host", ""))
    if not site:
        raise CustomException(msg="当前访问域名未配置品牌站点", code=10403, status_code=403)
    return site


def validate_session_site(
    *,
    session_site_id: int | None,
    request_site_id: int | None,
    tenant_site_id: int | None,
) -> None:
    """确保会话、Host 与租户始终处于同一 Site 边界。"""
    if not session_site_id or not request_site_id or not tenant_site_id:
        raise CustomException(msg="站点上下文缺失", code=10401, status_code=401)
    if len({int(session_site_id), int(request_site_id), int(tenant_site_id)}) != 1:
        raise CustomException(msg="站点上下文不匹配，禁止跨站点访问", code=10403, status_code=403)


def _redis_value_to_str(value) -> str | None:
    """将 Redis 返回值规整为字符串。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


async def _write_login_log(
    username: str,
    status: int,
    login_ip: str | None = None,
    login_location: str | None = None,
    request_os: str | None = None,
    request_browser: str | None = None,
    msg: str | None = None,
    tenant_id: int = 1,
) -> int | None:
    """写入登录日志；返回日志 ID（用于后台补全归属地）。"""
    from app.api.v1.module_system.log.crud import LoginLogCRUD
    from app.api.v1.module_system.log.schema import LoginLogCreateSchema
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session

    try:
        async with async_db_session() as session:
            async with session.begin():
                _auth = AuthSchema(db=session, tenant_id=tenant_id, check_data_scope=False)
                payload = LoginLogCreateSchema(
                    username=username,
                    status=status,
                    login_ip=login_ip,
                    login_location=login_location,
                    request_os=request_os,
                    request_browser=request_browser,
                    msg=msg,
                ).model_dump()
                payload["tenant_id"] = tenant_id
                obj = await LoginLogCRUD(_auth).create(data=payload)
                return obj.id if obj else None
    except Exception:
        return None


async def get_unique_user_by_username(
    db: AsyncSession,
    username: str,
    site_id: int | None = None,
) -> UserModel | None:
    """在当前 Site 内按用户名、邮箱或手机号解析唯一账号。"""
    from app.api.v1.module_system.user.login_identifier import resolve_user_by_login_identifier

    return await resolve_user_by_login_identifier(db, username, site_id)


async def _async_fill_login_location(
    redis, login_log_id: int, ip: str | None
) -> None:
    """后台异步补全登录日志的归属地。"""
    if not ip:
        return
    try:
        location = await IpLocalUtil.resolve_location_async(redis, ip)
        if location == "归属地查询中" or not location:
            return
        from sqlalchemy import update as sa_update

        from app.api.v1.module_system.log.model import LoginLogModel
        from app.core.database import async_db_session

        async with async_db_session() as session:
            async with session.begin():
                await session.execute(
                    sa_update(LoginLogModel)
                    .where(LoginLogModel.id == login_log_id)
                    .values(login_location=location)
                )
    except Exception as e:
        from app.core.logger import logger
        logger.warning(f"异步补全登录归属地失败: {e}")


def _resolve_request_ip(request: Request) -> str | None:
    """从请求中解析客户端真实 IP。"""
    return get_client_ip(request)


class LoginService:
    """登录认证服务"""

    def __init__(self, auth: AuthSchema | None = None) -> None:
        self.auth = auth

    @classmethod
    async def authenticate_user(
        cls,
        request: Request,
        background_tasks: BackgroundTasks,
        redis: Redis,
        login_form: CustomOAuth2PasswordRequestForm,
        db: AsyncSession,
    ) -> LoginWithTenantsSchema:
        """用户认证"""
        ua_result = ua_parser.parse(request.headers.get("user-agent"))
        request_ip = _resolve_request_ip(request)
        login_location = await IpLocalUtil.resolve_location_for_log(redis, request_ip)
        _login_os = ua_result.os.family if ua_result.os else "Unknown"
        _login_browser = ua_result.user_agent.family if ua_result.user_agent else "Unknown"
        _login_username = login_form.username
        site = await resolve_request_site(db, request)

        if settings.CAPTCHA_ENABLE:
            if not login_form.captcha_key or not login_form.captcha:
                raise CustomException(msg="验证码不能为空")
            await CaptchaService.check_captcha(
                redis=redis,
                key=login_form.captcha_key,
                captcha=login_form.captcha,
            )

        user = await get_unique_user_by_username(db, login_form.username, site.id)

        if not user:
            await _write_login_log(
                username=_login_username,
                status=2,
                login_ip=request_ip,
                login_location=login_location,
                request_os=_login_os,
                request_browser=_login_browser,
                msg="用户不存在",
            )
            raise CustomException(msg="账号或密码错误", status_code=400)

        if not PwdUtil.verify_password(plain_password=login_form.password, password_hash=user.password):
            await _write_login_log(
                username=_login_username,
                status=2,
                login_ip=request_ip,
                login_location=login_location,
                request_os=_login_os,
                request_browser=_login_browser,
                msg="账号或密码错误",
                tenant_id=user.tenant_id,
            )
            raise CustomException(msg="账号或密码错误", status_code=400)
        if user.status == 1:
            await _write_login_log(
                username=_login_username,
                status=2,
                login_ip=request_ip,
                login_location=login_location,
                request_os=_login_os,
                request_browser=_login_browser,
                msg="用户已被停用",
                tenant_id=user.tenant_id,
            )
            raise CustomException(msg="用户已被停用")

        tenants_auth = AuthSchema(db=db, user=user, tenant_id=user.tenant_id, check_data_scope=False)
        tenants = await LoginService(tenants_auth).get_user_tenants(user_id=user.id, site_id=site.id)
        if not tenants:
            await _write_login_log(
                username=_login_username,
                status=2,
                login_ip=request_ip,
                login_location=login_location,
                request_os=_login_os,
                request_browser=_login_browser,
                msg="所属租户已被禁用",
                tenant_id=user.tenant_id,
            )
            raise CustomException(msg="当前站点下没有可用租户", code=10401, status_code=401)
        selected_tenant_id = tenants[0].id

        user_auth = AuthSchema(
            db=db,
            user=user,
            tenant_id=user.tenant_id,
            check_data_scope=False,
        )
        await UserCRUD(user_auth).update_last_login(id=user.id)

        if not user:
            raise CustomException(msg="用户不存在")
        if not login_form.login_type:
            raise CustomException(msg="登录类型不能为空")

        token = await cls.create_token(
            request=request,
            redis=redis,
            user=user,
            login_type=login_form.login_type,
            tenant_id=selected_tenant_id,
            site_id=site.id,
        )

        user_info = {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "avatar": user.avatar,
            "is_superuser": user.is_superuser,
        }

        log_id = await _write_login_log(
            username=user.username,
            status=1,
            login_ip=request_ip,
            login_location=login_location,
            request_os=_login_os,
            request_browser=_login_browser,
            msg="登录成功",
            tenant_id=selected_tenant_id,
        )
        # 登录成功后异步补全归属地，不阻塞返回
        if log_id and login_location == "归属地查询中":
            background_tasks.add_task(_async_fill_login_location, redis, log_id, request_ip)

        return LoginWithTenantsSchema(
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_in=token.expires_in,
            token_type=token.token_type,
            tenants=tenants,
            user_info=user_info,
        )

    @classmethod
    async def create_token(
        cls,
        request: Request,
        redis: Redis,
        user: UserModel,
        login_type: str,
        *,
        tenant_id: int,
        site_id: int,
    ) -> JWTOutSchema:
        """创建访问令牌和刷新令牌"""
        session_id = str(uuid.uuid4())
        ua_result = ua_parser.parse(request.headers.get("user-agent"))
        request_ip = _resolve_request_ip(request)

        login_location = await IpLocalUtil.resolve_location_for_log(redis, request_ip)

        from dataclasses import replace

        from app.core.request_context import RequestContext

        base_ctx = getattr(request.state, "ctx", None) or RequestContext()
        request.state.ctx = replace(
            base_ctx,
            session_id=session_id,
            user_username=user.username,
            login_location=login_location,
        )

        access_expires = timedelta(seconds=settings.ACCESS_TOKEN_EXPIRE_SECONDS)
        refresh_expires = timedelta(seconds=settings.REFRESH_TOKEN_EXPIRE_SECONDS)

        now = datetime.now()

        session_info = OnlineOutSchema(
            session_id=session_id,
            user_id=user.id,
            tenant_id=tenant_id,
            site_id=site_id,
            is_superuser=user.is_superuser,
            name=user.name,
            user_name=user.username,
            ipaddr=request_ip,
            login_location=login_location,
            os=ua_result.os.family if ua_result.os else "Unknown",
            browser=ua_result.user_agent.family if ua_result.user_agent else "Unknown",
            login_time=user.last_login,
            login_type=login_type,
        ).model_dump_json()

        # 会话信息存 Redis（完整 JSON），JWT sub 仅含 session_id
        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}",
            value=session_info,
            expire=int(refresh_expires.total_seconds()),
        )

        access_token = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=False,
                exp=now + access_expires,
            )
        )
        refresh_token = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=True,
                exp=now + refresh_expires,
            )
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}",
            value=access_token,
            expire=int(access_expires.total_seconds()),
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}",
            value=refresh_token,
            expire=int(refresh_expires.total_seconds()),
        )

        return JWTOutSchema(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=int(access_expires.total_seconds()),
            token_type=settings.TOKEN_TYPE,
        )

    @classmethod
    async def refresh_token(
        cls,
        request: Request,
        db: AsyncSession,
        redis: Redis,
        refresh_token: RefreshTokenPayloadSchema,
    ) -> JWTOutSchema:
        """刷新访问令牌"""
        token_payload: JWTPayloadSchema = decode_access_token(token=refresh_token.refresh_token)
        if not token_payload.is_refresh:
            raise CustomException(msg="非法凭证，请传入刷新令牌")

        session_id = token_payload.sub
        current_refresh_token = _redis_value_to_str(
            await RedisCURD(redis).get(f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}")
        )
        if not current_refresh_token or current_refresh_token != refresh_token.refresh_token:
            raise CustomException(msg="刷新凭证已失效，请重新登录", code=10401, status_code=401)

        session_info = await RedisCURD(redis).get(
            f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}"
        )
        if not session_info:
            raise CustomException(msg="会话已过期，请重新登录")

        session_data = json.loads(session_info)
        user_id = session_data.get("user_id")
        tenant_id = session_data.get("tenant_id")
        site_id = session_data.get("site_id")

        if not session_id or not user_id or not tenant_id or not site_id:
            raise CustomException(msg="非法凭证,无法获取会话编号、用户ID、租户ID或站点ID", code=10401, status_code=401)

        user_stmt = select(UserModel).where(
            UserModel.id == user_id,
            UserModel.is_deleted.is_(False),
        ).limit(1)
        user = (await db.execute(user_stmt)).scalar_one_or_none()
        if not user:
            raise CustomException(msg="刷新token失败，用户不存在", code=10401, status_code=401)
        if user.status == 1:
            raise CustomException(msg="用户已被停用", code=10401, status_code=401)

        from app.api.v1.module_platform.tenant.model import TenantModel

        tenant_stmt = (
            select(TenantModel)
            .where(TenantModel.id == tenant_id, TenantModel.status.in_((0, 1)), TenantModel.is_deleted.is_(False))
            .limit(1)
        )
        tenant_result = await db.execute(tenant_stmt)
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            raise CustomException(msg="租户不存在或已被禁用", code=10401, status_code=401)

        request_site = await resolve_request_site(db, request)
        validate_session_site(
            session_site_id=site_id,
            request_site_id=request_site.id,
            tenant_site_id=tenant.site_id,
        )

        if not user.is_superuser:
            relation_stmt = (
                select(TenantUserModel)
                .where(TenantUserModel.user_id == user.id, TenantUserModel.tenant_id == tenant_id)
                .limit(1)
            )
            relation_result = await db.execute(relation_stmt)
            if not relation_result.scalar_one_or_none():
                raise CustomException(msg="租户会话已失效", code=10401, status_code=401)

        access_expires = timedelta(seconds=settings.ACCESS_TOKEN_EXPIRE_SECONDS)
        refresh_expires = timedelta(seconds=settings.REFRESH_TOKEN_EXPIRE_SECONDS)
        now = datetime.now()

        # 延长会话信息 Redis TTL
        await RedisCURD(redis).expire(
            key=f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}",
            expire=int(refresh_expires.total_seconds()),
        )

        access_token = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=False,
                exp=now + access_expires,
            )
        )

        refresh_token_new = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=True,
                exp=now + refresh_expires,
            )
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}",
            value=access_token,
            expire=int(access_expires.total_seconds()),
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}",
            value=refresh_token_new,
            expire=int(refresh_expires.total_seconds()),
        )

        return JWTOutSchema(
            access_token=access_token,
            refresh_token=refresh_token_new,
            token_type=settings.TOKEN_TYPE,
            expires_in=int(access_expires.total_seconds()),
        )

    @staticmethod
    async def logout(redis: Redis, token: LogoutPayloadSchema, current_token: str) -> bool:
        """退出登录"""
        payload: JWTPayloadSchema = decode_access_token(token=token.token)
        current_payload: JWTPayloadSchema = decode_access_token(token=current_token)

        if payload.is_refresh or current_payload.is_refresh or payload.sub != current_payload.sub:
            raise CustomException(msg="非法凭证,无法注销非当前会话", code=10401, status_code=401)

        session_id = payload.sub

        if not session_id:
            raise CustomException(msg="非法凭证,无法获取会话编号")

        await RedisCURD(redis).delete(f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}")
        await RedisCURD(redis).delete(f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}")
        await RedisCURD(redis).delete(f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}")

        logger.info(f"用户退出登录成功,会话编号:{session_id}")

        return True

    async def get_user_tenants(
        self,
        user_id: int | None = None,
        site_id: int | None = None,
    ) -> list[TenantOptionSchema]:
        """获取用户关联的租户列表"""
        from sqlalchemy import select

        from app.api.v1.module_platform.tenant.model import TenantModel

        uid = user_id or (self.auth.user.id if self.auth.user else None)
        if not uid:
            return []
        site_id = site_id or self.auth.site_id
        if not site_id:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=403)

        if self.auth.user and self.auth.user.is_superuser:
            stmt = select(TenantModel).where(
                TenantModel.site_id == site_id,
                TenantModel.status.in_((0, 1)),
                TenantModel.is_deleted.is_(False),
            ).order_by(TenantModel.sort, TenantModel.id)
            result = await self.auth.db.execute(stmt)
            tenant_objs = result.scalars().all()
            return [TenantOptionSchema(id=t.id, name=t.name, code=t.code) for t in tenant_objs]

        stmt = (
            select(TenantModel)
            .join(TenantUserModel, TenantUserModel.tenant_id == TenantModel.id)
            .where(
                TenantUserModel.user_id == uid,
                TenantModel.site_id == site_id,
                TenantModel.status.in_((0, 1)),
                TenantModel.is_deleted.is_(False),
            )
            .order_by(TenantUserModel.is_default.desc(), TenantModel.sort, TenantModel.id)
        )
        result = await self.auth.db.execute(stmt)
        tenant_objs = result.scalars().all()
        return [TenantOptionSchema(id=t.id, name=t.name, code=t.code) for t in tenant_objs]

    async def select_tenant(
        self,
        request: Request,
        redis: Redis,
        tenant_id: int,
    ) -> SelectTenantOutSchema:
        """选择租户：验证用户归属并签发含租户上下文的新 JWT Token"""
        from sqlalchemy import select

        from app.api.v1.module_platform.tenant.model import TenantModel

        if not self.auth.user:
            raise CustomException(msg="未认证用户")

        if not self.auth.user.is_superuser:
            exist_stmt = (
                select(TenantUserModel)
                .where(
                    TenantUserModel.user_id == self.auth.user.id,
                    TenantUserModel.tenant_id == tenant_id,
                )
                .limit(1)
            )
            result = await self.auth.db.execute(exist_stmt)
            if not result.scalar_one_or_none():
                raise CustomException(msg="您不属于该租户，无法切换")

        tenant_stmt = select(TenantModel).where(TenantModel.id == tenant_id, TenantModel.status.in_((0, 1)), TenantModel.is_deleted.is_(False)).limit(1)
        result = await self.auth.db.execute(tenant_stmt)
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise CustomException(msg="租户不存在或已被禁用")

        ctx = getattr(request.state, "ctx", None)
        session_id = ctx.session_id if ctx else None
        session_info = ctx.session_info if ctx else None

        if not session_id or not session_info:
            raise CustomException(msg="会话已失效")

        request_site = await resolve_request_site(self.auth.db, request)
        validate_session_site(
            session_site_id=session_info.get("site_id"),
            request_site_id=request_site.id,
            tenant_site_id=tenant.site_id,
        )

        # 更新会话中的租户 ID 并写回 Redis
        session_info["tenant_id"] = tenant_id
        refresh_expires = timedelta(seconds=settings.REFRESH_TOKEN_EXPIRE_SECONDS)
        from app.core.redis_crud import RedisCURD
        from app.core.security import create_access_token

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}",
            value=json.dumps(session_info) if isinstance(session_info, dict) else session_info,
            expire=int(refresh_expires.total_seconds()),
        )

        access_expires = timedelta(seconds=settings.ACCESS_TOKEN_EXPIRE_SECONDS)
        now = datetime.now()

        new_access_token = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=False,
                exp=now + access_expires,
            )
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}",
            value=new_access_token,
            expire=int(access_expires.total_seconds()),
        )

        from app.core.request_context import set_current_tenant

        set_current_tenant(tenant_id)

        logger.info(f"用户 {self.auth.user.username}(id={self.auth.user.id}) 切换到租户 {tenant.name}(id={tenant_id})")

        return SelectTenantOutSchema(
            access_token=new_access_token,
            token_type=settings.TOKEN_TYPE,
            expires_in=int(access_expires.total_seconds()),
        )


class CaptchaService:
    """验证码服务"""

    @staticmethod
    async def get_captcha(redis: Redis) -> CaptchaOutSchema:
        """获取验证码"""
        if not settings.CAPTCHA_ENABLE:
            raise CustomException(msg="未开启验证码服务")

        captcha_base64, captcha_value = CaptchaUtil.captcha_arithmetic()
        captcha_key = get_random_character()

        redis_key = f"{RedisInitKeyConfig.CAPTCHA_CODES.key}:{captcha_key}"
        await RedisCURD(redis).set(
            key=redis_key,
            value=captcha_value,
            expire=settings.CAPTCHA_EXPIRE_SECONDS,
        )

        return CaptchaOutSchema(
            enable=settings.CAPTCHA_ENABLE,
            key=CaptchaKey(captcha_key),
            img_base=CaptchaBase64(f"data:image/png;base64,{captcha_base64}"),
        )

    @staticmethod
    async def check_captcha(redis: Redis, key: str, captcha: str) -> bool:
        """校验验证码"""
        if not captcha:
            raise CustomException(msg="验证码不能为空")

        redis_key = f"{RedisInitKeyConfig.CAPTCHA_CODES.key}:{key}"
        captcha_value = await RedisCURD(redis).get(redis_key)
        if not captcha_value:
            raise CustomException(msg="验证码已过期")

        if captcha.lower() != captcha_value.lower():
            raise CustomException(msg="验证码错误")

        await RedisCURD(redis).delete(redis_key)
        return True


class AutoLoginService:
    """免登录服务"""

    AUTO_LOGIN_PREFIX = "fastapiadmin:auto_login:"
    TOKEN_EXPIRE = 300

    @classmethod
    async def get_auto_login_users(
        cls,
        db: AsyncSession,
        tenant_id: int | None = None,
        site_id: int | None = None,
    ) -> list[AutoLoginUserSchema]:
        """获取免登录用户列表"""
        from sqlalchemy import select

        from app.api.v1.module_platform.tenant.model import TenantModel
        from app.api.v1.module_system.user.model import UserModel

        if not site_id:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=403)
        stmt = (
            select(UserModel)
            .join(TenantUserModel, TenantUserModel.user_id == UserModel.id)
            .join(TenantModel, TenantModel.id == TenantUserModel.tenant_id)
            .where(UserModel.status == 0, TenantModel.site_id == site_id)
            .distinct()
        )
        if tenant_id is not None:
            stmt = stmt.where(TenantUserModel.tenant_id == tenant_id)
        stmt = stmt.order_by(UserModel.id)
        result = await db.execute(stmt)
        users = result.scalars().all()

        return [
            AutoLoginUserSchema(
                id=user.id,
                username=user.username,
                name=user.name,
                avatar=user.avatar,
            )
            for user in users
        ]

    @classmethod
    async def create_auto_login_token(
        cls,
        redis: Redis,
        db: AsyncSession,
        user_id: int,
        tenant_id: int | None = None,
        site_id: int | None = None,
    ) -> AutoLoginTokenSchema:
        """创建免登录Token"""
        from sqlalchemy import select

        from app.api.v1.module_platform.tenant.model import TenantModel
        from app.api.v1.module_system.user.model import UserModel

        if not tenant_id or not site_id:
            raise CustomException(msg="租户或站点上下文缺失", code=10403, status_code=403)
        stmt = (
            select(UserModel)
            .join(TenantUserModel, TenantUserModel.user_id == UserModel.id)
            .join(TenantModel, TenantModel.id == TenantUserModel.tenant_id)
            .where(
                UserModel.id == user_id,
                TenantUserModel.tenant_id == tenant_id,
                TenantModel.site_id == site_id,
            )
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise CustomException(msg="用户不存在")

        if user.status == 1:
            raise CustomException(msg="用户已被停用")

        import uuid

        token = str(uuid.uuid4())
        token_key = f"{cls.AUTO_LOGIN_PREFIX}{token}"

        token_data = {
            "user_id": user.id,
            "username": user.username,
            "tenant_id": tenant_id,
            "site_id": site_id,
            "created_at": datetime.now().isoformat(),
        }
        await RedisCURD(redis).set(
            key=token_key,
            value=json.dumps(token_data),
            expire=cls.TOKEN_EXPIRE,
        )

        logger.info(f"创建免登录Token成功,用户:{user.username}")

        return AutoLoginTokenSchema(
            token=token,
            user=AutoLoginUserSchema(
                id=user.id,
                username=user.username,
                name=user.name,
                avatar=user.avatar,
            ),
        )

    @classmethod
    async def auto_login(
        cls,
        request: Request,
        redis: Redis,
        db: AsyncSession,
        token: str,
        tenant_id: int | None = None,
    ) -> JWTOutSchema:
        """免登录"""
        from sqlalchemy import select

        from app.api.v1.module_system.user.model import UserModel

        token_key = f"{cls.AUTO_LOGIN_PREFIX}{token}"
        token_data_str = await RedisCURD(redis).get(token_key)

        if not token_data_str:
            raise CustomException(msg="免登录Token已过期或无效")

        if isinstance(token_data_str, bytes):
            token_data_str = token_data_str.decode("utf-8")

        token_data = json.loads(token_data_str)
        user_id = token_data.get("user_id")
        token_tenant_id = token_data.get("tenant_id")
        token_site_id = token_data.get("site_id")

        request_site = await resolve_request_site(db, request)
        from app.api.v1.module_platform.tenant.model import TenantModel

        effective_tenant_id = tenant_id if tenant_id is not None else token_tenant_id
        tenant = (
            await db.execute(
                select(TenantModel).where(
                    TenantModel.id == effective_tenant_id,
                    TenantModel.status.in_((0, 1)),
                    TenantModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if not tenant:
            raise CustomException(msg="租户不存在或已被禁用")
        validate_session_site(
            session_site_id=token_site_id,
            request_site_id=request_site.id,
            tenant_site_id=tenant.site_id,
        )
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise CustomException(msg="用户不存在")

        if user.status == 1:
            raise CustomException(msg="用户已被停用")

        await RedisCURD(redis).delete(token_key)

        jwt_token = await LoginService.create_token(
            request=request,
            redis=redis,
            user=user,
            login_type="PC端",
            tenant_id=effective_tenant_id,
            site_id=request_site.id,
        )

        logger.info(f"用户{user.username}免登录成功")

        return jwt_token


class TenantRegisterService:
    """PRD §4.5 租户自助注册：一次性创建租户 + 管理员 + owner 角色 + 菜单分配"""

    DEFAULT_TRIAL_DAYS = 7

    @classmethod
    async def register(
        cls,
        db: AsyncSession,
        site_id: int,
        username: str,
        password: str,
        email: str,
        tenant_name: str | None = None,
    ) -> TenantRegisterOutSchema:
        """租户自助注册：一次性创建租户 + 管理员 + owner 角色 + 菜单分配"""
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from app.api.v1.module_platform.package.model import PackageModel
        from app.api.v1.module_platform.tenant.model import TenantModel
        from app.api.v1.module_platform.tenant.service import TenantService
        from app.api.v1.module_system.user.login_identifier import resolve_user_by_login_identifier
        from app.api.v1.module_system.user.model import UserModel

        if await resolve_user_by_login_identifier(db, username, site_id) or await resolve_user_by_login_identifier(
            db, email, site_id
        ):
            raise CustomException(msg="用户名或邮箱已被占用")

        pkg_stmt = (
            select(PackageModel)
            .where(PackageModel.site_id == site_id, PackageModel.status == 0)
            .order_by(PackageModel.id)
            .limit(1)
        )
        default_pkg = (await db.execute(pkg_stmt)).scalar_one_or_none()

        now = datetime.now()
        trial_end = now + timedelta(days=cls.DEFAULT_TRIAL_DAYS)

        tenant_code = f"T{uuid.uuid4().hex[:8].upper()}"

        tenant = TenantModel(
            name=tenant_name or f"{username}的租户",
            code=tenant_code,
            contact_name=username,
            site_id=site_id,
            package_id=default_pkg.id if default_pkg else None,
            start_time=now,
            end_time=trial_end,
            status=0,
        )
        db.add(tenant)
        await db.flush()

        user = UserModel(
            username=username,
            password=PwdUtil.hash_password(password),
            name=username,
            email=email,
            tenant_id=tenant.id,
            status=0,
        )
        db.add(user)
        await db.flush()

        await TenantService.ensure_tenant_owner(db, tenant.id, user.id)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise CustomException(msg="租户编码或用户名已被占用，请重试")

        try:
            await cls._send_welcome_email(email, username, tenant.name, trial_end)
        except Exception:
            logger.warning(f"注册欢迎邮件发送失败: {email}")

        return TenantRegisterOutSchema(
            user_id=user.id,
            username=username,
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            tenant_code=tenant_code,
            package=default_pkg.name if default_pkg else None,
            trial_end=trial_end.strftime("%Y-%m-%d"),
            message="注册成功",
        )

    @classmethod
    async def _send_welcome_email(cls, to_email: str, username: str, tenant_name: str, trial_end: datetime) -> None:
        """发送欢迎邮件（不阻塞注册流程）。"""
        from app.api.v1.module_platform.email.crud import EmailConfigCRUD
        from app.core.base_schema import AuthSchema
        from app.core.database import async_db_session
        from app.utils.email_util import render_template_file, send_email

        async with async_db_session() as _db:
            cfg = await EmailConfigCRUD(AuthSchema(db=_db, check_data_scope=False)).get_active_default()

        if not cfg:
            logger.info("无可用 SMTP 配置，跳过欢迎邮件")
            return

        html_body = render_template_file("emails/welcome.jinja2", {
            "tenant_name": tenant_name,
            "username": username,
            "trial_end": trial_end.strftime("%Y-%m-%d"),
        })

        await send_email(
            smtp_host=cfg.smtp_host,
            smtp_port=cfg.smtp_port,
            smtp_user=cfg.smtp_user,
            smtp_password=cfg.smtp_password,
            use_tls=cfg.use_tls,
            from_name=cfg.from_name,
            to_email=to_email,
            to_name=username,
            subject=f"欢迎加入 {tenant_name}！",
            body_html=html_body,
        )
        logger.info(f"欢迎邮件已发送至 {to_email}")
