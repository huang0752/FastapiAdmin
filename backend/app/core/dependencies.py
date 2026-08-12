import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from functools import wraps

from fastapi import Depends, Query, Request
from redis.asyncio.client import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD
from app.core.request_context import RequestContext
from app.core.request_context import get_current_tenant_id as _get_ctx_tenant_id
from app.core.security import OAuth2Schema, decode_access_token

# 套餐菜单权限缓存: {tenant_id: (timestamp, [menu_ids])}
_package_menu_cache: dict[int, tuple[float, list[int]]] = {}


async def db_getter() -> AsyncGenerator[AsyncSession, None]:
    """数据库会话 — 请求级生命周期管理。

    一个 HTTP 请求内所有 SQL 共享同一个事务：要么全成功，要么全失败。
    读操作也走这个事务（牺牲一点 MVCC 隔离换取读已写一致性）。
    """
    async with async_db_session() as session:
        async with session.begin():
            yield session


async def redis_getter(request: Request) -> Redis:
    """获取Redis连接

    参数:
    - request (Request): 请求对象

    返回:
    - Redis: Redis连接
    """
    return request.app.state.redis


async def get_current_tenant_id() -> int | None:
    """获取当前请求的租户 ID 依赖注入函数。

    从 ContextVar 中读取租户 ID（由 TenantMiddleware 设置）。
    非认证路径（白名单）返回 None。

    返回:
        int | None: 当前租户 ID，未设置时返回 None。
    """
    return _get_ctx_tenant_id()

async def _decode_token_info(token: str, redis: Redis) -> tuple[dict, str]:
    """解码 JWT token 返回 (user_info, session_id)

    JWT sub 现为纯 session_id，完整会话信息从 Redis 读取。

    参数:
        token: JWT token 字符串
        redis: Redis 连接

    返回:
        (user_info, session_id): 用户信息字典和会话 ID
    """
    payload = decode_access_token(token)
    if not payload or not hasattr(payload, "is_refresh") or payload.is_refresh:
        raise CustomException(msg="非法凭证", code=10401, status_code=401)

    session_id = payload.sub
    if not session_id:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    raw = await RedisCURD(redis).get(
        f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}"
    )
    if not raw:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    user_info = json.loads(raw)
    return user_info, session_id


