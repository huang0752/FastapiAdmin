"""
conftest — 模块化 API 接口测试共享 fixture。

提供:
- test_client: FastAPI TestClient 实例 (session 级复用)
- assert_route: 验证接口路由存在 (status_code != 404)
"""

import fnmatch
import json
import os
import sys
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

# ============================================================
# 测试环境变量
# ============================================================

_TEST_DB_PATH = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

os.environ["DATABASE_TYPE"] = "sqlite"
os.environ["DATABASE_NAME"] = _TEST_DB_PATH
os.environ["REDIS_ENABLE"] = "true"
os.environ["POOL_SIZE"] = "1"
os.environ["MAX_OVERFLOW"] = "1"

from app.config.setting import settings

settings.DATABASE_TYPE = "sqlite"
settings.DATABASE_NAME = _TEST_DB_PATH
settings.REDIS_ENABLE = True
settings.POOL_SIZE = 1
settings.MAX_OVERFLOW = 1
settings.CAPTCHA_ENABLE = False  # 测试环境关闭验证码

# ============================================================
# Mock Redis — dict 存储，支持 get/set/delete/exists/keys/ttl/expire
# 登录成功后写入的 session 数据可在后续请求中正确读取
# ============================================================

_mock_redis_store: dict[bytes, bytes] = {}


def _redis_get(name: bytes | str) -> bytes | None:
    return _mock_redis_store.get(_redis_bytes(name))


async def _redis_set(name: bytes | str, value: bytes | str, ex: int | None = None, nx: bool = False) -> bool | None:
    name = _redis_bytes(name)
    value = _redis_bytes(value)
    if nx and name in _mock_redis_store:
        return None
    _mock_redis_store[name] = value
    return True


async def _redis_delete(*names: bytes | str) -> int:
    count = 0
    for raw_name in names:
        n = _redis_bytes(raw_name)
        if _mock_redis_store.pop(n, None) is not None:
            count += 1
    return count


def _redis_keys(pattern: bytes | str | None = None) -> list[bytes]:
    normalized_pattern = None if pattern is None else _redis_bytes(pattern)
    if normalized_pattern == b"*" or normalized_pattern is None:
        return list(_mock_redis_store.keys())
    return [k for k in _mock_redis_store if k.startswith(normalized_pattern.replace(b"*", b""))]


def _redis_exists(*names: bytes | str) -> int:
    return sum(1 for name in names if _redis_bytes(name) in _mock_redis_store)


def _redis_ttl(name: bytes | str) -> int:
    return 3600 if _redis_bytes(name) in _mock_redis_store else -2


async def _redis_expire(name: bytes | str, time: int) -> bool:
    return _redis_bytes(name) in _mock_redis_store


async def _redis_flushall(asynchronous: bool = False) -> bool:
    _mock_redis_store.clear()
    return True


async def _redis_flushdb(asynchronous: bool = False) -> bool:
    _mock_redis_store.clear()
    return True


async def _redis_close() -> None:
    pass


async def _redis_aclose() -> None:
    pass


async def _redis_hmget(name: bytes, keys: list[bytes]) -> list[bytes | None]:
    return [_mock_redis_store.get(name + b":" + k) for k in keys]


async def _redis_hset(name: bytes, key: bytes, value: bytes) -> int:
    _mock_redis_store[name + b":" + key] = value
    return 1


async def _redis_hgetall(name: bytes) -> dict[bytes, bytes]:
    prefix = name + b":"
    return {k[len(prefix) :]: v for k, v in _mock_redis_store.items() if k.startswith(prefix)}


async def _redis_hdel(name: bytes, *keys: bytes) -> int:
    count = 0
    for k in keys:
        if _mock_redis_store.pop(name + b":" + k, None) is not None:
            count += 1
    return count


async def _redis_sadd(name: bytes | str, *values: bytes | str) -> int:
    prefix = (name if isinstance(name, bytes) else str(name).encode()) + b":"
    count = 0
    for value in values:
        member = value if isinstance(value, bytes) else str(value).encode()
        key = prefix + member
        if key not in _mock_redis_store:
            count += 1
        _mock_redis_store[key] = b"1"
    return count


async def _redis_srem(name: bytes | str, *values: bytes | str) -> int:
    prefix = (name if isinstance(name, bytes) else str(name).encode()) + b":"
    count = 0
    for value in values:
        member = value if isinstance(value, bytes) else str(value).encode()
        if _mock_redis_store.pop(prefix + member, None) is not None:
            count += 1
    return count


