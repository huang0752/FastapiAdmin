from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from redis.asyncio.client import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_system.federated_access.model import (
    FederatedAccessEntitlementModel,
    FederatedAccessEventModel,
)
from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@dataclass(slots=True)
class RedisLockOwnership:
    redis: Redis
    key: str
    value: str
    lost: asyncio.Event = field(default_factory=asyncio.Event)
    failure: BaseException | None = None

    CHECK_SCRIPT = """
    -- SESSION_LOCK_CHECK_V1
    return redis.call('get', KEYS[1]) == ARGV[1] and 1 or 0
    """

    async def ensure_owned(self) -> None:
        if self.lost.is_set():
            raise CustomException(
                msg="会话锁所有权已丢失，请重试",
                code=10409,
                status_code=409,
            ) from self.failure
        try:
            owned = await self.redis.eval(
                self.CHECK_SCRIPT,
                1,
                self.key,
                self.value,
            )
            if int(owned) != 1:
                self.lost.set()
                raise CustomException(
                    msg="会话锁所有权已丢失，请重试",
                    code=10409,
                    status_code=409,
                )
        except CustomException:
            raise
        except Exception as exc:
            self.failure = exc
            self.lost.set()
            raise CustomException(
                msg="会话锁所有权校验失败，请重试",
                code=10409,
                status_code=409,
            ) from exc


