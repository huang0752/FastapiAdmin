"""Real Redis/Lua checks in child processes, isolated from conftest's Redis mock.

Starts only a private loopback Redis on an ephemeral port with persistence disabled.
Never uses REDIS_URL or port 6379. No PostgreSQL or production services are accessed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def private_redis(tmp_path_factory):
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server is not installed; real Redis validation unavailable")
    directory = tmp_path_factory.mktemp("federated-private-redis")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    assert port != 6379
    log_path = directory / "redis.log"
    with log_path.open("w+") as output:
        process = subprocess.Popen(
            [executable, "--bind", "127.0.0.1", "--port", str(port), "--save", "", "--appendonly", "no", "--dir", str(directory), "--daemonize", "no"],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(f"Private Redis failed to start: {log_path.read_text()}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("Private Redis startup exceeded 8 seconds")
            yield port, process.pid
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("case", ["create_revoke", "lock_fencing", "rotate_switch", "recovery"])
def test_federated_session_real_redis_lua(private_redis, case):
    port, pid = private_redis
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(backend), "DATABASE_TYPE": "sqlite", "DATABASE_NAME": ":memory:"}
    # Running the test file as a program imports no conftest or pytest plugins.
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--redis-case", case, str(port), str(pid)],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=35,
    )
    assert result.returncode == 0, f"{case} failed:\n{result.stdout}\n{result.stderr}"
    assert f"REAL_REDIS_OK:{case}:" in result.stdout


async def _exercise(case: str, port: int, expected_pid: int):
    from redis.asyncio import Redis

    from app.api.v1.module_system.auth.session_registry import FederatedSessionRecovery as Recovery
    from app.api.v1.module_system.auth.session_registry import RedisLockOwnership
    from app.api.v1.module_system.auth.session_registry import UserSessionRegistry as Registry
    from app.core.exceptions import CustomException

    assert port != 6379
    redis = Redis(host="127.0.0.1", port=port, db=0, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
    try:
        info = await redis.info("server")
        # Protect against the unlikely ephemeral-port race before any mutation.
        assert info["process_id"] == expected_pid
        assert type(redis).__module__.startswith("redis.")
        await redis.flushdb()  # Only the process-owned, PID-verified test instance.

        def session(sid, tenant=10):
            return json.dumps({"session_id": sid, "site_id": 1, "tenant_id": tenant, "user_id": 7})

        async def create(sid, tenant=10):
            async with Registry.user_fences(redis, [(1, tenant, 7)]) as fences:
                await Registry.create_fenced(
                    redis,
                    ownership=fences[0],
                    session_id=sid,
                    session_info=session(sid, tenant),
                    access_token=f"access-{sid}",
                    refresh_token=f"refresh-{sid}",
                    site_id=1,
                    tenant_id=tenant,
                    user_id=7,
                    access_expire=30,
                    refresh_expire=60,
                )

        if case == "create_revoke":
            await create("tenant-a")
            await create("tenant-b", 20)
            index = Registry.index_key(1, 10, 7)
            assert await redis.smembers(index) == {"tenant-a"}
            assert 0 < await redis.ttl(index) <= 60
            assert 0 < await redis.ttl(Registry.access_key("tenant-a")) <= 30
            # Damaged legacy index must not revoke another tenant's valid session.
            await redis.sadd(index, "tenant-b", "missing-session")
            assert await Registry.revoke_user(redis, 1, 10, 7) == 1
            assert await redis.smembers(index) == set()
            assert await redis.exists(Registry.session_key("tenant-a"), Registry.access_key("tenant-a"), Registry.refresh_key("tenant-a")) == 0
            assert await redis.get(Registry.access_key("tenant-b")) == "access-tenant-b"
            assert await redis.smembers(Registry.index_key(1, 20, 7)) == {"tenant-b"}
            # Orphan cleanup must preserve index entries while any token survives.
            await redis.sadd(index, "orphan")
            await redis.set(Registry.refresh_key("orphan"), "surviving-token", ex=30)
            keys = [index, Registry.session_key("orphan"), Registry.access_key("orphan"), Registry.refresh_key("orphan")]
            assert await redis.eval(Registry.CLEAN_ORPHAN_INDEX_SCRIPT, 4, *keys, "orphan") == 0
            await redis.delete(Registry.refresh_key("orphan"))
            assert await redis.eval(Registry.CLEAN_ORPHAN_INDEX_SCRIPT, 4, *keys, "orphan") == 1

        elif case == "lock_fencing":
            lock = Registry.lock_key("locked")
            with pytest.raises(CustomException):
                async with Registry.mutation_lock(redis, "locked", expire=10, attempts=1) as owner:
                    await owner.ensure_owned()
                    await redis.set(lock, "new-owner", ex=10)
                    assert await redis.eval(Registry.RENEW_LOCK_SCRIPT, 1, lock, owner.value, "60") == 0
                    await owner.ensure_owned()
            assert await redis.get(lock) == "new-owner"
            assert await redis.eval(Registry.RELEASE_LOCK_SCRIPT, 1, lock, "new-owner") == 1
            fence_key = Registry.user_fence_key(1, 10, 7)
            await redis.set(fence_key, "new-fence", ex=10)
            lost_owner = RedisLockOwnership(redis=redis, key=fence_key, value="old-fence")
            with pytest.raises(CustomException):
                await Registry.create_fenced(
                    redis,
                    ownership=lost_owner,
                    session_id="denied",
                    session_info=session("denied"),
                    access_token="a",
                    refresh_token="r",
                    site_id=1,
                    tenant_id=10,
                    user_id=7,
                    access_expire=30,
                    refresh_expire=60,
                )
            assert lost_owner.lost.is_set()
            assert await redis.exists(Registry.session_key("denied"), Registry.access_key("denied"), Registry.refresh_key("denied")) == 0
            assert await redis.get(fence_key) == "new-fence"

        elif case == "rotate_switch":
            await create("rotating")
            async with Registry.user_fences(redis, [(1, 10, 7), (1, 20, 7)]) as fences:
                async with Registry.mutation_lock(redis, "rotating") as owner:
                    kwargs = {
                        "session_id": "rotating",
                        "site_id": 1,
                        "tenant_id": 10,
                        "user_id": 7,
                        "expected_session": session("rotating"),
                        "expected_refresh_token": "refresh-rotating",
                        "new_access_token": "access-new",
                        "new_refresh_token": "refresh-new",
                        "access_expire": 30,
                        "refresh_expire": 60,
                        "mutation_ownership": owner,
                        "fence_ownerships": fences,
                    }
                    await Registry.rotate_tokens(redis, **kwargs)
                    with pytest.raises(CustomException):
                        await Registry.rotate_tokens(redis, **kwargs)
                    assert await redis.get(Registry.refresh_key("rotating")) == "refresh-new"
                    await Registry.switch_tenant(
                        redis,
                        session_id="rotating",
                        site_id=1,
                        old_tenant_id=10,
                        new_tenant_id=20,
                        user_id=7,
                        expected_session=session("rotating"),
                        expected_access_token="access-new",
                        new_session=session("rotating", 20),
                        new_access_token="access-tenant-b",
                        session_expire=60,
                        access_expire=30,
                        mutation_ownership=owner,
                        fence_ownerships=fences,
                    )
                    assert await redis.smembers(Registry.index_key(1, 10, 7)) == set()
                    assert await redis.smembers(Registry.index_key(1, 20, 7)) == {"rotating"}
                    with pytest.raises(CustomException):
                        await Registry.delete_session(redis, "rotating", expected_access_token="access-new", mutation_ownership=owner)
                    assert await Registry.delete_session(redis, "rotating", expected_access_token="access-tenant-b", mutation_ownership=owner)
                    assert await redis.smembers(Registry.index_key(1, 20, 7)) == set()

        elif case == "recovery":
            await redis.set(Registry.session_key("legacy"), session("legacy"), ex=60)
            lock_value = await Recovery.acquire_lock(redis, expire=10)
            assert lock_value is not None
            assert await Recovery.acquire_lock(redis, expire=10) is None
            assert await Recovery.renew_lock(redis, lock_value, expire=10)
            assert await Recovery._store_scan_progress(redis, cursor=12, scan_epoch="epoch-a", lock_value=lock_value)
            assert await Recovery._store_pending_cursor(redis, cursor=15, lock_value=lock_value)
            await redis.set(Recovery.LOCK_KEY, "successor", ex=10)
            assert not await Recovery._store_scan_progress(redis, cursor=99, scan_epoch="stale", lock_value=lock_value)
            assert not await Recovery._complete_scan(redis, scan_epoch="stale", lock_value=lock_value)
            assert not await Recovery._store_pending_cursor(redis, cursor=99, lock_value=lock_value)
            assert not await Recovery._delete_pending_cursor(redis, lock_value=lock_value)
            await Recovery.release_lock(redis, lock_value)
            assert await redis.get(Recovery.LOCK_KEY) == "successor"
            assert await redis.get(Recovery.SESSION_SCAN_CURSOR_KEY) == "12"
            assert await redis.get(Recovery.PENDING_CURSOR_KEY) == "15"
            assert await redis.get(Recovery.SESSION_SCAN_WATERMARK_KEY) is None
            assert await Recovery._complete_scan(redis, scan_epoch="epoch-good", lock_value="successor")
            assert await Recovery._delete_pending_cursor(redis, lock_value="successor")
            assert await redis.get(Recovery.PENDING_CURSOR_KEY) is None
            assert await redis.get(Recovery.SESSION_SCAN_CURSOR_KEY) is None
            assert await redis.get(Recovery.SESSION_SCAN_WATERMARK_KEY) == "epoch-good"
            assert await Recovery.rebuild_indexes(redis, scan_count=100, max_batches=3, lock_value="successor") == 1
            assert await redis.smembers(Registry.index_key(1, 10, 7)) == {"legacy"}
            assert 0 < await redis.ttl(Registry.index_key(1, 10, 7)) <= 60
            await Recovery.release_lock(redis, "successor")
            assert await redis.get(Recovery.LOCK_KEY) is None
        else:
            raise AssertionError(f"Unknown Redis scenario: {case}")
        print(f"REAL_REDIS_OK:{case}:{info['redis_version']}")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    assert sys.argv[1] == "--redis-case"
    asyncio.run(asyncio.wait_for(_exercise(sys.argv[2], int(sys.argv[3]), int(sys.argv[4])), timeout=25))