async def _redis_smembers(name: bytes | str) -> set[bytes]:
    prefix = (name if isinstance(name, bytes) else str(name).encode()) + b":"
    return {normalized_key[len(prefix) :] for key in _mock_redis_store if (normalized_key := key if isinstance(key, bytes) else str(key).encode()).startswith(prefix)}


def _redis_info(section: str | None = None) -> dict:
    return {}


def _redis_dbsize() -> int:
    return len(_mock_redis_store)


def _redis_bytes(value: bytes | str) -> bytes:
    return value if isinstance(value, bytes) else str(value).encode()


async def _redis_scan(
    cursor: int = 0,
    match: bytes | str | None = None,
    count: int | None = None,
) -> tuple[int, list[bytes]]:
    pattern = "*" if match is None else _redis_bytes(match).decode()
    keys = sorted(key for key in _mock_redis_store if fnmatch.fnmatch(_redis_bytes(key).decode(), pattern))
    size = count or len(keys) or 1
    page = keys[cursor : cursor + size]
    next_cursor = cursor + size
    return (0 if next_cursor >= len(keys) else next_cursor, page)


async def _redis_eval(script: str, numkeys: int, *args) -> int:
    keys = [_redis_bytes(value) for value in args[:numkeys]]
    argv = [_redis_bytes(value) for value in args[numkeys:]]

    if "SESSION_CREATE_V1" in script:
        try:
            session = json.loads(argv[2])
        except (TypeError, ValueError):
            return -2
        expected_fence = _redis_bytes(
            f"user_session_fence:{argv[7].decode()}:{argv[8].decode()}:{argv[9].decode()}"
        )
        expected_index = _redis_bytes(
            f"user_session_index:{argv[7].decode()}:{argv[8].decode()}:{argv[9].decode()}"
        )
        if (
            keys[0] != expected_fence
            or keys[4] != expected_index
            or int(session.get("site_id") or 0) != int(argv[7])
            or int(session.get("tenant_id") or 0) != int(argv[8])
            or int(session.get("user_id") or 0) != int(argv[9])
        ):
            return -2
        if _mock_redis_store.get(keys[0]) != argv[0]:
            return 0
        await _redis_set(keys[1], argv[2])
        await _redis_set(keys[2], argv[3])
        await _redis_set(keys[3], argv[4])
        await _redis_sadd(keys[4], argv[1])
        return 1
    if "SESSION_INDEX_ADD_V1" in script:
        return await _redis_sadd(keys[0], argv[0])
    if "SESSION_REVOKE_ONE_V1" in script:
        if _mock_redis_store.get(keys[4]) != argv[4]:
            return -2
        raw = _mock_redis_store.get(keys[1])
        if raw is None:
            await _redis_srem(keys[0], argv[0])
            return 0
        session = json.loads(raw)
        matches = int(session["site_id"]) == int(argv[1]) and int(session["tenant_id"]) == int(argv[2]) and int(session["user_id"]) == int(argv[3])
        if not matches:
            await _redis_srem(keys[0], argv[0])
            return 0
        await _redis_delete(*keys[1:4])
        await _redis_srem(keys[0], argv[0])
        return 1
    if "SESSION_DELETE_V1" in script:
        if argv[3] and _mock_redis_store.get(keys[3]) != argv[3]:
            return -2
        raw = _mock_redis_store.get(keys[0])
        if argv[1] and _mock_redis_store.get(keys[1]) != argv[1]:
            return -1
        if raw is not None:
            session = json.loads(raw)
            index_key = _redis_bytes(f"{argv[2].decode()}:{session['site_id']}:{session['tenant_id']}:{session['user_id']}")
            await _redis_srem(index_key, argv[0])
        await _redis_delete(*keys[:3])
        return int(raw is not None)
    if "SESSION_CLEAN_ORPHAN_INDEX_V1" in script:
        if any(key in _mock_redis_store for key in keys[1:4]):
            return 0
        return await _redis_srem(keys[0], argv[0])
    if "SESSION_SWITCH_V1" in script:
        expected_lock = _redis_bytes(f"user_session_lock:{argv[6].decode()}")
        if (
            keys[4] != expected_lock
            or _mock_redis_store.get(keys[4]) != argv[7]
            or _mock_redis_store.get(keys[5]) != argv[8]
            or _mock_redis_store.get(keys[6]) != argv[9]
        ):
            return -2
        if _mock_redis_store.get(keys[0]) != argv[0] or _mock_redis_store.get(keys[1]) != argv[1]:
            return 0
        await _redis_set(keys[0], argv[2])
        await _redis_set(keys[1], argv[3])
        await _redis_sadd(keys[3], argv[6])
        if keys[2] != keys[3]:
            await _redis_srem(keys[2], argv[6])
        return 1
    if "SESSION_REFRESH_V1" in script:
        expected_lock = _redis_bytes(f"user_session_lock:{argv[6].decode()}")
        if (
            keys[4] != expected_lock
            or _mock_redis_store.get(keys[4]) != argv[7]
            or _mock_redis_store.get(keys[5]) != argv[8]
        ):
            return -2
        if _mock_redis_store.get(keys[0]) != argv[0] or _mock_redis_store.get(keys[2]) != argv[1]:
            return 0
        await _redis_set(keys[1], argv[2])
        await _redis_set(keys[2], argv[3])
        await _redis_sadd(keys[3], argv[6])
        return 1
    if "SESSION_LOCK_RELEASE_V1" in script:
        if _mock_redis_store.get(keys[0]) == argv[0]:
            return await _redis_delete(keys[0])
        return 0
    if "SESSION_LOCK_CHECK_V1" in script:
        return int(_mock_redis_store.get(keys[0]) == argv[0])
    if "SESSION_LOCK_RENEW_V1" in script:
        return int(_mock_redis_store.get(keys[0]) == argv[0])
    if "RECOVERY_LOCK_RENEW_V1" in script:
        return int(_mock_redis_store.get(keys[0]) == argv[0])
    if "RECOVERY_STORE_PROGRESS_V1" in script:
        if _mock_redis_store.get(keys[0]) != argv[0]:
            return 0
        await _redis_set(keys[1], argv[1])
        await _redis_set(keys[2], argv[2])
        return 1
    if "RECOVERY_COMPLETE_SCAN_V1" in script:
        if _mock_redis_store.get(keys[0]) != argv[0]:
            return 0
        await _redis_set(keys[3], argv[1])
        await _redis_delete(keys[1], keys[2])
        return 1
    if "RECOVERY_STORE_PENDING_CURSOR_V1" in script:
        if _mock_redis_store.get(keys[0]) != argv[0]:
            return 0
        await _redis_set(keys[1], argv[1])
        return 1
    if "RECOVERY_DELETE_PENDING_CURSOR_V1" in script:
        if _mock_redis_store.get(keys[0]) != argv[0]:
            return 0
        await _redis_delete(keys[1])
        return 1
    raise AssertionError("Mock Redis 尚未实现该 Lua 脚本")


