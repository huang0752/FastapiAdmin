from __future__ import annotations

import asyncio
import fnmatch
import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.v1.module_monitor.online.service import OnlineService
from app.api.v1.module_system.auth.control_sso_schema import ControlIdentityClaims
from app.api.v1.module_system.auth.control_sso_service import ControlSSOClientService
from app.api.v1.module_system.auth.controller import AuthRouter
from app.api.v1.module_system.auth.service import LoginService
from app.api.v1.module_system.auth.session_registry import (
    FederatedSessionRecovery,
    RedisLockOwnership,
    UserSessionRegistry,
    require_active_federated_entitlement,
)
from app.config.setting import settings
from app.core.base_schema import AuthSchema, LogoutPayloadSchema
from app.core.dependencies import AuthPermission
from app.core.exceptions import CustomException
from app.core.request_context import RequestContext
from app.init_app import (
    federated_session_recovery_lifecycle,
    start_federated_session_recovery,
    stop_federated_session_recovery,
)


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.sets: dict[str, set[str]] = defaultdict(set)
        self.expirations: dict[str, int] = {}
        self.scan_calls = 0

    async def get(self, name: str):
        return self.values.get(str(name))

    async def set(
        self,
        name: str,
        value,
        ex: int | None = None,
        nx: bool = False,
    ):
        key = str(name)
        if nx and (key in self.values or key in self.sets):
            return None
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            key = str(name)
            deleted += int(self.values.pop(key, None) is not None)
            deleted += int(self.sets.pop(key, None) is not None)
            self.expirations.pop(key, None)
        return deleted

    async def sadd(self, name: str, *values: str) -> int:
        before = len(self.sets[str(name)])
        self.sets[str(name)].update(str(value) for value in values)
        return len(self.sets[str(name)]) - before

    async def srem(self, name: str, *values: str) -> int:
        removed = 0
        members = self.sets[str(name)]
        for value in values:
            if str(value) in members:
                members.remove(str(value))
                removed += 1
        if not members:
            self.sets.pop(str(name), None)
        return removed

    async def smembers(self, name: str) -> set[str]:
        return set(self.sets.get(str(name), set()))

    async def expire(self, name: str, seconds: int) -> bool:
        key = str(name)
        if key not in self.values and key not in self.sets:
            return False
        self.expirations[key] = seconds
        return True

    async def ttl(self, name: str) -> int:
        return self.expirations.get(str(name), -1)

    async def scan(
        self,
        cursor: int = 0,
        match: str | None = None,
        count: int | None = None,
    ) -> tuple[int, list[str]]:
        self.scan_calls += 1
        keys = sorted(set(self.values) | set(self.sets))
        if match is not None:
            keys = [key for key in keys if fnmatch.fnmatch(key, match)]
        size = count or len(keys) or 1
        page = keys[cursor : cursor + size]
        next_cursor = cursor + size
        return (0 if next_cursor >= len(keys) else next_cursor, page)

    async def eval(self, script: str, numkeys: int, *args):
        keys = [str(value) for value in args[:numkeys]]
        argv = [str(value) for value in args[numkeys:]]

        def as_text(value):
            return value.decode() if isinstance(value, bytes) else value

        if "SESSION_CREATE_V1" in script:
            try:
                session = json.loads(argv[2])
            except (TypeError, ValueError):
                return -2
            expected_fence = UserSessionRegistry.user_fence_key(
                int(argv[7]), int(argv[8]), int(argv[9])
            )
            expected_index = UserSessionRegistry.index_key(
                int(argv[7]), int(argv[8]), int(argv[9])
            )
            if (
                keys[0] != expected_fence
                or keys[4] != expected_index
                or int(session.get("site_id") or 0) != int(argv[7])
                or int(session.get("tenant_id") or 0) != int(argv[8])
                or int(session.get("user_id") or 0) != int(argv[9])
            ):
                return -2
            if as_text(self.values.get(keys[0])) != argv[0]:
                return 0
            self.values[keys[1]] = argv[2]
            self.expirations[keys[1]] = int(argv[5])
            self.values[keys[2]] = argv[3]
            self.expirations[keys[2]] = int(argv[6])
            self.values[keys[3]] = argv[4]
            self.expirations[keys[3]] = int(argv[5])
            self.sets[keys[4]].add(argv[1])
            self.expirations[keys[4]] = max(
                int(argv[5]), self.expirations.get(keys[4], -1)
            )
            return 1
        if "SESSION_INDEX_ADD_V1" in script:
            self.sets[keys[0]].add(argv[0])
            self.expirations[keys[0]] = max(int(argv[1]), self.expirations.get(keys[0], -1))
            return 1
        if "SESSION_INDEX_MOVE_V1" in script:
            raw = self.values.get(keys[0])
            if raw is None:
                return 0
            session = json.loads(raw)
            if int(session["site_id"]) != int(argv[1]) or int(session["tenant_id"]) != int(argv[2]) or int(session["user_id"]) != int(argv[3]):
                return 0
            self.sets[keys[2]].add(argv[0])
            self.expirations[keys[2]] = max(int(argv[4]), self.expirations.get(keys[2], -1))
            if keys[1] != keys[2]:
                await self.srem(keys[1], argv[0])
            return 1
        if "SESSION_DELETE_V1" in script:
            if argv[3] and as_text(self.values.get(keys[3])) != argv[3]:
                return -2
            raw = self.values.get(keys[0])
            if argv[1] and as_text(self.values.get(keys[1])) != argv[1]:
                return -1
            if raw is not None:
                session = json.loads(raw)
                index_key = f"{argv[2]}:{session['site_id']}:{session['tenant_id']}:{session['user_id']}"
                await self.srem(index_key, argv[0])
            await MemoryRedis.delete(self, *keys[:3])
            return int(raw is not None)
        if "SESSION_CLEAN_ORPHAN_INDEX_V1" in script:
            if any(
                key in self.values or key in self.sets
                for key in keys[1:4]
            ):
                return 0
            return await self.srem(keys[0], argv[0])
        if "SESSION_REVOKE_ONE_V1" in script:
            if as_text(self.values.get(keys[4])) != argv[4]:
                return -2
            raw = self.values.get(keys[1])
            if raw is None:
                await self.srem(keys[0], argv[0])
                return 0
            session = json.loads(raw)
            matches = int(session["site_id"]) == int(argv[1]) and int(session["tenant_id"]) == int(argv[2]) and int(session["user_id"]) == int(argv[3])
            if not matches:
                await self.srem(keys[0], argv[0])
                return 0
            await MemoryRedis.delete(self, *keys[1:4])
            await self.srem(keys[0], argv[0])
            return 1
        if "SESSION_SWITCH_V1" in script:
            if (
                keys[4] != UserSessionRegistry.lock_key(argv[6])
                or as_text(self.values.get(keys[4])) != argv[7]
                or as_text(self.values.get(keys[5])) != argv[8]
                or as_text(self.values.get(keys[6])) != argv[9]
            ):
                return -2
            if as_text(self.values.get(keys[0])) != argv[0] or as_text(self.values.get(keys[1])) != argv[1]:
                return 0
            self.values[keys[0]] = argv[2]
            self.expirations[keys[0]] = int(argv[4])
            self.values[keys[1]] = argv[3]
            self.expirations[keys[1]] = int(argv[5])
            self.sets[keys[3]].add(argv[6])
            self.expirations[keys[3]] = max(int(argv[4]), self.expirations.get(keys[3], -1))
            if keys[2] != keys[3]:
                await self.srem(keys[2], argv[6])
            return 1
        if "SESSION_REFRESH_V1" in script:
            if (
                keys[4] != UserSessionRegistry.lock_key(argv[6])
                or as_text(self.values.get(keys[4])) != argv[7]
                or as_text(self.values.get(keys[5])) != argv[8]
            ):
                return -2
            if as_text(self.values.get(keys[0])) != argv[0] or as_text(self.values.get(keys[2])) != argv[1]:
                return 0
            self.expirations[keys[0]] = int(argv[5])
            self.values[keys[1]] = argv[2]
            self.expirations[keys[1]] = int(argv[4])
            self.values[keys[2]] = argv[3]
            self.expirations[keys[2]] = int(argv[5])
            self.sets[keys[3]].add(argv[6])
            self.expirations[keys[3]] = max(int(argv[5]), self.expirations.get(keys[3], -1))
            return 1
        if "SESSION_LOCK_RELEASE_V1" in script:
            if as_text(self.values.get(keys[0])) == argv[0]:
                return await self.delete(keys[0])
            return 0
        if "SESSION_LOCK_CHECK_V1" in script:
            return int(as_text(self.values.get(keys[0])) == argv[0])
        if "SESSION_LOCK_RENEW_V1" in script:
            if as_text(self.values.get(keys[0])) == argv[0]:
                self.expirations[keys[0]] = int(argv[1])
                return 1
            return 0
        if "RECOVERY_LOCK_RENEW_V1" in script:
            if as_text(self.values.get(keys[0])) == argv[0]:
                self.expirations[keys[0]] = int(argv[1])
                return 1
            return 0
        if "RECOVERY_STORE_PROGRESS_V1" in script:
            if as_text(self.values.get(keys[0])) != argv[0]:
                return 0
            self.values[keys[1]] = argv[1]
            self.values[keys[2]] = argv[2]
            self.expirations[keys[1]] = int(argv[3])
            self.expirations[keys[2]] = int(argv[3])
            return 1
        if "RECOVERY_COMPLETE_SCAN_V1" in script:
            if as_text(self.values.get(keys[0])) != argv[0]:
                return 0
            self.values[keys[3]] = argv[1]
            self.expirations[keys[3]] = int(argv[2])
            await self.delete(keys[1], keys[2])
            return 1
        if "RECOVERY_STORE_PENDING_CURSOR_V1" in script:
            if as_text(self.values.get(keys[0])) != argv[0]:
                return 0
            self.values[keys[1]] = argv[1]
            self.expirations[keys[1]] = int(argv[2])
            return 1
        if "RECOVERY_DELETE_PENDING_CURSOR_V1" in script:
            if as_text(self.values.get(keys[0])) != argv[0]:
                return 0
            await self.delete(keys[1])
            return 1
        key, value = keys[0], argv[0]
        if as_text(self.values.get(key)) == value:
            return await self.delete(key)
        return 0