class UserSessionRegistry:
    """Maintain the reverse index from a product user to its Redis sessions."""

    INDEX_PREFIX = "user_session_index"
    CREATE_SCRIPT = """
    -- SESSION_CREATE_V1
    local expected_fence = 'user_session_fence:' .. ARGV[8] .. ':' .. ARGV[9]
        .. ':' .. ARGV[10]
    local expected_index = 'user_session_index:' .. ARGV[8] .. ':' .. ARGV[9]
        .. ':' .. ARGV[10]
    local ok, session = pcall(cjson.decode, ARGV[3])
    if KEYS[1] ~= expected_fence or KEYS[5] ~= expected_index or not ok
       or tonumber(session.site_id) ~= tonumber(ARGV[8])
       or tonumber(session.tenant_id) ~= tonumber(ARGV[9])
       or tonumber(session.user_id) ~= tonumber(ARGV[10]) then
        return -2
    end
    if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
    redis.call('set', KEYS[2], ARGV[3], 'EX', tonumber(ARGV[6]))
    redis.call('set', KEYS[3], ARGV[4], 'EX', tonumber(ARGV[7]))
    redis.call('set', KEYS[4], ARGV[5], 'EX', tonumber(ARGV[6]))
    redis.call('sadd', KEYS[5], ARGV[2])
    local ttl = redis.call('ttl', KEYS[5])
    if ttl < tonumber(ARGV[6]) then redis.call('expire', KEYS[5], tonumber(ARGV[6])) end
    return 1
    """
    ADD_SCRIPT = """
    -- SESSION_INDEX_ADD_V1
    redis.call('sadd', KEYS[1], ARGV[1])
    local ttl = redis.call('ttl', KEYS[1])
    local requested = tonumber(ARGV[2])
    if ttl < requested then redis.call('expire', KEYS[1], requested) end
    return 1
    """
    MOVE_SCRIPT = """
    -- SESSION_INDEX_MOVE_V1
    local raw = redis.call('get', KEYS[1])
    if not raw then return 0 end
    local ok, session = pcall(cjson.decode, raw)
    if not ok then return -1 end
    if tonumber(session.site_id) ~= tonumber(ARGV[2])
       or tonumber(session.tenant_id) ~= tonumber(ARGV[3])
       or tonumber(session.user_id) ~= tonumber(ARGV[4]) then
        return 0
    end
    redis.call('sadd', KEYS[3], ARGV[1])
    local ttl = redis.call('ttl', KEYS[3])
    local requested = tonumber(ARGV[5])
    if ttl < requested then redis.call('expire', KEYS[3], requested) end
    if KEYS[2] ~= KEYS[3] then redis.call('srem', KEYS[2], ARGV[1]) end
    return 1
    """
    DELETE_SCRIPT = """
    -- SESSION_DELETE_V1
    if ARGV[4] ~= '' and redis.call('get', KEYS[4]) ~= ARGV[4] then
        return -2
    end
    local raw = redis.call('get', KEYS[1])
    if ARGV[2] ~= '' and redis.call('get', KEYS[2]) ~= ARGV[2] then
        return -1
    end
    local index_key = nil
    if raw then
        local ok, session = pcall(cjson.decode, raw)
        if ok and session.site_id and session.tenant_id and session.user_id then
            index_key = ARGV[3] .. ':' .. tostring(session.site_id)
                .. ':' .. tostring(session.tenant_id) .. ':' .. tostring(session.user_id)
        end
    end
    redis.call('del', KEYS[1], KEYS[2], KEYS[3])
    if index_key then redis.call('srem', index_key, ARGV[1]) end
    return raw and 1 or 0
    """
    REVOKE_ONE_SCRIPT = """
    -- SESSION_REVOKE_ONE_V1
    if redis.call('get', KEYS[5]) ~= ARGV[5] then return -2 end
    local raw = redis.call('get', KEYS[2])
    if not raw then
        redis.call('srem', KEYS[1], ARGV[1])
        return 0
    end
    local ok, session = pcall(cjson.decode, raw)
    if not ok
       or tonumber(session.site_id) ~= tonumber(ARGV[2])
       or tonumber(session.tenant_id) ~= tonumber(ARGV[3])
       or tonumber(session.user_id) ~= tonumber(ARGV[4]) then
        redis.call('srem', KEYS[1], ARGV[1])
        return 0
    end
    redis.call('del', KEYS[2], KEYS[3], KEYS[4])
    redis.call('srem', KEYS[1], ARGV[1])
    return 1
    """
    CLEAN_ORPHAN_INDEX_SCRIPT = """
    -- SESSION_CLEAN_ORPHAN_INDEX_V1
    if redis.call('exists', KEYS[2], KEYS[3], KEYS[4]) ~= 0 then
        return 0
    end
    return redis.call('srem', KEYS[1], ARGV[1])
    """
    SWITCH_SCRIPT = """
    -- SESSION_SWITCH_V1
    local expected_lock = 'user_session_lock:' .. ARGV[7]
    if KEYS[5] ~= expected_lock
       or redis.call('get', KEYS[5]) ~= ARGV[8]
       or redis.call('get', KEYS[6]) ~= ARGV[9]
       or redis.call('get', KEYS[7]) ~= ARGV[10] then
        return -2
    end
    if redis.call('get', KEYS[1]) ~= ARGV[1]
       or redis.call('get', KEYS[2]) ~= ARGV[2] then
        return 0
    end
    redis.call('set', KEYS[1], ARGV[3], 'EX', tonumber(ARGV[5]))
    redis.call('set', KEYS[2], ARGV[4], 'EX', tonumber(ARGV[6]))
    redis.call('sadd', KEYS[4], ARGV[7])
    local ttl = redis.call('ttl', KEYS[4])
    if ttl < tonumber(ARGV[5]) then redis.call('expire', KEYS[4], tonumber(ARGV[5])) end
    if KEYS[3] ~= KEYS[4] then redis.call('srem', KEYS[3], ARGV[7]) end
    return 1
    """
    REFRESH_SCRIPT = """
    -- SESSION_REFRESH_V1
    local expected_lock = 'user_session_lock:' .. ARGV[7]
    if KEYS[5] ~= expected_lock
       or redis.call('get', KEYS[5]) ~= ARGV[8]
       or redis.call('get', KEYS[6]) ~= ARGV[9] then
        return -2
    end
    if redis.call('get', KEYS[1]) ~= ARGV[1]
       or redis.call('get', KEYS[3]) ~= ARGV[2] then
        return 0
    end
    redis.call('expire', KEYS[1], tonumber(ARGV[6]))
    redis.call('set', KEYS[2], ARGV[3], 'EX', tonumber(ARGV[5]))
    redis.call('set', KEYS[3], ARGV[4], 'EX', tonumber(ARGV[6]))
    redis.call('sadd', KEYS[4], ARGV[7])
    local ttl = redis.call('ttl', KEYS[4])
    if ttl < tonumber(ARGV[6]) then redis.call('expire', KEYS[4], tonumber(ARGV[6])) end
    return 1
    """
    RELEASE_LOCK_SCRIPT = """
    -- SESSION_LOCK_RELEASE_V1
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """
    RENEW_LOCK_SCRIPT = """
    -- SESSION_LOCK_RENEW_V1
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
    end
    return 0
    """

    @classmethod
    def index_key(cls, site_id: int, tenant_id: int, user_id: int) -> str:
        return f"{cls.INDEX_PREFIX}:{site_id}:{tenant_id}:{user_id}"

    @staticmethod
    def session_key(session_id: str) -> str:
        return f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}"

    @staticmethod
    def access_key(session_id: str) -> str:
        return f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}"

    @staticmethod
    def refresh_key(session_id: str) -> str:
        return f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}"

    @staticmethod
    def lock_key(session_id: str) -> str:
        return f"user_session_lock:{session_id}"

    @staticmethod
    def user_fence_key(site_id: int, tenant_id: int, user_id: int) -> str:
        return f"user_session_fence:{site_id}:{tenant_id}:{user_id}"

    @staticmethod
    async def ensure_ownerships(
        ownerships: list[RedisLockOwnership] | tuple[RedisLockOwnership, ...],
    ) -> None:
        for ownership in ownerships:
            await ownership.ensure_owned()

    @staticmethod
    def ownership_for_key(
        ownerships: list[RedisLockOwnership] | tuple[RedisLockOwnership, ...],
        key: str,
    ) -> RedisLockOwnership:
        ownership = next(
            (candidate for candidate in ownerships if candidate.key == key),
            None,
        )
        if ownership is None:
            raise CustomException(
                msg="会话锁作用域不匹配，请重试",
                code=10409,
                status_code=409,
            )
        return ownership

    @classmethod
    async def create_fenced(
        cls,
        redis: Redis,
        *,
        ownership: RedisLockOwnership,
        session_id: str,
        session_info: str,
        access_token: str,
        refresh_token: str,
        site_id: int,
        tenant_id: int,
        user_id: int,
        access_expire: int,
        refresh_expire: int,
    ) -> None:
        expected_fence_key = cls.user_fence_key(site_id, tenant_id, user_id)
        if ownership.key != expected_fence_key:
            raise CustomException(
                msg="会话锁作用域不匹配，请重试",
                code=10409,
                status_code=409,
            )
        created = await redis.eval(
            cls.CREATE_SCRIPT,
            5,
            ownership.key,
            cls.session_key(session_id),
            cls.access_key(session_id),
            cls.refresh_key(session_id),
            cls.index_key(site_id, tenant_id, user_id),
            ownership.value,
            session_id,
            session_info,
            access_token,
            refresh_token,
            str(refresh_expire),
            str(access_expire),
            str(site_id),
            str(tenant_id),
            str(user_id),
        )
        if int(created) == -2:
            raise CustomException(
                msg="会话锁作用域不匹配，请重试",
                code=10409,
                status_code=409,
            )
        if int(created) != 1:
            ownership.lost.set()
            raise CustomException(
                msg="会话锁所有权已丢失，请重试",
                code=10409,
                status_code=409,
            )

    @classmethod
    @asynccontextmanager
    async def _owned_lock(
        cls,
        redis: Redis,
        key: str,
        *,
        expire: int,
        attempts: int,
        delay: float,
    ):
        value = uuid.uuid4().hex
        for _ in range(attempts):
            if await redis.set(key, value, ex=expire, nx=True):
                break
            await asyncio.sleep(delay)
        else:
            raise CustomException(msg="会话正在更新，请重试", code=10409, status_code=409)
        ownership = RedisLockOwnership(redis=redis, key=key, value=value)
        stop_heartbeat = asyncio.Event()

        async def heartbeat() -> None:
            delay_seconds = max(expire / 3, 0.05)
            while True:
                try:
                    await asyncio.wait_for(
                        stop_heartbeat.wait(),
                        timeout=delay_seconds,
                    )
                    return
                except TimeoutError:
                    try:
                        renewed = await redis.eval(
                            cls.RENEW_LOCK_SCRIPT,
                            1,
                            key,
                            value,
                            str(expire),
                        )
                        if int(renewed) != 1:
                            ownership.lost.set()
                            return
                    except Exception as exc:
                        ownership.failure = exc
                        ownership.lost.set()
                        return

        heartbeat_task = asyncio.create_task(
            heartbeat(),
            name=f"session-lock-heartbeat:{key}",
        )
        try:
            yield ownership
            await ownership.ensure_owned()
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await redis.eval(cls.RELEASE_LOCK_SCRIPT, 1, key, value)

    @classmethod
    @asynccontextmanager
    async def mutation_lock(
        cls,
        redis: Redis,
        session_id: str,
        *,
        expire: int = 30,
        attempts: int = 100,
        delay: float = 0.05,
    ):
        async with cls._owned_lock(
            redis,
            cls.lock_key(session_id),
            expire=expire,
            attempts=attempts,
            delay=delay,
        ) as ownership:
            yield ownership

    @classmethod
    @asynccontextmanager
    async def user_fences(
        cls,
        redis: Redis,
        scopes: list[tuple[int, int, int]],
        *,
        expire: int = 30,
        attempts: int = 100,
        delay: float = 0.05,
    ):
        keys = sorted({cls.user_fence_key(site_id, tenant_id, user_id) for site_id, tenant_id, user_id in scopes})
        ownerships: list[RedisLockOwnership] = []
        async with AsyncExitStack() as stack:
            for key in keys:
                ownerships.append(
                    await stack.enter_async_context(
                        cls._owned_lock(
                            redis,
                            key,
                            expire=expire,
                            attempts=attempts,
                            delay=delay,
                        )
                    )
                )
            yield ownerships

    @classmethod
    async def add(
        cls,
        redis: Redis,
        *,
        session_id: str,
        site_id: int,
        tenant_id: int,
        user_id: int,
        expire: int,
    ) -> None:
        key = cls.index_key(site_id, tenant_id, user_id)
        await redis.eval(cls.ADD_SCRIPT, 1, key, session_id, str(expire))

    @classmethod
    async def touch(
        cls,
        redis: Redis,
        *,
        session_id: str,
        site_id: int,
        tenant_id: int,
        user_id: int,
        expire: int,
    ) -> None:
        await cls.add(
            redis,
            session_id=session_id,
            site_id=site_id,
            tenant_id=tenant_id,
            user_id=user_id,
            expire=expire,
        )

    @classmethod
    async def move(
        cls,
        redis: Redis,
        *,
        session_id: str,
        site_id: int,
        old_tenant_id: int,
        new_tenant_id: int,
        user_id: int,
        expire: int,
    ) -> None:
        new_key = cls.index_key(site_id, new_tenant_id, user_id)
        old_key = cls.index_key(site_id, old_tenant_id, user_id)
        moved = await redis.eval(
            cls.MOVE_SCRIPT,
            3,
            cls.session_key(session_id),
            old_key,
            new_key,
            session_id,
            str(site_id),
            str(old_tenant_id),
            str(user_id),
            str(expire),
        )
        if int(moved) != 1:
            raise CustomException(msg="会话已变更，无法更新租户索引", code=10409, status_code=409)

    @classmethod
    async def delete_session(
        cls,
        redis: Redis,
        session_id: str,
        *,
        expected_access_token: str | None = None,
        mutation_ownership: RedisLockOwnership | None = None,
    ) -> bool:
        result = await redis.eval(
            cls.DELETE_SCRIPT,
            4,
            cls.session_key(session_id),
            cls.access_key(session_id),
            cls.refresh_key(session_id),
            cls.lock_key(session_id),
            session_id,
            expected_access_token or "",
            cls.INDEX_PREFIX,
            mutation_ownership.value if mutation_ownership is not None else "",
        )
        if int(result) == -2:
            if mutation_ownership is not None:
                mutation_ownership.lost.set()
            raise CustomException(
                msg="会话锁所有权已丢失，请重试",
                code=10409,
                status_code=409,
            )
        if int(result) == -1:
            raise CustomException(msg="会话凭证已变更", code=10401, status_code=401)
        return int(result) == 1

    @classmethod
    async def index_user_historical_sessions(
        cls, redis: Redis, site_id: int, tenant_id: int, user_id: int,
        *, fence_ownerships: list[RedisLockOwnership], scan_count: int = 100, max_batches: int = 20,
    ) -> bool:
        """Bounded legacy scan while holding this user's fence; False keeps recovery pending."""
        cls.ownership_for_key(fence_ownerships, cls.user_fence_key(site_id, tenant_id, user_id))
        cursor = 0
        for _ in range(max_batches):
            await cls.ensure_ownerships(fence_ownerships)
            cursor, keys = await redis.scan(cursor=cursor, match=f"{RedisInitKeyConfig.USER_SESSION.key}:*", count=scan_count)
            for raw_key in keys:
                raw = _as_text(await redis.get(raw_key))
                if not raw:
                    continue
                try:
                    session = json.loads(raw)
                    if (int(session["site_id"]), int(session["tenant_id"]), int(session["user_id"])) != (site_id, tenant_id, user_id):
                        continue
                    session_id = session["session_id"]
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                ttl = await redis.ttl(raw_key)
                await cls.add(redis, session_id=session_id, site_id=site_id, tenant_id=tenant_id, user_id=user_id, expire=ttl if ttl > 0 else settings.REFRESH_TOKEN_EXPIRE_SECONDS)
            if int(cursor) == 0:
                await cls.ensure_ownerships(fence_ownerships)
                return True
        return False

    @classmethod
    async def revoke_user(
        cls,
        redis: Redis,
        site_id: int,
        tenant_id: int,
        user_id: int,
        *,
        _fence_ownerships: list[RedisLockOwnership] | None = None,
    ) -> int:
        if _fence_ownerships is None:
            async with cls.user_fences(
                redis, [(site_id, tenant_id, user_id)]
            ) as ownerships:
                return await cls.revoke_user(
                    redis,
                    site_id,
                    tenant_id,
                    user_id,
                    _fence_ownerships=ownerships,
                )
        await cls.ensure_ownerships(_fence_ownerships)
        index_key = cls.index_key(site_id, tenant_id, user_id)
        fence_key = cls.user_fence_key(site_id, tenant_id, user_id)
        fence_ownership = cls.ownership_for_key(_fence_ownerships, fence_key)
        members = await redis.smembers(index_key)
        session_ids = sorted(session_id for value in members if (session_id := _as_text(value)))
        revoked = 0
        for session_id in session_ids:
            await cls.ensure_ownerships(_fence_ownerships)
            result = int(
                await redis.eval(
                    cls.REVOKE_ONE_SCRIPT,
                    5,
                    index_key,
                    cls.session_key(session_id),
                    cls.access_key(session_id),
                    cls.refresh_key(session_id),
                    fence_key,
                    session_id,
                    str(site_id),
                    str(tenant_id),
                    str(user_id),
                    fence_ownership.value,
                )
            )
            if result == -2:
                fence_ownership.lost.set()
                raise CustomException(
                    msg="会话锁所有权已丢失，请重试",
                    code=10409,
                    status_code=409,
                )
            revoked += result
        return revoked

    @classmethod
    async def switch_tenant(
        cls,
        redis: Redis,
        *,
        session_id: str,
        site_id: int,
        old_tenant_id: int,
        new_tenant_id: int,
        user_id: int,
        expected_session: str,
        expected_access_token: str,
        new_session: str,
        new_access_token: str,
        session_expire: int,
        access_expire: int,
        mutation_ownership: RedisLockOwnership,
        fence_ownerships: list[RedisLockOwnership],
    ) -> None:
        if mutation_ownership.key != cls.lock_key(session_id):
            raise CustomException(
                msg="会话锁作用域不匹配，请重试",
                code=10409,
                status_code=409,
            )
        old_fence_key = cls.user_fence_key(site_id, old_tenant_id, user_id)
        new_fence_key = cls.user_fence_key(site_id, new_tenant_id, user_id)
        old_fence = cls.ownership_for_key(fence_ownerships, old_fence_key)
        new_fence = cls.ownership_for_key(fence_ownerships, new_fence_key)
        result = await redis.eval(
            cls.SWITCH_SCRIPT,
            7,
            cls.session_key(session_id),
            cls.access_key(session_id),
            cls.index_key(site_id, old_tenant_id, user_id),
            cls.index_key(site_id, new_tenant_id, user_id),
            mutation_ownership.key,
            old_fence_key,
            new_fence_key,
            expected_session,
            expected_access_token,
            new_session,
            new_access_token,
            str(session_expire),
            str(access_expire),
            session_id,
            mutation_ownership.value,
            old_fence.value,
            new_fence.value,
        )
        if int(result) == -2:
            mutation_ownership.lost.set()
            old_fence.lost.set()
            new_fence.lost.set()
            raise CustomException(
                msg="会话锁所有权已丢失，请重试",
                code=10409,
                status_code=409,
            )
        if int(result) != 1:
            raise CustomException(msg="会话已变更，请重新操作", code=10409, status_code=409)

    @classmethod
    async def rotate_tokens(
        cls,
        redis: Redis,
        *,
        session_id: str,
        site_id: int,
        tenant_id: int,
        user_id: int,
        expected_session: str,
        expected_refresh_token: str,
        new_access_token: str,
        new_refresh_token: str,
        access_expire: int,
        refresh_expire: int,
        mutation_ownership: RedisLockOwnership,
        fence_ownerships: list[RedisLockOwnership],
    ) -> None:
        if mutation_ownership.key != cls.lock_key(session_id):
            raise CustomException(
                msg="会话锁作用域不匹配，请重试",
                code=10409,
                status_code=409,
            )
        fence_key = cls.user_fence_key(site_id, tenant_id, user_id)
        fence_ownership = cls.ownership_for_key(fence_ownerships, fence_key)
        result = await redis.eval(
            cls.REFRESH_SCRIPT,
            6,
            cls.session_key(session_id),
            cls.access_key(session_id),
            cls.refresh_key(session_id),
            cls.index_key(site_id, tenant_id, user_id),
            mutation_ownership.key,
            fence_key,
            expected_session,
            expected_refresh_token,
            new_access_token,
            new_refresh_token,
            str(access_expire),
            str(refresh_expire),
            session_id,
            mutation_ownership.value,
            fence_ownership.value,
        )
        if int(result) == -2:
            mutation_ownership.lost.set()
            fence_ownership.lost.set()
            raise CustomException(
                msg="会话锁所有权已丢失，请重试",
                code=10409,
                status_code=409,
            )
        if int(result) != 1:
            raise CustomException(msg="刷新凭证已变更，请重新登录", code=10401, status_code=401)

    @classmethod
    async def clear_all(cls, redis: Redis, *, scan_count: int = 500) -> None:
        """Clear online session artifacts without blocking Redis with KEYS."""

        async def scan_keys(pattern: str):
            cursor = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor=cursor,
                    match=pattern,
                    count=scan_count,
                )
                for raw_key in keys:
                    key = _as_text(raw_key)
                    if key:
                        yield key
                if int(cursor) == 0:
                    break

        session_ids: set[str] = set()
        prefixes = (
            f"{RedisInitKeyConfig.USER_SESSION.key}:",
            f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:",
            f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:",
        )
        for prefix in prefixes:
            async for key in scan_keys(f"{prefix}*"):
                session_ids.add(key.removeprefix(prefix))
        for session_id in session_ids:
            await cls.delete_session(redis, session_id)

        async for index_key in scan_keys(f"{cls.INDEX_PREFIX}:*"):
            parts = index_key.split(":")
            if len(parts) != 4:
                # Malformed legacy index cannot reference a valid scope.
                await redis.delete(index_key)
                continue
            try:
                site_id, tenant_id, user_id = map(int, parts[1:])
            except ValueError:
                await redis.delete(index_key)
                continue
            members = await redis.smembers(index_key)
            for raw_session_id in members:
                session_id = _as_text(raw_session_id)
                if not session_id:
                    continue
                await redis.eval(
                    cls.CLEAN_ORPHAN_INDEX_SCRIPT,
                    4,
                    index_key,
                    cls.session_key(session_id),
                    cls.access_key(session_id),
                    cls.refresh_key(session_id),
                    session_id,
                )