_mock_redis = AsyncMock()
_mock_redis.ping = AsyncMock(return_value=True)
_mock_redis.get = AsyncMock(side_effect=_redis_get)
_mock_redis.set = AsyncMock(side_effect=_redis_set)
_mock_redis.delete = AsyncMock(side_effect=_redis_delete)
_mock_redis.keys = AsyncMock(side_effect=_redis_keys)
_mock_redis.exists = AsyncMock(side_effect=_redis_exists)
_mock_redis.ttl = AsyncMock(side_effect=_redis_ttl)
_mock_redis.expire = AsyncMock(side_effect=_redis_expire)
_mock_redis.flushall = AsyncMock(side_effect=_redis_flushall)
_mock_redis.flushdb = AsyncMock(side_effect=_redis_flushdb)
_mock_redis.close = AsyncMock(side_effect=_redis_close)
_mock_redis.aclose = AsyncMock(side_effect=_redis_aclose)
_mock_redis.hmget = AsyncMock(side_effect=_redis_hmget)
_mock_redis.hset = AsyncMock(side_effect=_redis_hset)
_mock_redis.hgetall = AsyncMock(side_effect=_redis_hgetall)
_mock_redis.hdel = AsyncMock(side_effect=_redis_hdel)
_mock_redis.sadd = AsyncMock(side_effect=_redis_sadd)
_mock_redis.srem = AsyncMock(side_effect=_redis_srem)
_mock_redis.smembers = AsyncMock(side_effect=_redis_smembers)
_mock_redis.scan = AsyncMock(side_effect=_redis_scan)
_mock_redis.eval = AsyncMock(side_effect=_redis_eval)
_mock_redis.info = AsyncMock(side_effect=_redis_info)
_mock_redis.dbsize = AsyncMock(side_effect=_redis_dbsize)

patch("redis.asyncio.Redis.from_url", return_value=_mock_redis).start()
patch("app.init_app.FastAPILimiter.init", new=AsyncMock()).start()
patch("app.init_app.FastAPILimiter.close", new=AsyncMock()).start()
patch("app.core.ap_scheduler.SchedulerUtil.init_scheduler", new=AsyncMock()).start()
patch("app.core.ap_scheduler.SchedulerUtil.shutdown", new=AsyncMock()).start()