class ExpiringMemoryRedis(MemoryRedis):
    def __init__(self) -> None:
        super().__init__()
        self.deadlines: dict[str, float] = {}

    def _expire_if_needed(self, key: str) -> None:
        deadline = self.deadlines.get(key)
        if deadline is not None and time.monotonic() >= deadline:
            self.values.pop(key, None)
            self.deadlines.pop(key, None)

    async def get(self, name: str):
        key = str(name)
        self._expire_if_needed(key)
        return await super().get(key)

    async def set(
        self,
        name: str,
        value,
        ex: int | None = None,
        nx: bool = False,
    ):
        key = str(name)
        self._expire_if_needed(key)
        result = await super().set(key, value, ex=ex, nx=nx)
        if result and ex is not None:
            self.deadlines[key] = time.monotonic() + ex
        return result

    async def eval(self, script: str, numkeys: int, *args):
        key = str(args[0]) if args else ""
        self._expire_if_needed(key)
        if "SESSION_LOCK_RENEW_V1" in script:
            expected = str(args[numkeys])
            expire = int(args[numkeys + 1])
            if self.values.get(key) != expected:
                return 0
            self.deadlines[key] = time.monotonic() + expire
            return 1
        return await super().eval(script, numkeys, *args)


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        headers={"user-agent": "pytest"},
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )


def test_create_logout_and_online_delete_maintain_real_session_index() -> None:
    redis = MemoryRedis()
    user = SimpleNamespace(
        id=81,
        username="control_shadow",
        name="联邦用户",
        is_superuser=False,
        last_login=datetime.now(),
    )

    token = asyncio.run(
        LoginService.create_token(
            request=_request(),
            redis=redis,
            user=user,
            login_type="control_sso",
            tenant_id=23,
            site_id=7,
        )
    )
    session_key = next(key for key in redis.values if key.startswith("user_session:"))
    session_id = session_key.removeprefix("user_session:")
    index_key = "user_session_index:7:23:81"

    assert json.loads(redis.values[session_key])["session_id"] == session_id
    assert redis.sets[index_key] == {session_id}

    asyncio.run(
        LoginService.logout(
            redis=redis,
            token=LogoutPayloadSchema(token=token.access_token),
            current_token=token.access_token,
        )
    )
    assert session_key not in redis.values
    assert index_key not in redis.sets

    second = asyncio.run(
        LoginService.create_token(
            request=_request(),
            redis=redis,
            user=user,
            login_type="control_sso",
            tenant_id=23,
            site_id=7,
        )
    )
    del second
    second_session_key = next(key for key in redis.values if key.startswith("user_session:"))
    second_session_id = second_session_key.removeprefix("user_session:")
    asyncio.run(OnlineService.delete_online(redis, second_session_id))
    assert second_session_key not in redis.values
    assert index_key not in redis.sets


def test_clear_online_scans_without_keys_and_removes_all_session_artifacts() -> None:
    class NoKeysRedis(MemoryRedis):
        async def keys(self, *_args, **_kwargs):
            raise AssertionError("clear_online must not use KEYS")

    redis = NoKeysRedis()
    for session_id, user_id in (("clear-a", 81), ("clear-b", 82)):
        asyncio.run(
            UserSessionRegistry.add(
                redis,
                session_id=session_id,
                site_id=7,
                tenant_id=23,
                user_id=user_id,
                expire=900,
            )
        )
        redis.values[f"user_session:{session_id}"] = json.dumps(
            {
                "session_id": session_id,
                "site_id": 7,
                "tenant_id": 23,
                "user_id": user_id,
            }
        )
        redis.values[f"access_token:{session_id}"] = f"access-{session_id}"
        redis.values[f"refresh_token:{session_id}"] = f"refresh-{session_id}"

    asyncio.run(OnlineService.clear_online(redis))

    assert not any(key.startswith(("user_session:", "access_token:", "refresh_token:")) for key in redis.values)
    assert not redis.sets


def test_clear_online_removes_orphan_reverse_index_member_with_valid_lua_args() -> None:
    redis = MemoryRedis()
    index_key = UserSessionRegistry.index_key(7, 23, 81)
    redis.sets[index_key] = {"orphan-session"}

    asyncio.run(OnlineService.clear_online(redis))

    assert index_key not in redis.sets


def test_clear_online_propagates_raw_redis_scan_failure() -> None:
    class FailingScanRedis(MemoryRedis):
        async def scan(self, **_kwargs):
            raise ConnectionError("redis scan unavailable")

    with pytest.raises(ConnectionError, match="redis scan unavailable"):
        asyncio.run(OnlineService.clear_online(FailingScanRedis()))


def test_revoke_user_sessions_is_scoped_and_idempotent() -> None:
    redis = MemoryRedis()
    target_index = "user_session_index:7:23:81"
    other_index = "user_session_index:7:24:81"
    redis.sets[target_index] = {"session-a", "session-b"}
    redis.sets[other_index] = {"session-c"}
    for session_id, tenant_id in (
        ("session-a", 23),
        ("session-b", 23),
        ("session-c", 24),
    ):
        redis.values[f"user_session:{session_id}"] = json.dumps({"session_id": session_id, "site_id": 7, "tenant_id": tenant_id, "user_id": 81})
        redis.values[f"access_token:{session_id}"] = "access"
        redis.values[f"refresh_token:{session_id}"] = "refresh"

    assert asyncio.run(UserSessionRegistry.revoke_user(redis, 7, 23, 81)) == 2
    assert target_index not in redis.sets
    assert other_index in redis.sets
    assert "user_session:session-c" in redis.values
    assert asyncio.run(UserSessionRegistry.revoke_user(redis, 7, 23, 81)) == 0