def _decode_redis_value(value) -> str | None:
    """将 Redis 返回值规整为字符串。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


async def _check_token_online(redis: Redis, session_id: str, token: str) -> None:
    """检查 token 是否在线且为当前会话最新访问令牌

    参数:
        redis: Redis 连接
        session_id: 会话 ID
        token: 当前请求携带的访问令牌
    """
    current_token = _decode_redis_value(
        await RedisCURD(redis).get(f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}")
    )
    if not current_token or current_token != token:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)


async def _try_sliding_refresh(redis: Redis, session_id: str) -> None:
    """滑动过期续期（仅在 token 剩余不足一半时触发）

    参数:
        redis: Redis 连接
        session_id: 会话 ID
    """
    if not settings.TOKEN_SLIDING_EXPIRE:
        return

    ttl = await RedisCURD(redis).ttl(
        key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}"
    )
    # TTL 返回秒，配置也是秒，无需转换
    expire_seconds = settings.ACCESS_TOKEN_EXPIRE_SECONDS
    if ttl > 0 and ttl < expire_seconds // 2:
        await RedisCURD(redis).expire(
            key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}",
            expire=expire_seconds,
        )
        await RedisCURD(redis).expire(
            key=f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}",
            expire=settings.REFRESH_TOKEN_EXPIRE_SECONDS,
        )

def _scope_user_org_context(user, tenant_id: int):
    """将用户的组织关系收敛到当前租户，避免切换租户后沿用旧部门/岗位。"""
    if hasattr(user, "roles"):
        user.roles = [
            role
            for role in user.roles
            if role and role.status == 0 and role.tenant_id == tenant_id
        ]
    if hasattr(user, "positions"):
        user.positions = [
            position
            for position in user.positions
            if position and position.status == 0 and position.tenant_id == tenant_id
        ]
    dept = getattr(user, "dept", None)
    if dept is not None and getattr(dept, "tenant_id", None) != tenant_id:
        user.dept = None
        user.dept_id = None
    return user


async def _load_user_from_db(db: AsyncSession, user_id: int, tenant_id: int):
    """从数据库加载用户（含角色、菜单、部门、职位全量预加载）

    使用原始查询以绕过 CRUDBase 的权限过滤，确保用户认证阶段不受数据权限影响。
    所有关系链均 eager-loaded，调用方可在会话关闭后安全访问对象属性。

    参数:
        db: 数据库会话
        user_id: 用户 ID
        tenant_id: 当前会话租户 ID

    返回:
        UserModel: 已全量加载的用户 ORM 对象
    """
    from app.api.v1.module_system.role.model import RoleModel
    from app.api.v1.module_system.user.model import UserModel

    stmt = (
        select(UserModel)
        .options(
            selectinload(UserModel.dept),
            selectinload(UserModel.roles).selectinload(RoleModel.menus),
            selectinload(UserModel.roles).selectinload(RoleModel.created_by),
            selectinload(UserModel.roles).selectinload(RoleModel.updated_by),
            selectinload(UserModel.roles).selectinload(RoleModel.deleted_by),
            selectinload(UserModel.positions),
            selectinload(UserModel.created_by),
            selectinload(UserModel.updated_by),
            selectinload(UserModel.deleted_by),
        )
        .where(UserModel.id == user_id, UserModel.is_deleted == False)  # noqa: E712
    )
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise CustomException(msg="用户不存在", code=10401, status_code=401)
    if user.status == 1:
        raise CustomException(msg="用户已被停用", code=10401, status_code=401)

    # 在会话内收敛到当前租户，确保会话关闭后不会携带其他租户组织关系。
    return _scope_user_org_context(user, tenant_id)


async def _validate_session_tenant(
    db: AsyncSession,
    user,
    tenant_id: int,
    *,
    session_site_id: int | None,
    request_site_id: int | None,
    jwt_site_id: int | None = None,
) -> None:
    """校验当前会话租户有效，且普通用户仍属于该租户。"""
    from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel

    tenant_stmt = (
        select(TenantModel)
        .where(TenantModel.id == tenant_id, TenantModel.status.in_((0, 1)), TenantModel.is_deleted.is_(False))
        .limit(1)
    )
    tenant_result = await db.execute(tenant_stmt)
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise CustomException(msg="租户不存在或已被禁用", code=10401, status_code=401)

    from app.api.v1.module_system.auth.service import validate_session_site

    validate_session_site(
        jwt_site_id=jwt_site_id,
        session_site_id=session_site_id,
        request_site_id=request_site_id,
        tenant_site_id=tenant.site_id,
    )

    if user.is_superuser:
        return

    relation_stmt = (
        select(TenantUserModel)
        .where(TenantUserModel.user_id == user.id, TenantUserModel.tenant_id == tenant_id)
        .limit(1)
    )
    relation_result = await db.execute(relation_stmt)
    if not relation_result.scalar_one_or_none():
        raise CustomException(msg="租户会话已失效", code=10401, status_code=401)

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(db_getter),
    redis: Redis = Depends(redis_getter),
    token: str = Depends(OAuth2Schema),
) -> AuthSchema:
    """获取当前用户

    用户查询使用独立的只读数据库会话（不参与请求事务，查询完成后立即释放快照），
    返回的 auth.db 指向请求级事务会话供后续写操作使用。

    参数:
    - request (Request): 请求对象
    - db (AsyncSession): 请求级事务会话
    - redis (Redis): Redis连接
    - token (str): 访问令牌

    返回:
    - AuthSchema: 认证信息模型
    """
    return await _authenticate(token, db, redis, request)


async def get_current_user_ws(
    token: str = Query(..., description="认证token"),
    db: AsyncSession = Depends(db_getter),
    redis: Redis = Depends(redis_getter),
) -> AuthSchema:
    """获取当前用户（WebSocket专用，从查询参数获取token）

    参数:
    - token (str): 认证token
    - db (AsyncSession): 数据库会话
    - redis (Redis): Redis连接

    返回:
    - AuthSchema: 认证信息模型
    """
    return await _authenticate(token, db, redis)


async def _authenticate(
    token: str,
    db: AsyncSession,
    redis: Redis,
    request: Request | None = None,
) -> AuthSchema:
    """核心认证逻辑（HTTP 与 WebSocket 共享）

    参数:
    - token: 访问令牌
    - db: 请求级事务会话
    - redis: Redis连接
    - request: HTTP 请求对象（WebSocket 场景为 None）

    返回:
    - AuthSchema: 认证信息模型
    """
    if not token:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    # 处理Bearer token
    if token.startswith("Bearer"):
        token = token.split(" ")[1]

    jwt_payload = decode_access_token(token)
    if not getattr(jwt_payload, "site_id", None):
        raise CustomException(msg="JWT 站点上下文不匹配", code=10403, status_code=403)

    # 优先使用 TenantMiddleware 缓存在 request.state.ctx 中的会话信息（避免重复 Redis 读取）
    user_info = None
    if request:
        ctx = getattr(request.state, "ctx", None)
        user_info = ctx.jwt_user_info if ctx else None

    if not user_info:
        # 降级路径：自行从 Redis 读取会话信息
        user_info, _ = await _decode_token_info(token, redis)

    session_id = user_info.get("session_id")
    if not session_id:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    # Redis 在线检查 + 滑动续期
    await _check_token_online(redis, session_id, token)
    await _try_sliding_refresh(redis, session_id)

    user_id = user_info.get("user_id")
    if not user_id:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)
    tenant_id = user_info.get("tenant_id")
    if not tenant_id:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)
    site_id = user_info.get("site_id")
    if not site_id:
        raise CustomException(msg="站点会话已失效", code=10401, status_code=401)

    request_site_id = int(site_id)
    if request:
        from app.api.v1.module_system.auth.service import resolve_request_site

        request_site = await resolve_request_site(db, request)
        request_site_id = request_site.id

    # 用户查询使用独立只读会话（不参与请求事务，查询后立即释放快照）
    async with async_db_session() as lookup_db:
        user = await _load_user_from_db(lookup_db, int(user_id), int(tenant_id))
        await _validate_session_tenant(
            lookup_db,
            user,
            int(tenant_id),
            session_site_id=int(site_id),
            request_site_id=int(request_site_id),
            jwt_site_id=getattr(jwt_payload, "site_id", None),
        )

    # 设置请求上下文（仅在当前 request 对象上，业务方通过 request.state.ctx 读取）
    if request:
        request.state.ctx = replace(
            (getattr(request.state, "ctx", None) or RequestContext()),
            user_id=user.id,
            user_username=user.username,
            session_id=session_id,
            session_info=user_info,
        )

    # 返回的 auth.db 指向请求级事务会话，供后续读写操作使用
    auth = AuthSchema(
        db=db,
        tenant_id=int(tenant_id),
        site_id=int(site_id),
        check_data_scope=False,
    )
    auth.user = user
    return auth

async def _get_cached_tenant_menu_ids(auth: AuthSchema, tenant_id: int) -> list[int]:
    """获取当前租户可用菜单 ID。

    权限撤销必须跨 worker 立即生效，因此授权判断不读取进程内缓存。保留的
    ``_package_menu_cache`` 只用于兼容旧调用方的显式失效，不再作为安全决策源。

    参数:
        auth: 认证信息
        tenant_id: 租户 ID

    返回:
        可用菜单 ID 列表
    """
    from app.api.v1.module_platform.package.service import PackageService

    return await PackageService.get_tenant_available_menu_ids(auth, tenant_id)


async def is_tenant_package_active(auth: AuthSchema, tenant_id: int) -> bool:
    """判断普通租户是否仍绑定有效套餐；系统租户不受套餐约束。"""
    if tenant_id == 1:
        return True

    from app.api.v1.module_platform.package.model import PackageModel
    from app.api.v1.module_platform.tenant.model import TenantModel

    return bool(
        (
            await auth.db.execute(
                select(TenantModel.id)
                .join(PackageModel, PackageModel.id == TenantModel.package_id)
                .where(
                    TenantModel.id == tenant_id,
                    PackageModel.status == 0,
                    PackageModel.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
    )


async def resolve_effective_permissions(
    auth: AuthSchema,
    *,
    bypass_role_grants: bool = False,
    require_active_package: bool = False,
) -> set[str]:
    """解析 actor 在角色、菜单与套餐约束下仍然有效的权限。

    ``bypass_role_grants`` 仅供后台租户代管场景的真实超级管理员使用：它只
    豁免角色授予，菜单启用状态和租户套餐 entitlement 仍然必须成立。
    """
    if not auth.user:
        return set()

    if auth.tenant_id is not None and require_active_package and not await is_tenant_package_active(auth, auth.tenant_id):
        return set()

    if bypass_role_grants:
        from app.api.v1.module_platform.menu.model import MenuModel

        if auth.tenant_id is None:
            result = await auth.db.execute(select(MenuModel.permission).where(MenuModel.status == 0, MenuModel.permission.is_not(None)))
        else:
            allowed_ids = set(await _get_cached_tenant_menu_ids(auth, auth.tenant_id))
            if not allowed_ids:
                return set()
            result = await auth.db.execute(
                select(MenuModel.permission).where(
                    MenuModel.id.in_(allowed_ids),
                    MenuModel.status == 0,
                    MenuModel.permission.is_not(None),
                )
            )
        return {permission for permission in result.scalars() if permission}

    if not auth.user.roles:
        return set()

    role_menus = [
        menu
        for role in auth.user.roles
        if role.status == 0 and (auth.tenant_id is None or role.tenant_id == auth.tenant_id)
        for menu in role.menus
        if menu.status == 0 and menu.permission
    ]
    if not role_menus:
        return set()
    if auth.tenant_id is None:
        return {menu.permission for menu in role_menus}

    allowed_ids = set(await _get_cached_tenant_menu_ids(auth, auth.tenant_id))
    return {menu.permission for menu in role_menus if menu.id in allowed_ids}


class AuthPermission:
    """权限验证类"""

    def __init__(
        self,
        permissions: list[str] | None = None,
        check_data_scope: bool = True,
    ) -> None:
        """
        初始化权限验证

        参数:
        - permissions (list[str] | None): 权限标识列表。
        - check_data_scope (bool): 是否启用严格模式校验。
        """
        self.permissions = permissions or []
        self.check_data_scope = check_data_scope

    async def __call__(self, auth: AuthSchema = Depends(get_current_user)) -> AuthSchema:
        """
        调用权限验证

        参数:
        - auth (AuthSchema): 认证信息对象。

        返回:
        - AuthSchema: 认证信息对象。
        """
        # 用 model_copy 派生一份带正确 check_data_scope 的新实例（不修改原实例）
        auth = auth.model_copy(update={"check_data_scope": self.check_data_scope})

        # 超级管理员直接通过
        if auth.user and auth.user.is_superuser:
            return auth

        # 无需验证权限
        if not self.permissions:
            return auth

        # 超级管理员权限标识只能由真实超级管理员命中；普通用户不能因接口声明通配符而放行。
        if "*" in self.permissions or "*:*:*" in self.permissions:
            raise CustomException(msg="无权限操作", code=10403, status_code=403)

        user_permissions = await resolve_effective_permissions(auth)

        # 权限验证 - 满足任一权限即可
        if not any(perm in user_permissions for perm in self.permissions):
            logger.error(f"用户缺少任何所需的权限: {self.permissions}")
            raise CustomException(msg="无权限操作", code=10403, status_code=403)

        return auth


def require_superadmin(func):
    """
    装饰器：仅超级管理员可调用 Service 方法。

    自动校验 ``self.auth.user.is_superuser`` 属性，非超管直接抛出 403。
    适用于实例方法（``Service(auth).xxx(...)``），由 ``self.auth`` 取认证上下文。

    用法:
        class XxxService:
            def __init__(self, auth: AuthSchema) -> None:
                self.auth = auth

            @require_superadmin
            async def create(self, data: ...) -> ...:
                ...
    """
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.auth.user or not self.auth.user.is_superuser:
            raise CustomException(msg="仅平台管理员可操作", code=10403, status_code=403)
        return await func(self, *args, **kwargs)

    return wrapper


def require_platform_admin(func):
    """装饰器：仅平台全局管理员可调用 Service 方法。

    与只检查 ``is_superuser`` 的 ``require_superadmin`` 不同，本装饰器还要求
    当前认证上下文满足 ``AuthSchema.is_platform_global``。因此，普通租户中的
    用户即使被误标为超级管理员，也不能读取或操作跨租户平台数据。
    """

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.auth.is_platform_global:
            raise CustomException(msg="仅平台管理员可操作", code=10403, status_code=403)
        return await func(self, *args, **kwargs)

    return wrapper