# RateLimiter → no-op（需匹配 FastAPI 依赖注入签名）
from fastapi_limiter.depends import RateLimiter, WebSocketRateLimiter
from starlette.requests import Request
from starlette.responses import Response


async def _noop_rate_limit(self, request: Request, response: Response) -> None:
    pass


async def _noop_ws_rate_limit(self, websocket, context_key: str = "") -> None:
    pass


RateLimiter.__call__ = _noop_rate_limit
WebSocketRateLimiter.__call__ = _noop_ws_rate_limit

# ============================================================
# 精简 lifespan — 仅做数据库初始化
# ============================================================


@asynccontextmanager
async def _test_lifespan(app) -> AsyncGenerator[Any, None]:
    from app.scripts.initialize import InitializeData

    await InitializeData().init_db()
    app.state.redis = _mock_redis

    # 将 admin 密码重置为已知密码 "admin123"
    from sqlalchemy import update

    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session
    from app.utils.hash_bcrpy_util import PwdUtil

    async with async_db_session() as db:
        await db.execute(update(UserModel).where(UserModel.username == "admin").values(password=PwdUtil.hash_password("admin123")))
        await db.commit()

    yield


from main import create_app

_app = create_app()
_app.router.lifespan_context = _test_lifespan

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="session")
def _api_client() -> TestClient:
    """session 级共享 TestClient，所有测试复用同一个 app 实例。"""
    with TestClient(_app) as c:
        yield c


@pytest.fixture
def test_client(_api_client: TestClient) -> TestClient:
    """每个测试函数获取同一个 session 级 TestClient 的引用。"""
    return _api_client


@pytest.fixture(scope="session")
def auth_headers(_api_client: TestClient) -> dict[str, str]:
    """session 级 admin 认证头，登录一次，所有测试复用。"""
    resp = _api_client.post(
        "/system/auth/login",
        data={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200, f"admin 登录失败: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 公共辅助函数
# ============================================================


def assert_route(
    test_client: TestClient,
    method: str,
    path: str,
    *,
    expected_status: int | None = None,
    auth: dict[str, str] | None = None,
    **kwargs,
) -> None:
    """断言接口路由存在且返回码符合预期。

    Args:
        test_client: FastAPI TestClient 实例。
        method: HTTP 方法 (GET/POST/PUT/DELETE 等)。
        path: 接口路径。
        expected_status: 期望的 HTTP 状态码。为 None 时仅校验路由存在 (!= 404)。
        auth: 认证 headers（dict），传入则合并到请求头。
        **kwargs: 传递给 TestClient 请求方法的额外参数 (json/data/params/headers 等)。
    """
    headers: dict[str, str] = {}
    if auth:
        headers.update(auth)
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))
    if headers:
        kwargs["headers"] = headers

    response = test_client.request(method, path, **kwargs)

    if expected_status is not None:
        assert response.status_code == expected_status, f"{method} {path} 期望 {expected_status}，实际 {response.status_code}"
    else:
        assert response.status_code != 404, f"{method} {path} 返回 404，路由未注册"


@pytest.fixture
def control_provider_context(test_client, monkeypatch):
    """Provider API tests opt into capabilities without exposing them to SaaS tests."""
    import asyncio
    import copy
    import importlib

    from sqlalchemy import select

    from app.api.v1.module_platform.menu.model import MenuModel
    from app.core.assembly import get_assembly
    from app.core.database import async_db_session
    from app.plugin.module_task.runtime.registry import business_task_registry
    from app.scripts.initialize import InitializeData

    monkeypatch.setitem(get_assembly().feature_flags, "sso_provider", True)
    for module, handler in (
        ("app.plugin.module_control_provision.handlers", "control.tenant_provision"),
        ("app.plugin.module_control_provision.entitlement_handlers", "control.user_entitlement_sync"),
    ):
        imported = importlib.import_module(module)
        if handler not in {item.handler_code for item in business_task_registry.all()}:
            importlib.reload(imported)

    async def ensure_portal_menu():
        async with async_db_session() as db:
            if await db.scalar(select(MenuModel.id).where(MenuModel.route_name == "Control")):
                return
            seed = Path(__file__).parents[1] / "app/scripts/seeds/control/platform_menu.json"
            root = next(item for item in json.loads(seed.read_text()) if item.get("route_name") == "Control")
            objects = InitializeData._InitializeData__create_objects_with_children([copy.deepcopy(root)], MenuModel)
            db.add_all(objects)
            await db.commit()

    asyncio.run(ensure_portal_menu())
    yield