def test_revoke_user_discards_stale_index_without_deleting_moved_session() -> None:
    redis = MemoryRedis()
    stale_index = "user_session_index:7:23:81"
    current_index = "user_session_index:7:24:81"
    redis.sets[stale_index] = {"session-moved"}
    redis.sets[current_index] = {"session-moved"}
    redis.values["user_session:session-moved"] = json.dumps(
        {
            "session_id": "session-moved",
            "site_id": 7,
            "tenant_id": 24,
            "user_id": 81,
        }
    )
    redis.values["access_token:session-moved"] = "access"
    redis.values["refresh_token:session-moved"] = "refresh"

    revoked = asyncio.run(UserSessionRegistry.revoke_user(redis, 7, 23, 81))

    assert revoked == 0
    assert stale_index not in redis.sets
    assert redis.sets[current_index] == {"session-moved"}
    assert "user_session:session-moved" in redis.values
    assert "access_token:session-moved" in redis.values
    assert "refresh_token:session-moved" in redis.values


def test_user_fence_serializes_session_registration_and_revoke() -> None:
    async def scenario():
        redis = MemoryRedis()
        registration_entered = asyncio.Event()
        allow_registration = asyncio.Event()
        session_id = "session-racing-login"

        async def register_session():
            async with UserSessionRegistry.user_fences(redis, [(7, 23, 81)]):
                registration_entered.set()
                await allow_registration.wait()
                redis.values[f"user_session:{session_id}"] = json.dumps(
                    {
                        "session_id": session_id,
                        "site_id": 7,
                        "tenant_id": 23,
                        "user_id": 81,
                    }
                )
                redis.values[f"access_token:{session_id}"] = "access"
                redis.values[f"refresh_token:{session_id}"] = "refresh"
                await UserSessionRegistry.add(
                    redis,
                    session_id=session_id,
                    site_id=7,
                    tenant_id=23,
                    user_id=81,
                    expire=900,
                )

        registration = asyncio.create_task(register_session())
        await asyncio.wait_for(registration_entered.wait(), timeout=1)
        revoke = asyncio.create_task(UserSessionRegistry.revoke_user(redis, 7, 23, 81))
        await asyncio.sleep(0)
        assert not revoke.done()
        allow_registration.set()
        await registration
        revoked = await revoke
        return redis, session_id, revoked

    redis, session_id, revoked = asyncio.run(scenario())

    assert revoked == 1
    assert f"user_session:{session_id}" not in redis.values
    assert f"access_token:{session_id}" not in redis.values
    assert f"refresh_token:{session_id}" not in redis.values
    assert "user_session_index:7:23:81" not in redis.sets


def test_move_failure_does_not_leave_partial_new_index() -> None:
    class FailAfterAddRedis(MemoryRedis):
        async def expire(self, name: str, seconds: int) -> bool:
            if str(name) == "user_session_index:7:24:81":
                raise ConnectionError("redis interrupted")
            return await super().expire(name, seconds)

        async def eval(self, script: str, numkeys: int, *args):
            if "SESSION_INDEX_MOVE_V1" in script:
                raise ConnectionError("redis interrupted")
            return await super().eval(script, numkeys, *args)

    redis = FailAfterAddRedis()
    redis.sets["user_session_index:7:23:81"] = {"session-switch"}
    redis.values["user_session:session-switch"] = json.dumps({"session_id": "session-switch", "site_id": 7, "tenant_id": 23, "user_id": 81})

    with pytest.raises(ConnectionError, match="interrupted"):
        asyncio.run(
            UserSessionRegistry.move(
                redis,
                session_id="session-switch",
                site_id=7,
                old_tenant_id=23,
                new_tenant_id=24,
                user_id=81,
                expire=900,
            )
        )

    assert redis.sets["user_session_index:7:23:81"] == {"session-switch"}
    assert "user_session_index:7:24:81" not in redis.sets


def test_delete_failure_does_not_partially_remove_session_keys() -> None:
    class PartialDeleteRedis(MemoryRedis):
        async def delete(self, *names: str) -> int:
            if len(names) > 1:
                await super().delete(names[0])
                raise ConnectionError("redis interrupted")
            return await super().delete(*names)

        async def eval(self, script: str, numkeys: int, *args):
            if "SESSION_DELETE_V1" in script:
                raise ConnectionError("redis interrupted")
            return await super().eval(script, numkeys, *args)

    redis = PartialDeleteRedis()
    session_id = "session-delete"
    redis.values[f"user_session:{session_id}"] = json.dumps({"session_id": session_id, "site_id": 7, "tenant_id": 23, "user_id": 81})
    redis.values[f"access_token:{session_id}"] = "access"
    redis.values[f"refresh_token:{session_id}"] = "refresh"
    redis.sets["user_session_index:7:23:81"] = {session_id}

    with pytest.raises(ConnectionError, match="interrupted"):
        asyncio.run(UserSessionRegistry.delete_session(redis, session_id))

    assert f"user_session:{session_id}" in redis.values
    assert f"access_token:{session_id}" in redis.values
    assert f"refresh_token:{session_id}" in redis.values
    assert redis.sets["user_session_index:7:23:81"] == {session_id}


def test_logout_delete_lua_rejects_session_lock_owner_changed_after_precheck() -> None:
    class StealSessionLockRedis(MemoryRedis):
        session_id = ""

        async def eval(self, script: str, numkeys: int, *args):
            if "SESSION_DELETE_V1" in script:
                self.values[UserSessionRegistry.lock_key(self.session_id)] = "new-owner"
            return await super().eval(script, numkeys, *args)

    redis = StealSessionLockRedis()
    user = SimpleNamespace(
        id=81,
        username="local-user",
        name="本地用户",
        is_superuser=True,
        last_login=datetime.now(),
    )
    issued = asyncio.run(
        LoginService.create_token(
            request=_request(),
            redis=redis,
            user=user,
            login_type="PC端",
            tenant_id=23,
            site_id=7,
        )
    )
    session_key = next(key for key in redis.values if key.startswith("user_session:"))
    session_id = session_key.removeprefix("user_session:")
    redis.session_id = session_id

    with pytest.raises(CustomException, match="锁所有权已丢失"):
        asyncio.run(
            LoginService.logout(
                redis,
                LogoutPayloadSchema(token=issued.access_token),
                issued.access_token,
            )
        )

    assert session_key in redis.values
    assert f"access_token:{session_id}" in redis.values
    assert f"refresh_token:{session_id}" in redis.values


def test_select_tenant_moves_session_between_user_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    session_id = "session-switch"
    session_info = {
        "session_id": session_id,
        "site_id": 7,
        "tenant_id": 23,
        "user_id": 81,
    }
    redis.values[f"user_session:{session_id}"] = json.dumps(session_info)
    redis.values[f"access_token:{session_id}"] = "access-current"
    redis.sets["user_session_index:7:23:81"] = {session_id}

    class _TenantDB:
        def __init__(self) -> None:
            self.results = iter(
                [
                    auth_user,
                    SimpleNamespace(id=24, site_id=7, name="新租户", status=0),
                ]
            )

        async def execute(self, _statement):
            return _ScalarResult(next(self.results))

    class _RequestDB:
        async def rollback(self) -> None:
            return None

    @asynccontextmanager
    async def scoped_db_factory():
        yield _TenantDB()

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7)

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        resolve_site,
    )
    request = _request()
    request.state.ctx = RequestContext(
        session_id=session_id,
        session_info=session_info,
    )
    auth_user = SimpleNamespace(
        id=81,
        username="control_shadow",
        is_superuser=True,
        auth_source="local",
        status=0,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.async_db_session",
        scoped_db_factory,
    )
    auth = AuthSchema.model_construct(
        db=_RequestDB(),
        user=auth_user,
        tenant_id=23,
        site_id=7,
        check_data_scope=False,
    )

    asyncio.run(
        LoginService(auth).select_tenant(
            request,
            redis,
            24,
            current_token="access-current",
        )
    )

    assert "user_session_index:7:23:81" not in redis.sets
    assert redis.sets["user_session_index:7:24:81"] == {session_id}