async def require_active_federated_entitlement(
    db: AsyncSession,
    *,
    user,
    site_id: int,
    tenant_id: int,
) -> None:
    """Fail closed for federated users when entitlement enforcement is enabled."""
    if not settings.CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED:
        return
    if user.is_superuser or user.auth_source != "federated":
        return

    entitlement_id = (
        await db.execute(
            select(FederatedAccessEntitlementModel.id).where(
                FederatedAccessEntitlementModel.site_id == site_id,
                FederatedAccessEntitlementModel.tenant_id == tenant_id,
                FederatedAccessEntitlementModel.local_user_id == user.id,
                FederatedAccessEntitlementModel.issuer == settings.CONTROL_SSO_ISSUER.rstrip("/"),
                FederatedAccessEntitlementModel.status == "active",
            )
        )
    ).scalar_one_or_none()
    if entitlement_id is None:
        raise CustomException(
            msg="中控已撤销该产品访问权限",
            code=10401,
            status_code=401,
        )


class FederatedSessionRecovery:
    """Bounded startup recovery for legacy indexes and pending revocations."""

    LOCK_KEY = "federated_session_recovery_lock"
    SESSION_SCAN_CURSOR_KEY = "federated_session_recovery:user_session_cursor"
    SESSION_SCAN_EPOCH_KEY = "federated_session_recovery:user_session_epoch"
    SESSION_SCAN_WATERMARK_KEY = "federated_session_recovery:user_session_watermark"
    PENDING_CURSOR_KEY = "federated_session_recovery:pending_cursor"
    CURSOR_EXPIRE_SECONDS = 86400
    RENEW_LOCK_SCRIPT = """
    -- RECOVERY_LOCK_RENEW_V1
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
    end
    return 0
    """
    STORE_PROGRESS_SCRIPT = """
    -- RECOVERY_STORE_PROGRESS_V1
    if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
    redis.call('set', KEYS[2], ARGV[2], 'EX', tonumber(ARGV[4]))
    redis.call('set', KEYS[3], ARGV[3], 'EX', tonumber(ARGV[4]))
    return 1
    """
    COMPLETE_SCAN_SCRIPT = """
    -- RECOVERY_COMPLETE_SCAN_V1
    if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
    redis.call('set', KEYS[4], ARGV[2], 'EX', tonumber(ARGV[3]))
    redis.call('del', KEYS[2], KEYS[3])
    return 1
    """
    STORE_PENDING_CURSOR_SCRIPT = """
    -- RECOVERY_STORE_PENDING_CURSOR_V1
    if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
    redis.call('set', KEYS[2], ARGV[2], 'EX', tonumber(ARGV[3]))
    return 1
    """
    DELETE_PENDING_CURSOR_SCRIPT = """
    -- RECOVERY_DELETE_PENDING_CURSOR_V1
    if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
    redis.call('del', KEYS[2])
    return 1
    """
    RELEASE_LOCK_SCRIPT = """
    -- SESSION_LOCK_RELEASE_V1
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    @classmethod
    async def _store_scan_progress(
        cls,
        redis: Redis,
        *,
        cursor: int,
        scan_epoch: str,
        lock_value: str | None,
    ) -> bool:
        if lock_value is None:
            await redis.set(
                cls.SESSION_SCAN_CURSOR_KEY,
                str(cursor),
                ex=cls.CURSOR_EXPIRE_SECONDS,
            )
            await redis.set(
                cls.SESSION_SCAN_EPOCH_KEY,
                scan_epoch,
                ex=cls.CURSOR_EXPIRE_SECONDS,
            )
            return True
        result = await redis.eval(
            cls.STORE_PROGRESS_SCRIPT,
            3,
            cls.LOCK_KEY,
            cls.SESSION_SCAN_CURSOR_KEY,
            cls.SESSION_SCAN_EPOCH_KEY,
            lock_value,
            str(cursor),
            scan_epoch,
            str(cls.CURSOR_EXPIRE_SECONDS),
        )
        return int(result) == 1

    @classmethod
    async def _complete_scan(
        cls,
        redis: Redis,
        *,
        scan_epoch: str,
        lock_value: str | None,
    ) -> bool:
        if lock_value is None:
            await redis.set(
                cls.SESSION_SCAN_WATERMARK_KEY,
                scan_epoch,
                ex=cls.CURSOR_EXPIRE_SECONDS,
            )
            await redis.delete(cls.SESSION_SCAN_CURSOR_KEY, cls.SESSION_SCAN_EPOCH_KEY)
            return True
        result = await redis.eval(
            cls.COMPLETE_SCAN_SCRIPT,
            4,
            cls.LOCK_KEY,
            cls.SESSION_SCAN_CURSOR_KEY,
            cls.SESSION_SCAN_EPOCH_KEY,
            cls.SESSION_SCAN_WATERMARK_KEY,
            lock_value,
            scan_epoch,
            str(cls.CURSOR_EXPIRE_SECONDS),
        )
        return int(result) == 1

    @classmethod
    async def rebuild_indexes(
        cls,
        redis: Redis,
        *,
        scan_count: int = 100,
        max_batches: int = 20,
        lock_value: str | None = None,
    ) -> int:
        cursor_raw = _as_text(await redis.get(cls.SESSION_SCAN_CURSOR_KEY))
        try:
            cursor = int(cursor_raw or 0)
        except ValueError:
            cursor = 0
        epoch_raw = _as_text(await redis.get(cls.SESSION_SCAN_EPOCH_KEY))
        scan_epoch = epoch_raw or datetime.now(UTC).isoformat()
        rebuilt = 0
        for _ in range(max_batches):
            cursor, keys = await redis.scan(
                cursor=cursor,
                match=f"{RedisInitKeyConfig.USER_SESSION.key}:*",
                count=scan_count,
            )
            for raw_key in keys:
                key = _as_text(raw_key)
                if not key:
                    continue
                raw = _as_text(await redis.get(key))
                if not raw:
                    continue
                try:
                    session = json.loads(raw)
                    session_id = session["session_id"]
                    site_id = int(session["site_id"])
                    tenant_id = int(session["tenant_id"])
                    user_id = int(session["user_id"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                ttl = await redis.ttl(key)
                expire = ttl if ttl > 0 else settings.REFRESH_TOKEN_EXPIRE_SECONDS
                await cls._add_recovered(
                    redis,
                    session_id=session_id,
                    site_id=site_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    expire=expire,
                )
                rebuilt += 1
            if int(cursor) == 0:
                completed = await cls._complete_scan(
                    redis,
                    scan_epoch=scan_epoch,
                    lock_value=lock_value,
                )
                if not completed:
                    raise RuntimeError("联邦会话恢复扫描锁所有权已丢失")
                break
            stored = await cls._store_scan_progress(
                redis,
                cursor=int(cursor),
                scan_epoch=scan_epoch,
                lock_value=lock_value,
            )
            if not stored:
                raise RuntimeError("联邦会话恢复扫描锁所有权已丢失")
        return rebuilt

    @staticmethod
    async def _add_recovered(
        redis: Redis,
        *,
        session_id: str,
        site_id: int,
        tenant_id: int,
        user_id: int,
        expire: int,
    ) -> None:
        await UserSessionRegistry.add(
            redis,
            session_id=session_id,
            site_id=site_id,
            tenant_id=tenant_id,
            user_id=user_id,
            expire=expire,
        )

    @classmethod
    async def acquire_lock(cls, redis: Redis, *, expire: int = 300) -> str | None:
        value = uuid.uuid4().hex
        acquired = await redis.set(cls.LOCK_KEY, value, ex=expire, nx=True)
        return value if acquired else None

    @classmethod
    async def release_lock(cls, redis: Redis, value: str) -> None:
        await redis.eval(cls.RELEASE_LOCK_SCRIPT, 1, cls.LOCK_KEY, value)

    @classmethod
    async def renew_lock(cls, redis: Redis, value: str, *, expire: int) -> bool:
        result = await redis.eval(
            cls.RENEW_LOCK_SCRIPT,
            1,
            cls.LOCK_KEY,
            value,
            str(expire),
        )
        return int(result) == 1

    @classmethod
    async def _store_pending_cursor(
        cls,
        redis: Redis,
        *,
        cursor: int,
        lock_value: str | None,
    ) -> bool:
        if lock_value is None:
            await redis.set(
                cls.PENDING_CURSOR_KEY,
                str(cursor),
                ex=cls.CURSOR_EXPIRE_SECONDS,
            )
            return True
        result = await redis.eval(
            cls.STORE_PENDING_CURSOR_SCRIPT,
            2,
            cls.LOCK_KEY,
            cls.PENDING_CURSOR_KEY,
            lock_value,
            str(cursor),
            str(cls.CURSOR_EXPIRE_SECONDS),
        )
        return int(result) == 1

    @classmethod
    async def _delete_pending_cursor(
        cls,
        redis: Redis,
        *,
        lock_value: str | None,
    ) -> bool:
        if lock_value is None:
            await redis.delete(cls.PENDING_CURSOR_KEY)
            return True
        result = await redis.eval(
            cls.DELETE_PENDING_CURSOR_SCRIPT,
            2,
            cls.LOCK_KEY,
            cls.PENDING_CURSOR_KEY,
            lock_value,
        )
        return int(result) == 1

    @classmethod
    async def compensate_pending(
        cls,
        redis: Redis,
        *,
        batch_size: int = 50,
        max_batches: int = 10,
        lock_value: str | None = None,
    ) -> int:
        from app.api.v1.module_system.federated_access.tenant_role_lock import (
            lock_tenant_membership_users,
            lock_tenant_role_assignment,
        )
        from app.core.database import async_db_session

        watermark_raw = _as_text(await redis.get(cls.SESSION_SCAN_WATERMARK_KEY))
        if not watermark_raw:
            return 0
        try:
            watermark = datetime.fromisoformat(watermark_raw)
            if watermark.tzinfo is None:
                watermark = watermark.replace(tzinfo=UTC)
        except ValueError:
            logger.warning("联邦会话恢复扫描水位无效，暂不清理 pending")
            return 0

        compensated = 0
        cursor_raw = _as_text(await redis.get(cls.PENDING_CURSOR_KEY))
        try:
            cursor = int(cursor_raw or 0)
        except ValueError:
            cursor = 0
        for _ in range(max_batches):
            async with async_db_session() as page_db:
                rows = list(
                    (
                        await page_db.execute(
                            select(
                                FederatedAccessEntitlementModel.id,
                                FederatedAccessEntitlementModel.site_id,
                                FederatedAccessEntitlementModel.tenant_id,
                                FederatedAccessEntitlementModel.local_user_id,
                                FederatedAccessEntitlementModel.last_synced_at,
                            )
                            .where(
                                FederatedAccessEntitlementModel.id > cursor,
                                FederatedAccessEntitlementModel.status == "inactive",
                                FederatedAccessEntitlementModel.session_cleanup_pending.is_(True),
                                FederatedAccessEntitlementModel.last_synced_at <= watermark,
                            )
                            .order_by(FederatedAccessEntitlementModel.id)
                            .limit(batch_size)
                        )
                    ).all()
                )
            if not rows:
                if not await cls._delete_pending_cursor(redis, lock_value=lock_value):
                    raise RuntimeError("联邦会话恢复补偿锁所有权已丢失")
                break

            for entitlement_id, site_id, tenant_id, user_id, _last_synced_at in rows:
                cursor = int(entitlement_id)
                try:
                    fence_scopes = []
                    if user_id is not None:
                        fence_scopes.append((int(site_id), int(tenant_id), int(user_id)))
                    async with UserSessionRegistry.user_fences(
                        redis, fence_scopes
                    ) as fence_ownerships:
                        async with async_db_session() as db:
                            async with db.begin():
                                async with lock_tenant_role_assignment(db, int(tenant_id)):
                                    user_ids = [] if user_id is None else [int(user_id)]
                                    async with lock_tenant_membership_users(db, user_ids):
                                        entitlement = (
                                            await db.execute(
                                                select(FederatedAccessEntitlementModel)
                                                .where(
                                                    FederatedAccessEntitlementModel.id == entitlement_id,
                                                    FederatedAccessEntitlementModel.status == "inactive",
                                                    FederatedAccessEntitlementModel.session_cleanup_pending.is_(True),
                                                    FederatedAccessEntitlementModel.last_synced_at <= watermark,
                                                )
                                                .with_for_update()
                                            )
                                        ).scalar_one_or_none()
                                        if entitlement is None:
                                            continue
                                        await UserSessionRegistry.ensure_ownerships(
                                            fence_ownerships
                                        )
                                        if user_id is not None:
                                            await UserSessionRegistry.revoke_user(
                                                redis,
                                                int(site_id),
                                                int(tenant_id),
                                                int(user_id),
                                                _fence_ownerships=fence_ownerships,
                                            )
                                        entitlement.session_cleanup_pending = False
                                        receipts = (
                                            await db.execute(
                                                select(FederatedAccessEventModel).where(
                                                    FederatedAccessEventModel.entitlement_id
                                                    == entitlement.id,
                                                    FederatedAccessEventModel.sync_version
                                                    <= entitlement.applied_version,
                                                )
                                            )
                                        ).scalars()
                                        for receipt in receipts:
                                            if receipt.result_json.get(
                                                "session_cleanup_pending"
                                            ) is True:
                                                receipt.result_json = {
                                                    **receipt.result_json,
                                                    "session_cleanup_pending": False,
                                                }
                                        await UserSessionRegistry.ensure_ownerships(
                                            fence_ownerships
                                        )
                                        await db.flush()
                            # Count only after the transaction context confirms commit.
                            compensated += 1
                except Exception:
                    logger.exception(
                        "联邦会话待补偿清理失败: entitlement_id={}",
                        entitlement_id,
                    )
            if len(rows) < batch_size:
                if not await cls._delete_pending_cursor(redis, lock_value=lock_value):
                    raise RuntimeError("联邦会话恢复补偿锁所有权已丢失")
                break
            if not await cls._store_pending_cursor(
                redis,
                cursor=cursor,
                lock_value=lock_value,
            ):
                raise RuntimeError("联邦会话恢复补偿锁所有权已丢失")
        return compensated

    @classmethod
    async def run_once(
        cls,
        redis: Redis,
        *,
        scan_count: int = 100,
        max_scan_batches: int = 20,
        pending_batch_size: int = 50,
        max_pending_batches: int = 10,
        lock_expire: int = 300,
    ) -> dict[str, int | bool]:
        lock_value = await cls.acquire_lock(redis, expire=lock_expire)
        if lock_value is None:
            return {"lock_acquired": False, "rebuilt": 0, "compensated": 0}
        lost_lock = asyncio.Event()
        stop_heartbeat = asyncio.Event()
        heartbeat_failure: Exception | None = None

        async def heartbeat() -> None:
            nonlocal heartbeat_failure
            delay = max(lock_expire / 3, 0.05)
            while True:
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=delay)
                    return
                except TimeoutError:
                    try:
                        renewed = await cls.renew_lock(
                            redis, lock_value, expire=lock_expire
                        )
                    except Exception as exc:
                        heartbeat_failure = exc
                        lost_lock.set()
                        raise
                    if not renewed:
                        lost_lock.set()
                        return

        heartbeat_task = asyncio.create_task(heartbeat(), name="federated-session-recovery-lock-heartbeat")
        body_error: BaseException | None = None
        try:
            rebuilt = await cls.rebuild_indexes(
                redis,
                scan_count=scan_count,
                max_batches=max_scan_batches,
                lock_value=lock_value,
            )
            if lost_lock.is_set():
                if heartbeat_failure is not None:
                    raise heartbeat_failure
                raise RuntimeError("联邦会话恢复扫描锁所有权已丢失")
            compensated = await cls.compensate_pending(
                redis,
                batch_size=pending_batch_size,
                max_batches=max_pending_batches,
                lock_value=lock_value,
            )
            if lost_lock.is_set():
                if heartbeat_failure is not None:
                    raise heartbeat_failure
                raise RuntimeError("联邦会话恢复补偿锁所有权已丢失")
            return {
                "lock_acquired": True,
                "rebuilt": rebuilt,
                "compensated": compensated,
            }
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            heartbeat_error: Exception | None = None
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                heartbeat_error = exc
            finally:
                await cls.release_lock(redis, lock_value)
            if heartbeat_error is not None:
                if body_error is None:
                    raise heartbeat_error
                logger.error(
                    "联邦会话恢复锁续租异常: {}",
                    heartbeat_error,
                )

    @classmethod
    def start(
        cls,
        redis: Redis,
        *,
        interval_seconds: float = 60.0,
    ) -> asyncio.Task:
        async def worker() -> None:
            while True:
                try:
                    await cls.run_once(redis)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("联邦会话恢复扫描失败")
                await asyncio.sleep(interval_seconds)

        return asyncio.create_task(worker(), name="federated-session-recovery")

    @staticmethod
    async def stop(task: asyncio.Task | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
