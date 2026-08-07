from sqlalchemy import delete, func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.core.exceptions import CustomException

from .model import UserLoginIdentifierModel, UserModel

LOGIN_IDENTIFIER_TYPES = ("username", "email", "mobile")


def normalize_login_identifier(value: str) -> str:
    """登录标识统一去首尾空格并按大小写不敏感处理。"""
    return value.strip().casefold()


def user_login_identifiers(user: UserModel) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for identifier_type in LOGIN_IDENTIFIER_TYPES:
        raw_value = getattr(user, identifier_type, None)
        if raw_value:
            values.append((identifier_type, normalize_login_identifier(str(raw_value))))
    return values


def _site_user_ids(site_id: int):
    home_user_ids = (
        select(UserModel.id)
        .join(TenantModel, TenantModel.id == UserModel.tenant_id)
        .where(TenantModel.site_id == site_id)
    )
    member_user_ids = (
        select(TenantUserModel.user_id)
        .join(TenantModel, TenantModel.id == TenantUserModel.tenant_id)
        .where(TenantModel.site_id == site_id)
    )
    return union(home_user_ids, member_user_ids)


async def get_user_site_ids(db: AsyncSession, user: UserModel) -> set[int]:
    home_site_ids = select(TenantModel.site_id).where(TenantModel.id == user.tenant_id)
    member_site_ids = (
        select(TenantModel.site_id)
        .join(TenantUserModel, TenantUserModel.tenant_id == TenantModel.id)
        .where(TenantUserModel.user_id == user.id)
    )
    rows = await db.execute(union(home_site_ids, member_site_ids))
    return {int(site_id) for site_id in rows.scalars().all() if site_id is not None}


async def _has_legacy_site_conflict(
    db: AsyncSession,
    *,
    user: UserModel,
    site_ids: set[int],
    normalized_values: list[str],
) -> bool:
    if not site_ids or not normalized_values:
        return False
    matching_users = list(
        (
            await db.execute(
                select(UserModel)
                .where(
                    UserModel.id != user.id,
                    UserModel.is_deleted.is_(False),
                    or_(
                        func.lower(func.trim(UserModel.username)).in_(normalized_values),
                        func.lower(func.trim(UserModel.email)).in_(normalized_values),
                        func.trim(UserModel.mobile).in_(normalized_values),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for other_user in matching_users:
        if site_ids & await get_user_site_ids(db, other_user):
            return True
    return False


async def sync_user_login_identifiers(db: AsyncSession, user: UserModel) -> None:
    """在当前事务内同步用户在各 Site 下的登录标识。"""
    identifiers = user_login_identifiers(user)
    normalized_values = [value for _, value in identifiers]
    if len(normalized_values) != len(set(normalized_values)):
        raise CustomException(msg="用户名、邮箱或手机号存在重复", status_code=409)

    site_ids = await get_user_site_ids(db, user)
    if normalized_values:
        registry_conflict = (
            await db.execute(
                select(UserLoginIdentifierModel.user_id)
                .where(
                    UserLoginIdentifierModel.site_id.in_(site_ids),
                    UserLoginIdentifierModel.normalized_value.in_(normalized_values),
                    UserLoginIdentifierModel.user_id != user.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if registry_conflict is not None or await _has_legacy_site_conflict(
            db,
            user=user,
            site_ids=site_ids,
            normalized_values=normalized_values,
        ):
            raise CustomException(msg="登录标识已被占用", status_code=409)

    await db.execute(delete(UserLoginIdentifierModel).where(UserLoginIdentifierModel.user_id == user.id))
    db.add_all(
        [
            UserLoginIdentifierModel(
                site_id=site_id,
                user_id=user.id,
                identifier_type=identifier_type,
                normalized_value=value,
            )
            for site_id in site_ids
            for identifier_type, value in identifiers
        ]
    )
    await db.flush()


async def resolve_user_by_login_identifier(
    db: AsyncSession,
    identifier: str,
    site_id: int | None = None,
) -> UserModel | None:
    """在 Host 对应 Site 内解析用户名、邮箱或手机号。"""
    normalized = normalize_login_identifier(identifier)
    stmt = (
        select(UserModel)
        .join(UserLoginIdentifierModel, UserLoginIdentifierModel.user_id == UserModel.id)
        .where(
            UserLoginIdentifierModel.normalized_value == normalized,
            UserModel.is_deleted.is_(False),
        )
        .distinct()
    )
    if site_id is not None:
        stmt = stmt.where(UserLoginIdentifierModel.site_id == site_id)
    users = list((await db.execute(stmt.limit(2))).scalars().all())
    if len(users) > 1:
        raise CustomException(msg="账号标识不唯一，请联系管理员", status_code=400)
    if users:
        return users[0]

    legacy_stmt = select(UserModel).where(
        UserModel.is_deleted.is_(False),
        or_(
            func.lower(func.trim(UserModel.username)) == normalized,
            func.lower(func.trim(UserModel.email)) == normalized,
            func.trim(UserModel.mobile) == normalized,
        ),
    )
    if site_id is not None:
        legacy_stmt = legacy_stmt.where(UserModel.id.in_(_site_user_ids(site_id)))
    legacy_users = list((await db.execute(legacy_stmt.limit(2))).scalars().all())
    if len(legacy_users) > 1:
        raise CustomException(msg="账号标识不唯一，请联系管理员", status_code=400)
    return legacy_users[0] if legacy_users else None