def test_revoke_lua_rejects_user_fence_owner_changed_after_precheck() -> None:
    class StealFenceRedis(MemoryRedis):
        async def eval(self, script: str, numkeys: int, *args):
            if "SESSION_REVOKE_ONE_V1" in script:
                self.values[UserSessionRegistry.user_fence_key(7, 23, 81)] = (
                    "new-owner"
                )
            return await super().eval(script, numkeys, *args)

    redis = StealFenceRedis()
    session_id = "revoke-owner-race"
    redis.values[f"user_session:{session_id}"] = json.dumps(
        {"session_id": session_id, "site_id": 7, "tenant_id": 23, "user_id": 81}
    )
    redis.values[f"access_token:{session_id}"] = "access"
    redis.values[f"refresh_token:{session_id}"] = "refresh"
    redis.sets["user_session_index:7:23:81"] = {session_id}

    with pytest.raises(CustomException, match="锁所有权已丢失"):
        asyncio.run(UserSessionRegistry.revoke_user(redis, 7, 23, 81))

    assert f"user_session:{session_id}" in redis.values
    assert f"access_token:{session_id}" in redis.values
    assert f"refresh_token:{session_id}" in redis.values


def test_concurrent_tenant_switches_allow_at_most_one_cas_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    user = SimpleNamespace(
        id=81,
        username="local-user",
        name="本地用户",
        is_superuser=True,
        auth_source="local",
        status=0,
        last_login=datetime.now(),
    )
    issued = asyncio.run(
        LoginService.create_token(
            request=_request(),
            redis=redis,
            user=user,
            login_type="PC端",
            tenant_id=23,
            site_id=7,
        )
    )
    session_key = next(key for key in redis.values if key.startswith("user_session:"))
    session_id = session_key.removeprefix("user_session:")
    initial_session = json.loads(redis.values[session_key])

    class _TenantDB:
        def __init__(self) -> None:
            self.results = iter(
                [
                    user,
                    SimpleNamespace(id=24, site_id=7, name="目标租户", status=0),
                ]
            )

        async def execute(self, _statement):
            return _ScalarResult(next(self.results))

    class _RequestDB:
        async def rollback(self) -> None:
            return None

    @asynccontextmanager
    async def scoped_db_factory():
        yield _TenantDB()

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7)

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        resolve_site,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.async_db_session",
        scoped_db_factory,
    )

    def make_request():
        request = _request()
        request.state.ctx = RequestContext(
            session_id=session_id,
            session_info=dict(initial_session),
        )
        return request

    auth = AuthSchema.model_construct(
        db=_RequestDB(),
        user=user,
        tenant_id=23,
        site_id=7,
        check_data_scope=False,
    )

    async def scenario():
        return await asyncio.gather(
            LoginService(auth).select_tenant(make_request(), redis, 24, current_token=issued.access_token),
            LoginService(auth).select_tenant(make_request(), redis, 25, current_token=issued.access_token),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, CustomException) for result in results) == 1
    stored = json.loads(redis.values[session_key])
    assert stored["tenant_id"] in {24, 25}
    assert redis.sets[f"user_session_index:7:{stored['tenant_id']}:81"] == {session_id}
    losing_tenant = 25 if stored["tenant_id"] == 24 else 24
    assert f"user_session_index:7:{losing_tenant}:81" not in redis.sets


def test_switch_lua_rejects_session_lock_owner_changed_after_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StealSessionLockRedis(MemoryRedis):
        async def eval(self, script: str, numkeys: int, *args):
            if "SESSION_SWITCH_V1" in script:
                self.values[UserSessionRegistry.lock_key("switch-owner-race")] = (
                    "new-owner"
                )
            return await super().eval(script, numkeys, *args)

    redis = StealSessionLockRedis()
    session_id = "switch-owner-race"
    session = {
        "session_id": session_id,
        "site_id": 7,
        "tenant_id": 23,
        "user_id": 81,
    }
    original_session = json.dumps(session)
    redis.values[f"user_session:{session_id}"] = original_session
    redis.values[f"access_token:{session_id}"] = "access-current"
    redis.sets["user_session_index:7:23:81"] = {session_id}
    user = SimpleNamespace(
        id=81,
        username="local-user",
        is_superuser=True,
        auth_source="local",
        status=0,
    )

    class _RequestDB:
        async def rollback(self) -> None:
            return None

    class _ScopedDB:
        def __init__(self) -> None:
            self.results = iter(
                [
                    user,
                    SimpleNamespace(id=24, site_id=7, name="新租户", status=0),
                ]
            )

        async def execute(self, _statement):
            return _ScalarResult(next(self.results))

    @asynccontextmanager
    async def scoped_db_factory():
        yield _ScopedDB()

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7)

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.async_db_session",
        scoped_db_factory,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        resolve_site,
    )
    request = _request()
    request.state.ctx = RequestContext(session_id=session_id, session_info=session)
    auth = AuthSchema.model_construct(
        db=_RequestDB(),
        user=user,
        tenant_id=23,
        site_id=7,
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="锁所有权已丢失"):
        asyncio.run(
            LoginService(auth).select_tenant(
                request,
                redis,
                24,
                current_token="access-current",
            )
        )

    assert redis.values[f"user_session:{session_id}"] == original_session
    assert redis.values[f"access_token:{session_id}"] == "access-current"
    assert redis.sets["user_session_index:7:23:81"] == {session_id}
    assert "user_session_index:7:24:81" not in redis.sets


@pytest.mark.parametrize("operation", ["switch", "refresh"])
def test_session_mutation_rejects_other_session_lock_scope_in_python(
    operation: str,
) -> None:
    redis = MemoryRedis()
    session_id = "scope-target"
    session = json.dumps(
        {
            "session_id": session_id,
            "site_id": 7,
            "tenant_id": 23,
            "user_id": 81,
        }
    )
    redis.values[UserSessionRegistry.session_key(session_id)] = session
    redis.values[UserSessionRegistry.access_key(session_id)] = "old-access"
    redis.values[UserSessionRegistry.refresh_key(session_id)] = "old-refresh"
    redis.sets[UserSessionRegistry.index_key(7, 23, 81)] = {session_id}
    other_lock = UserSessionRegistry.lock_key("scope-other")
    redis.values[other_lock] = "other-owner"
    mutation = RedisLockOwnership(
        redis=redis,
        key=other_lock,
        value="other-owner",
    )

    async def scenario() -> None:
        scopes = [(7, 23, 81)]
        if operation == "switch":
            scopes.append((7, 24, 81))
        async with UserSessionRegistry.user_fences(redis, scopes) as fences:
            if operation == "switch":
                await UserSessionRegistry.switch_tenant(
                    redis,
                    session_id=session_id,
                    site_id=7,
                    old_tenant_id=23,
                    new_tenant_id=24,
                    user_id=81,
                    expected_session=session,
                    expected_access_token="old-access",
                    new_session=session.replace('"tenant_id": 23', '"tenant_id": 24'),
                    new_access_token="new-access",
                    session_expire=900,
                    access_expire=300,
                    mutation_ownership=mutation,
                    fence_ownerships=fences,
                )
            else:
                await UserSessionRegistry.rotate_tokens(
                    redis,
                    session_id=session_id,
                    site_id=7,
                    tenant_id=23,
                    user_id=81,
                    expected_session=session,
                    expected_refresh_token="old-refresh",
                    new_access_token="new-access",
                    new_refresh_token="new-refresh",
                    access_expire=300,
                    refresh_expire=900,
                    mutation_ownership=mutation,
                    fence_ownerships=fences,
                )

    with pytest.raises(CustomException, match="锁作用域不匹配"):
        asyncio.run(scenario())

    assert redis.values[UserSessionRegistry.session_key(session_id)] == session
    assert redis.values[UserSessionRegistry.access_key(session_id)] == "old-access"
    assert redis.values[UserSessionRegistry.refresh_key(session_id)] == "old-refresh"


@pytest.mark.parametrize("operation", ["switch", "refresh"])
def test_session_mutation_lua_rejects_other_session_lock_scope(
    operation: str,
) -> None:
    redis = MemoryRedis()
    session_id = "lua-scope-target"
    session = json.dumps(
        {
            "session_id": session_id,
            "site_id": 7,
            "tenant_id": 23,
            "user_id": 81,
        }
    )
    session_key = UserSessionRegistry.session_key(session_id)
    access_key = UserSessionRegistry.access_key(session_id)
    refresh_key = UserSessionRegistry.refresh_key(session_id)
    old_fence = UserSessionRegistry.user_fence_key(7, 23, 81)
    new_fence = UserSessionRegistry.user_fence_key(7, 24, 81)
    other_lock = UserSessionRegistry.lock_key("lua-scope-other")
    redis.values[session_key] = session
    redis.values[access_key] = "old-access"
    redis.values[refresh_key] = "old-refresh"
    redis.values[other_lock] = "other-owner"
    redis.values[old_fence] = "old-fence-owner"
    redis.values[new_fence] = "new-fence-owner"

    if operation == "switch":
        result = asyncio.run(
            redis.eval(
                UserSessionRegistry.SWITCH_SCRIPT,
                7,
                session_key,
                access_key,
                UserSessionRegistry.index_key(7, 23, 81),
                UserSessionRegistry.index_key(7, 24, 81),
                other_lock,
                old_fence,
                new_fence,
                session,
                "old-access",
                session.replace('"tenant_id": 23', '"tenant_id": 24'),
                "new-access",
                "900",
                "300",
                session_id,
                "other-owner",
                "old-fence-owner",
                "new-fence-owner",
            )
        )
    else:
        result = asyncio.run(
            redis.eval(
                UserSessionRegistry.REFRESH_SCRIPT,
                6,
                session_key,
                access_key,
                refresh_key,
                UserSessionRegistry.index_key(7, 23, 81),
                other_lock,
                old_fence,
                session,
                "old-refresh",
                "new-access",
                "new-refresh",
                "300",
                "900",
                session_id,
                "other-owner",
                "old-fence-owner",
            )
        )

    assert result == -2
    assert redis.values[session_key] == session
    assert redis.values[access_key] == "old-access"
    assert redis.values[refresh_key] == "old-refresh"


def test_switch_waits_for_inflight_logout_and_session_cannot_be_resurrected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CoordinatedRedis(MemoryRedis):
        def __init__(self) -> None:
            super().__init__()
            self.logout_deleted = asyncio.Event()
            self.allow_logout_return = asyncio.Event()

        async def delete(self, *names: str) -> int:
            result = await super().delete(*names)
            if len(names) > 1 and any(str(name).startswith("user_session:") for name in names):
                self.logout_deleted.set()
                await self.allow_logout_return.wait()
            return result

        async def eval(self, script: str, numkeys: int, *args):
            result = await super().eval(script, numkeys, *args)
            if "SESSION_DELETE_V1" in script:
                self.logout_deleted.set()
                await self.allow_logout_return.wait()
            return result

    async def scenario():
        redis = CoordinatedRedis()
        user = SimpleNamespace(
            id=81,
            username="local-user",
            name="本地用户",
            is_superuser=True,
            auth_source="local",
            status=0,
            last_login=datetime.now(),
        )
        issued = await LoginService.create_token(
            request=_request(),
            redis=redis,
            user=user,
            login_type="PC端",
            tenant_id=23,
            site_id=7,
        )
        session_key = next(key for key in redis.values if key.startswith("user_session:"))
        session_id = session_key.removeprefix("user_session:")
        request = _request()
        request.state.ctx = RequestContext(
            session_id=session_id,
            session_info=json.loads(redis.values[session_key]),
        )

        class _RequestDB:
            async def rollback(self) -> None:
                return None

        auth = AuthSchema.model_construct(
            db=_RequestDB(),
            user=user,
            tenant_id=23,
            site_id=7,
            check_data_scope=False,
        )
        logout_task = asyncio.create_task(
            LoginService.logout(
                redis,
                LogoutPayloadSchema(token=issued.access_token),
                issued.access_token,
            )
        )
        await asyncio.wait_for(redis.logout_deleted.wait(), timeout=1)
        switch_task = asyncio.create_task(
            LoginService(auth).select_tenant(
                request,
                redis,
                24,
                current_token=issued.access_token,
            )
        )
        await asyncio.sleep(0)
        assert not switch_task.done()
        redis.allow_logout_return.set()
        await logout_task
        switch_result = await asyncio.gather(switch_task, return_exceptions=True)
        return redis, session_id, switch_result[0]

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7)

    class _ScopedDB:
        def __init__(self) -> None:
            self.results = iter(
                [
                    SimpleNamespace(
                        id=81,
                        username="local-user",
                        is_superuser=True,
                        auth_source="local",
                        status=0,
                    ),
                    SimpleNamespace(id=24, site_id=7, name="新租户", status=0),
                ]
            )

        async def execute(self, _statement):
            return _ScalarResult(next(self.results))

    @asynccontextmanager
    async def scoped_db_factory():
        yield _ScopedDB()

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        resolve_site,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.async_db_session",
        scoped_db_factory,
    )
    redis, session_id, switch_result = asyncio.run(scenario())

    assert isinstance(switch_result, CustomException)
    assert f"user_session:{session_id}" not in redis.values
    assert f"access_token:{session_id}" not in redis.values
    assert f"refresh_token:{session_id}" not in redis.values
    assert not any(session_id in members for members in redis.sets.values())


def test_refresh_extends_reverse_index_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = MemoryRedis()
    user = SimpleNamespace(
        id=81,
        username="local-user",
        name="本地用户",
        is_superuser=True,
        auth_source="local",
        status=0,
        last_login=datetime.now(),
    )
    issued = asyncio.run(
        LoginService.create_token(
            request=_request(),
            redis=redis,
            user=user,
            login_type="PC端",
            tenant_id=23,
            site_id=7,
        )
    )
    index_key = "user_session_index:7:23:81"
    redis.expirations[index_key] = 1

    class _RefreshDB:
        def __init__(self) -> None:
            self.results = iter(
                [
                    user,
                    SimpleNamespace(id=23, site_id=7, status=0),
                ]
            )

        async def execute(self, _statement):
            return _ScalarResult(next(self.results))

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7)

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        resolve_site,
    )
    asyncio.run(
        LoginService.refresh_token(
            request=_request(),
            db=_RefreshDB(),
            redis=redis,
            refresh_token=SimpleNamespace(refresh_token=issued.refresh_token),
        )
    )

    assert redis.expirations[index_key] == settings.REFRESH_TOKEN_EXPIRE_SECONDS


@pytest.mark.parametrize("stolen_owner", ["session", "user"])
def test_refresh_lua_rejects_owner_changed_after_precheck(
    monkeypatch: pytest.MonkeyPatch,
    stolen_owner: str,
) -> None:
    class StealOwnerRedis(MemoryRedis):
        session_id = ""

        async def eval(self, script: str, numkeys: int, *args):
            if "SESSION_REFRESH_V1" in script:
                key = (
                    UserSessionRegistry.lock_key(self.session_id)
                    if stolen_owner == "session"
                    else UserSessionRegistry.user_fence_key(7, 23, 81)
                )
                self.values[key] = "new-owner"
            return await super().eval(script, numkeys, *args)

    redis = StealOwnerRedis()
    user = SimpleNamespace(
        id=81,
        username="local-user",
        name="本地用户",
        is_superuser=True,
        auth_source="local",
        status=0,
        last_login=datetime.now(),
    )
    issued = asyncio.run(
        LoginService.create_token(
            request=_request(),
            redis=redis,
            user=user,
            login_type="PC端",
            tenant_id=23,
            site_id=7,
        )
    )
    session_key = next(key for key in redis.values if key.startswith("user_session:"))
    session_id = session_key.removeprefix("user_session:")
    redis.session_id = session_id

    class _RefreshDB:
        def __init__(self) -> None:
            self.results = iter(
                [
                    user,
                    SimpleNamespace(id=23, site_id=7, status=0),
                ]
            )

        async def execute(self, _statement):
            return _ScalarResult(next(self.results))

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7)

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        resolve_site,
    )

    with pytest.raises(CustomException, match="锁所有权已丢失"):
        asyncio.run(
            LoginService.refresh_token(
                request=_request(),
                db=_RefreshDB(),
                redis=redis,
                refresh_token=SimpleNamespace(refresh_token=issued.refresh_token),
            )
        )

    stored_access = redis.values[f"access_token:{session_id}"]
    stored_refresh = redis.values[f"refresh_token:{session_id}"]
    assert (
        stored_access.decode() if isinstance(stored_access, bytes) else stored_access
    ) == issued.access_token
    assert (
        stored_refresh.decode() if isinstance(stored_refresh, bytes) else stored_refresh
    ) == issued.refresh_token


def test_recovery_rebuilds_historical_index_with_bounded_scan() -> None:
    redis = MemoryRedis()
    for index in range(5):
        redis.values[f"user_session:legacy-{index}"] = json.dumps(
            {
                "session_id": f"legacy-{index}",
                "site_id": 3,
                "tenant_id": 4,
                "user_id": 5,
            }
        )

    rebuilt = asyncio.run(
        FederatedSessionRecovery.rebuild_indexes(
            redis,
            scan_count=2,
            max_batches=2,
        )
    )

    assert rebuilt == 4
    assert redis.scan_calls == 2
    assert redis.sets["user_session_index:3:4:5"] == {
        "legacy-0",
        "legacy-1",
        "legacy-2",
        "legacy-3",
    }


def test_recovery_resumes_bounded_historical_scan_on_next_run() -> None:
    redis = MemoryRedis()
    for session_id, user_id in (("legacy-a", 81), ("legacy-b", 82)):
        redis.values[f"user_session:{session_id}"] = json.dumps(
            {
                "session_id": session_id,
                "site_id": 7,
                "tenant_id": 23,
                "user_id": user_id,
            }
        )
        redis.expirations[f"user_session:{session_id}"] = 900

    first = asyncio.run(
        FederatedSessionRecovery.rebuild_indexes(
            redis,
            scan_count=1,
            max_batches=1,
        )
    )
    second = asyncio.run(
        FederatedSessionRecovery.rebuild_indexes(
            redis,
            scan_count=1,
            max_batches=1,
        )
    )

    assert first == 1
    assert second == 1
    assert redis.sets["user_session_index:7:23:81"] == {"legacy-a"}
    assert redis.sets["user_session_index:7:23:82"] == {"legacy-b"}


def test_recovery_run_once_is_guarded_by_distributed_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    redis.values[FederatedSessionRecovery.LOCK_KEY] = "other-worker"
    called = False

    async def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        FederatedSessionRecovery,
        "rebuild_indexes",
        classmethod(unexpected),
    )
    result = asyncio.run(FederatedSessionRecovery.run_once(redis))

    assert result == {"lock_acquired": False, "rebuilt": 0, "compensated": 0}
    assert called is False


def test_recovery_stale_owner_cannot_overwrite_shared_scan_cursor() -> None:
    redis = MemoryRedis()
    redis.values[FederatedSessionRecovery.LOCK_KEY] = "new-owner"
    redis.values[FederatedSessionRecovery.SESSION_SCAN_CURSOR_KEY] = "19"

    stored = asyncio.run(
        FederatedSessionRecovery._store_scan_progress(
            redis,
            cursor=7,
            scan_epoch="2026-09-01T00:00:00+00:00",
            lock_value="stale-owner",
        )
    )

    assert stored is False
    assert redis.values[FederatedSessionRecovery.SESSION_SCAN_CURSOR_KEY] == "19"


def test_recovery_stale_owner_cannot_move_pending_cursor_backwards() -> None:
    redis = MemoryRedis()
    redis.values[FederatedSessionRecovery.LOCK_KEY] = "new-owner"
    redis.values[FederatedSessionRecovery.PENDING_CURSOR_KEY] = "19"

    stored = asyncio.run(
        FederatedSessionRecovery._store_pending_cursor(
            redis,
            cursor=7,
            lock_value="stale-owner",
        )
    )

    assert stored is False
    assert redis.values[FederatedSessionRecovery.PENDING_CURSOR_KEY] == "19"


def test_recovery_lock_heartbeat_renews_ownership_during_long_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    scan_started = asyncio.Event()
    allow_scan_to_finish = asyncio.Event()

    async def slow_rebuild(_cls, _redis, **_kwargs):
        scan_started.set()
        await allow_scan_to_finish.wait()
        return 0

    async def no_pending(_cls, _redis, **_kwargs):
        return 0

    monkeypatch.setattr(FederatedSessionRecovery, "rebuild_indexes", classmethod(slow_rebuild))
    monkeypatch.setattr(FederatedSessionRecovery, "compensate_pending", classmethod(no_pending))

    async def scenario() -> None:
        task = asyncio.create_task(FederatedSessionRecovery.run_once(redis, lock_expire=1))
        await scan_started.wait()
        await asyncio.sleep(0.4)
        assert redis.expirations[FederatedSessionRecovery.LOCK_KEY] == 1
        allow_scan_to_finish.set()
        await task

    asyncio.run(scenario())


def test_session_lock_heartbeat_keeps_owner_past_original_ttl() -> None:
    async def scenario() -> None:
        redis = ExpiringMemoryRedis()
        key = UserSessionRegistry.lock_key("ttl-session")
        async with UserSessionRegistry._owned_lock(
            redis,
            key,
            expire=1,
            attempts=1,
            delay=0,
        ) as owner:
            await asyncio.sleep(1.2)
            assert await redis.get(key) == owner.value
            await owner.ensure_owned()

    asyncio.run(scenario())


def test_stale_session_lock_owner_fails_closed_before_write() -> None:
    async def scenario() -> None:
        redis = ExpiringMemoryRedis()
        key = UserSessionRegistry.lock_key("stolen-session")
        redis.values["protected-write"] = "before"
        with pytest.raises(CustomException, match="锁所有权已丢失"):
            async with UserSessionRegistry._owned_lock(
                redis,
                key,
                expire=1,
                attempts=1,
                delay=0,
            ) as owner:
                redis.values[key] = "new-owner"
                await owner.ensure_owned()
                redis.values["protected-write"] = "after"
        assert redis.values["protected-write"] == "before"

    asyncio.run(scenario())


def test_login_losing_user_fence_cannot_leave_partial_session_state() -> None:
    class LoseFenceOnWriteRedis(MemoryRedis):
        async def set(self, name: str, value, **kwargs):
            result = await super().set(name, value, **kwargs)
            if str(name).startswith("user_session:"):
                fence_key = UserSessionRegistry.user_fence_key(7, 23, 81)
                self.values[fence_key] = "new-owner"
            return result

        async def eval(self, script: str, numkeys: int, *args):
            if "SESSION_CREATE_V1" in script:
                self.values[str(args[0])] = "new-owner"
            return await super().eval(script, numkeys, *args)

    async def scenario() -> None:
        redis = LoseFenceOnWriteRedis()
        user = SimpleNamespace(
            id=81,
            username="control_shadow",
            name="联邦用户",
            is_superuser=False,
            last_login=datetime.now(),
        )
        with pytest.raises(CustomException, match="锁所有权已丢失"):
            async with UserSessionRegistry.user_fences(
                redis, [(7, 23, 81)]
            ) as ownerships:
                await LoginService.create_token(
                    request=_request(),
                    redis=redis,
                    user=user,
                    login_type="control_sso",
                    tenant_id=23,
                    site_id=7,
                    _fence_ownerships=ownerships,
                )
        assert not any(key.startswith("user_session:") for key in redis.values)
        assert not any(key.startswith("access_token:") for key in redis.values)
        assert not any(key.startswith("refresh_token:") for key in redis.values)

    asyncio.run(scenario())


def test_fenced_session_create_rejects_cross_scope_owner() -> None:
    async def scenario() -> None:
        redis = MemoryRedis()
        session_id = "cross-scope-create"
        async with UserSessionRegistry.user_fences(
            redis, [(7, 23, 81)]
        ) as ownerships:
            with pytest.raises(CustomException, match="作用域不匹配"):
                await UserSessionRegistry.create_fenced(
                    redis,
                    ownership=ownerships[0],
                    session_id=session_id,
                    session_info=json.dumps(
                        {
                            "session_id": session_id,
                            "site_id": 7,
                            "tenant_id": 24,
                            "user_id": 81,
                        }
                    ),
                    access_token="access",
                    refresh_token="refresh",
                    site_id=7,
                    tenant_id=24,
                    user_id=81,
                    access_expire=300,
                    refresh_expire=900,
                )
        assert f"user_session:{session_id}" not in redis.values
        assert f"access_token:{session_id}" not in redis.values
        assert f"refresh_token:{session_id}" not in redis.values
        assert session_id not in redis.sets.get(
            "user_session_index:7:24:81", set()
        )

    asyncio.run(scenario())


def test_session_create_lua_defends_scope_even_without_python_validation() -> None:
    async def scenario() -> None:
        redis = MemoryRedis()
        fence_key = UserSessionRegistry.user_fence_key(7, 23, 81)
        redis.values[fence_key] = "owner"
        session_id = "lua-cross-scope"
        result = await redis.eval(
            UserSessionRegistry.CREATE_SCRIPT,
            5,
            fence_key,
            f"user_session:{session_id}",
            f"access_token:{session_id}",
            f"refresh_token:{session_id}",
            "user_session_index:7:24:81",
            "owner",
            session_id,
            json.dumps(
                {
                    "session_id": session_id,
                    "site_id": 7,
                    "tenant_id": 24,
                    "user_id": 81,
                }
            ),
            "access",
            "refresh",
            "900",
            "300",
            "7",
            "24",
            "81",
        )

        assert int(result) == -2
        assert f"user_session:{session_id}" not in redis.values

    asyncio.run(scenario())


def test_control_login_releases_db_connection_before_waiting_for_user_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    provision_started = asyncio.Event()
    expected = SimpleNamespace(access_token="access", refresh_token="refresh")
    claims = ControlIdentityClaims(
        issuer="https://control.example/api/v1",
        central_user_uuid="subject-81",
        name="联邦用户",
        status=0,
        site_code="brand",
        central_tenant_code="central-tenant",
        target_tenant_code="tenant-23",
    )

    class _Result:
        def __init__(self, value) -> None:
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self, pool: asyncio.Semaphore) -> None:
            self.results = iter(
                [
                    SimpleNamespace(id=23, site_id=7, code="tenant-23"),
                    81,
                ]
            )
            self.rollback_called = asyncio.Event()
            self.pool = pool
            self.checked_out = False

        async def execute(self, _statement):
            if not self.checked_out:
                await self.pool.acquire()
                self.checked_out = True
            return _Result(next(self.results))

        async def rollback(self) -> None:
            if self.checked_out:
                self.checked_out = False
                self.pool.release()
            self.rollback_called.set()

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7, code="brand")

    async def exchange(_cls, _code, _site_code):
        return claims

    async def provision_and_login(_cls, **_kwargs):
        provision_started.set()
        return expected

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.control_sso_service.resolve_request_site",
        resolve_site,
    )
    monkeypatch.setattr(
        ControlSSOClientService,
        "_exchange_code",
        classmethod(exchange),
    )
    monkeypatch.setattr(
        ControlSSOClientService,
        "_provision_and_login",
        classmethod(provision_and_login),
    )

    async def scenario() -> None:
        # A one-connection pool exposes lock-order starvation immediately.
        pool = asyncio.Semaphore(1)
        db = _DB(pool)
        async with UserSessionRegistry.user_fences(redis, [(7, 23, 81)]):
            task = asyncio.create_task(
                ControlSSOClientService.exchange_and_login(
                    _request(), db, redis, "ticket"
                )
            )
            await asyncio.wait_for(db.rollback_called.wait(), timeout=0.2)
            await asyncio.wait_for(pool.acquire(), timeout=0.2)
            pool.release()
            await asyncio.sleep(0.1)
            assert provision_started.is_set() is False
        assert await task is expected
        assert provision_started.is_set() is True

    asyncio.run(scenario())


def test_select_tenant_releases_request_db_before_waiting_for_session_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    session_id = "select-lock-order"
    session = {
        "session_id": session_id,
        "site_id": 7,
        "tenant_id": 23,
        "user_id": 81,
    }
    redis.values[f"user_session:{session_id}"] = json.dumps(session)
    redis.values[f"access_token:{session_id}"] = "access-current"
    redis.sets["user_session_index:7:23:81"] = {session_id}
    pool = asyncio.Semaphore(1)

    class _RequestDB:
        def __init__(self) -> None:
            self.rollback_called = asyncio.Event()
            self.checked_out = False

        async def checkout(self) -> None:
            await pool.acquire()
            self.checked_out = True

        async def rollback(self) -> None:
            if self.checked_out:
                self.checked_out = False
                pool.release()
            self.rollback_called.set()

        async def execute(self, _statement):
            raise AssertionError("select_tenant reused request-scoped auth.db")

    user = SimpleNamespace(
        id=81,
        username="local-user",
        is_superuser=True,
        auth_source="local",
        status=0,
    )

    class _ScopedDB:
        def __init__(self) -> None:
            self.results = iter(
                [
                    user,
                    SimpleNamespace(id=24, site_id=7, name="新租户", status=0),
                ]
            )

        async def execute(self, _statement):
            return _ScalarResult(next(self.results))

    @asynccontextmanager
    async def scoped_db_factory():
        yield _ScopedDB()

    async def resolve_site(_db, _request):
        return SimpleNamespace(id=7)

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.async_db_session",
        scoped_db_factory,
        raising=False,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        resolve_site,
    )

    async def scenario() -> None:
        request_db = _RequestDB()
        await request_db.checkout()
        request = _request()
        request.state.ctx = RequestContext(
            session_id=session_id,
            session_info=session,
        )
        auth = AuthSchema.model_construct(
            db=request_db,
            user=user,
            tenant_id=23,
            site_id=7,
            check_data_scope=False,
        )
        async with UserSessionRegistry.mutation_lock(redis, session_id):
            task = asyncio.create_task(
                LoginService(auth).select_tenant(
                    request,
                    redis,
                    24,
                    current_token="access-current",
                )
            )
            await asyncio.wait_for(request_db.rollback_called.wait(), timeout=0.2)
            await asyncio.wait_for(pool.acquire(), timeout=0.2)
            pool.release()
            assert task.done() is False
        result = await task
        assert result.access_token

    asyncio.run(scenario())


def test_logout_route_does_not_checkout_database_before_session_lock() -> None:
    route = next(route for route in AuthRouter.routes if route.path == "/auth/logout")

    assert not any(
        isinstance(dependency.call, AuthPermission)
        for dependency in route.dependant.dependencies
    )


def test_select_tenant_route_has_single_authentication_dependency() -> None:
    route = next(
        route for route in AuthRouter.routes if route.path == "/auth/select-tenant"
    )

    assert sum(
        isinstance(dependency.call, AuthPermission)
        for dependency in route.dependant.dependencies
    ) == 1


def test_recovery_cancellation_does_not_wait_for_stuck_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StuckRenewRedis(MemoryRedis):
        def __init__(self) -> None:
            super().__init__()
            self.renew_started = asyncio.Event()

        async def eval(self, script: str, numkeys: int, *args):
            if "RECOVERY_LOCK_RENEW_V1" in script:
                self.renew_started.set()
                await asyncio.Event().wait()
            return await super().eval(script, numkeys, *args)

    redis = StuckRenewRedis()

    async def blocked_rebuild(_cls, _redis, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        FederatedSessionRecovery,
        "rebuild_indexes",
        classmethod(blocked_rebuild),
    )

    async def scenario() -> None:
        task = asyncio.create_task(FederatedSessionRecovery.run_once(redis, lock_expire=1))
        await asyncio.wait_for(redis.renew_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.2)

    asyncio.run(scenario())


def test_recovery_heartbeat_exception_still_releases_owned_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingRenewRedis(MemoryRedis):
        def __init__(self) -> None:
            super().__init__()
            self.renew_failed = asyncio.Event()
            self.release_calls = 0

        async def eval(self, script: str, numkeys: int, *args):
            if "RECOVERY_LOCK_RENEW_V1" in script:
                self.renew_failed.set()
                raise ConnectionError("renew failed")
            if "SESSION_LOCK_RELEASE_V1" in script:
                self.release_calls += 1
            return await super().eval(script, numkeys, *args)

    redis = ExplodingRenewRedis()
    compensate_called = False

    async def rebuild(_cls, _redis, **_kwargs):
        await asyncio.wait_for(redis.renew_failed.wait(), timeout=1)
        return 0

    async def no_pending(_cls, _redis, **_kwargs):
        nonlocal compensate_called
        compensate_called = True
        return 0

    monkeypatch.setattr(
        FederatedSessionRecovery,
        "rebuild_indexes",
        classmethod(rebuild),
    )
    monkeypatch.setattr(
        FederatedSessionRecovery,
        "compensate_pending",
        classmethod(no_pending),
    )

    with pytest.raises(ConnectionError, match="renew failed"):
        asyncio.run(FederatedSessionRecovery.run_once(redis, lock_expire=1))
    assert redis.release_calls == 1
    assert FederatedSessionRecovery.LOCK_KEY not in redis.values
    assert compensate_called is False


def test_recovery_background_task_is_cancelled_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def run_once(_cls, _redis, **_kwargs):
        started.set()
        return {"lock_acquired": True, "rebuilt": 0, "compensated": 0}

    monkeypatch.setattr(
        FederatedSessionRecovery,
        "run_once",
        classmethod(run_once),
    )

    async def scenario():
        task = FederatedSessionRecovery.start(MemoryRedis(), interval_seconds=3600)
        await asyncio.wait_for(started.wait(), timeout=1)
        await FederatedSessionRecovery.stop(task)
        return task

    task = asyncio.run(scenario())
    assert task.done()
    assert task.cancelled()


def test_recovery_lifecycle_stops_task_when_later_startup_step_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = SimpleNamespace(state=SimpleNamespace(redis=MemoryRedis()))
    stopped = False

    monkeypatch.setattr(
        "app.init_app.start_federated_session_recovery",
        lambda target: setattr(target.state, "federated_session_recovery_task", "started"),
    )

    async def stop(_app) -> None:
        nonlocal stopped
        stopped = True

    monkeypatch.setattr("app.init_app.stop_federated_session_recovery", stop)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="later startup failure"):
            async with federated_session_recovery_lifecycle(app):
                raise RuntimeError("later startup failure")

    asyncio.run(scenario())
    assert stopped is True


def test_lifespan_recovery_helpers_do_not_depend_on_task_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MemoryRedis()
    sentinel_task = SimpleNamespace()
    app = SimpleNamespace(state=SimpleNamespace(redis=redis))
    stopped: list[object] = []

    monkeypatch.setattr(
        FederatedSessionRecovery,
        "start",
        classmethod(lambda _cls, actual_redis: sentinel_task if actual_redis is redis else None),
    )

    async def stop(task):
        stopped.append(task)

    monkeypatch.setattr(
        FederatedSessionRecovery,
        "stop",
        staticmethod(stop),
    )

    start_federated_session_recovery(app)
    asyncio.run(stop_federated_session_recovery(app))

    assert app.state.federated_session_recovery_task is None
    assert stopped == [sentinel_task]


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _EntitlementDB:
    def __init__(self, entitlement_id: int | None) -> None:
        self.entitlement_id = entitlement_id
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.entitlement_id)


def test_active_entitlement_gate_is_fail_closed_only_for_federated_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
        True,
        raising=False,
    )
    federated = SimpleNamespace(id=81, auth_source="federated", is_superuser=False)

    with pytest.raises(
        CustomException,
        match="中控已撤销该产品访问权限",
    ) as denied:
        asyncio.run(
            require_active_federated_entitlement(
                _EntitlementDB(None),
                user=federated,
                site_id=7,
                tenant_id=23,
            )
        )
    assert denied.value.status_code == 401

    asyncio.run(
        require_active_federated_entitlement(
            _EntitlementDB(99),
            user=federated,
            site_id=7,
            tenant_id=23,
        )
    )
    local_db = _EntitlementDB(None)
    asyncio.run(
        require_active_federated_entitlement(
            local_db,
            user=SimpleNamespace(id=82, auth_source="local", is_superuser=False),
            site_id=7,
            tenant_id=23,
        )
    )
    super_db = _EntitlementDB(None)
    asyncio.run(
        require_active_federated_entitlement(
            super_db,
            user=SimpleNamespace(id=1, auth_source="local", is_superuser=True),
            site_id=7,
            tenant_id=23,
        )
    )
    assert local_db.statements == []
    assert super_db.statements == []

    monkeypatch.setattr(
        settings,
        "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
        False,
        raising=False,
    )
    disabled_db = _EntitlementDB(None)
    asyncio.run(
        require_active_federated_entitlement(
            disabled_db,
            user=federated,
            site_id=7,
            tenant_id=23,
        )
    )
    assert disabled_db.statements == []


def test_federated_tenant_options_are_filtered_by_active_entitlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements = []

    class _EmptyScalars:
        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        async def execute(self, statement):
            statements.append(statement)
            return _EmptyScalars()

    monkeypatch.setattr(
        settings,
        "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(settings, "CONTROL_SSO_ISSUER", "https://control.example")
    auth = AuthSchema.model_construct(
        db=_DB(),
        user=SimpleNamespace(
            id=81,
            auth_source="federated",
            is_superuser=False,
        ),
        tenant_id=23,
        site_id=7,
        check_data_scope=False,
    )

    assert asyncio.run(LoginService(auth).get_user_tenants()) == []
    statement = str(statements[0])
    assert "sys_federated_access_entitlement" in statement
    assert "sys_federated_access_entitlement.status" in statement

    statements.clear()
    superuser_auth = AuthSchema.model_construct(
        db=_DB(),
        user=SimpleNamespace(
            id=1,
            auth_source="federated",
            is_superuser=True,
        ),
        tenant_id=23,
        site_id=7,
        check_data_scope=False,
    )
    assert asyncio.run(LoginService(superuser_auth).get_user_tenants()) == []
    assert "sys_federated_access_entitlement" not in str(statements[0])


@pytest.mark.asyncio
async def test_inline_historical_scan_is_bounded_and_only_indexes_exact_user():
    redis = MemoryRedis()
    for sid, uid in [("legacy-a", 81), ("legacy-b", 82), ("legacy-c", 81)]:
        await redis.set(UserSessionRegistry.session_key(sid), json.dumps({"session_id": sid, "site_id": 7, "tenant_id": 23, "user_id": uid}), ex=120)
    async with UserSessionRegistry.user_fences(redis, [(7, 23, 81)]) as ownerships:
        complete = await UserSessionRegistry.index_user_historical_sessions(redis, 7, 23, 81, fence_ownerships=ownerships, scan_count=1, max_batches=1)
        assert complete is False
        assert redis.scan_calls == 1
        complete = await UserSessionRegistry.index_user_historical_sessions(redis, 7, 23, 81, fence_ownerships=ownerships, scan_count=10, max_batches=1)
        assert complete is True
        assert await redis.smembers(UserSessionRegistry.index_key(7, 23, 81)) == {"legacy-a", "legacy-c"}
        assert await redis.smembers(UserSessionRegistry.index_key(7, 23, 82)) == set()
        assert await UserSessionRegistry.revoke_user(redis, 7, 23, 81, _fence_ownerships=ownerships) == 2
    assert await redis.get(UserSessionRegistry.session_key("legacy-b")) is not None
