"""One unified source tree, isolated provider/product PostgreSQL and real HTTP/Celery/Redis."""

from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from io import TextIOBase
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import psycopg
import pytest
from psycopg import sql
from test_control_upgrade_paths import isolated_postgres as isolated_postgres

BACKEND_DIR = Path(__file__).resolve().parents[1]
CONTROL_REVISION = TARGET_REVISION = "20260910_fa_control_access"
DATABASE_NAME_RE = re.compile(r"fastapiadmin_(?:control|product|beta)_e2e_[0-9a-f]{12}")


@dataclass(frozen=True)
class DatabaseSnapshot:
    database: str
    revision: str
    tables: frozenset[str]
    seeded_site_count: int


@dataclass
class SpawnedProcess:
    label: str
    process: subprocess.Popen[str]
    log: TextIOBase


@dataclass(frozen=True)
class ShadowSnapshot:
    identity_count: int
    local_user_count: int
    local_user_id: int | None
    auth_source: str | None
    password_login_enabled: bool | None
    name: str | None
    dept_id: int | None
    membership_count: int
    membership_tenant_code: str | None
    role_count: int


@dataclass(frozen=True)
class ProvisionedTenantSnapshot:
    tenant_count: int
    mapping_count: int
    tenant_code: str | None
    tenant_uuid: str | None
    credit_code: str | None
    owner_role: str | None
    owner_role_count: int
    local_password_admin_count: int


def _validated_database_name(database_name: str) -> str:
    if DATABASE_NAME_RE.fullmatch(database_name) is None:
        raise ValueError(f"拒绝操作非 E2E 数据库: {database_name}")
    return database_name


def _run_backend_command(
    database_name: str,
    *,
    backend_dir: Path,
    assembly: str,
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=backend_dir,
        env=_database_environment(database_name, assembly=assembly, backend_dir=backend_dir),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, f"backend command failed for {database_name}: {' '.join(command)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    return completed


def _migrate_and_seed(
    database_name: str,
    *,
    backend_dir: Path,
    assembly: str,
    revision: str,
) -> None:
    _run_backend_command(
        database_name,
        backend_dir=backend_dir,
        assembly=assembly,
        command=[sys.executable, "-m", "alembic", "upgrade", revision],
    )
    _run_backend_command(
        database_name,
        backend_dir=backend_dir,
        assembly=assembly,
        command=[
            sys.executable,
            "-c",
            "import asyncio; from app.scripts.initialize import InitializeData; asyncio.run(InitializeData().init_db())",
        ],
    )


def _drop_database(admin_connection: psycopg.Connection, database_name: str) -> None:
    validated_name = _validated_database_name(database_name)
    with admin_connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (validated_name,),
        )
        cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(validated_name)))


def _snapshot(database_name: str) -> DatabaseSnapshot:
    with psycopg.connect(**_connection_kwargs(database_name)) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        connected_database = cursor.fetchone()[0]
        cursor.execute("SELECT version_num FROM alembic_version")
        revision = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        tables = frozenset(row[0] for row in cursor.fetchall())
        cursor.execute("SELECT COUNT(*) FROM platform_site")
        seeded_site_count = cursor.fetchone()[0]
    return DatabaseSnapshot(
        database=connected_database,
        revision=revision,
        tables=tables,
        seeded_site_count=seeded_site_count,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _process_log_tail(runtime: SpawnedProcess, limit: int = 4000) -> str:
    runtime.log.flush()
    runtime.log.seek(0)
    return runtime.log.read()[-limit:]


@contextmanager
def _spawned_process(
    label: str,
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> Iterator[SpawnedProcess]:
    log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    runtime = SpawnedProcess(label=label, process=process, log=log)
    try:
        yield runtime
    except BaseException:
        print(f"{label} failure log:\n{_process_log_tail(runtime, 16000)}", file=sys.stderr)
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()


def _wait_for_redis(runtime: SpawnedProcess, port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    last_error = "Redis 尚未响应"
    while time.monotonic() < deadline:
        if runtime.process.poll() is not None:
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
                connection.sendall(b"*1\r\n$4\r\nPING\r\n")
                if connection.recv(64).startswith(b"+PONG"):
                    return
        except OSError as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise AssertionError(f"Redis 未在限定时间内就绪: {last_error}\n{_process_log_tail(runtime)}")


def _wait_for_http(runtime: SpawnedProcess, base_url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    last_error = "HTTP 尚未响应"
    while time.monotonic() < deadline:
        if runtime.process.poll() is not None:
            break
        try:
            response = httpx.get(f"{base_url}/common/health", timeout=1)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise AssertionError(f"{runtime.label} 未在限定时间内就绪: {last_error}\n{_process_log_tail(runtime)}")


@contextmanager
def _running_redis(port: int) -> Iterator[SpawnedProcess]:
    with tempfile.TemporaryDirectory(prefix="fastapiadmin-control-sso-redis-") as redis_dir:
        command = [
            "redis-server",
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            redis_dir,
            "--loglevel",
            "warning",
        ]
        with _spawned_process("Redis", command, cwd=BACKEND_DIR) as runtime:
            _wait_for_redis(runtime, port)
            yield runtime


def _backend_runtime_environment(
    database_name: str,
    *,
    backend_dir: Path,
    assembly: str,
    redis_port: int,
    redis_database: int,
    extra_settings: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = _database_environment(database_name, assembly=assembly, backend_dir=backend_dir)
    environment.update(
        {
            "ROOT_PATH": "",
            "SERVER_HOST": "127.0.0.1",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "REDIS_DB_NAME": str(redis_database),
            "REDIS_ENABLE": "true",
            "REDIS_PASSWORD": "",
            "CAPTCHA_ENABLE": "false",
            "POOL_SIZE": "10",
            "MAX_OVERFLOW": "10",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "REQUEST_LIMITER_REDIS_PREFIX": f"fastapiadmin:e2e:{database_name}:",
        }
    )
    if extra_settings:
        environment.update(extra_settings)
    return environment


@contextmanager
def _running_backend(
    label: str,
    database_name: str,
    *,
    backend_dir: Path,
    assembly: str,
    port: int,
    redis_port: int,
    redis_database: int,
    extra_settings: dict[str, str] | None = None,
) -> Iterator[str]:
    environment = _backend_runtime_environment(
        database_name,
        backend_dir=backend_dir,
        assembly=assembly,
        redis_port=redis_port,
        redis_database=redis_database,
        extra_settings=extra_settings,
    )
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    with _spawned_process(label, command, cwd=backend_dir, environment=environment) as runtime:
        base_url = f"http://127.0.0.1:{port}"
        _wait_for_http(runtime, base_url)
        yield base_url


@contextmanager
def _running_control_worker(
    database_name: str,
    *,
    redis_port: int,
    queue_name: str,
    key_prefix: str,
) -> Iterator[SpawnedProcess]:
    environment = _backend_runtime_environment(
        database_name,
        backend_dir=BACKEND_DIR,
        assembly="control",
        redis_port=redis_port,
        redis_database=0,
        extra_settings={
            "CELERY_ENABLED": "true",
            "CELERY_BROKER_URL": f"redis://127.0.0.1:{redis_port}/4",
            "CELERY_DEFAULT_QUEUE": queue_name,
            "CELERY_BROKER_KEY_PREFIX": key_prefix,
            "CELERY_WORKER_CONCURRENCY": "1",
        },
    )
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.plugin.module_task.runtime.worker:celery_app",
        "worker",
        "--loglevel=INFO",
        "--pool=solo",
        "--concurrency=1",
        f"--queues={queue_name}",
    ]
    with _spawned_process("Control Celery Worker", command, cwd=BACKEND_DIR, environment=environment) as runtime:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if runtime.process.poll() is not None:
                break
            log_tail = _process_log_tail(runtime)
            normalized_log = log_tail.lower()
            if "ready" in normalized_log or "mingle: all alone" in normalized_log:
                yield runtime
                return
            time.sleep(0.2)
        raise AssertionError(f"Control Worker 未在限定时间内就绪\n{_process_log_tail(runtime)}")


def _wait_for_provisions(
    control: httpx.Client,
    headers: dict[str, str],
    *,
    tenant_id: int,
    expected_status: str,
    expected_by_application: dict[int, str] | None = None,
    timeout: float = 40,
) -> list[dict]:
    deadline = time.monotonic() + timeout
    latest: list[dict] = []
    while time.monotonic() < deadline:
        data = _response_data(
            control.get(
                "/control/tenant-provisions",
                headers=headers,
                params={"tenant_id": tenant_id, "page_no": 1, "page_size": 20},
            ),
            "查询租户开通进度",
        )
        assert isinstance(data, dict)
        latest = data["items"]
        if expected_by_application is not None:
            if {item["application_id"]: item["status"] for item in latest} == expected_by_application:
                return latest
        elif len(latest) == 1 and all(item["status"] == expected_status for item in latest):
            return latest
        else:
            assert not any(item["status"] == "failed" for item in latest), f"开户永久失败: {latest}"
        time.sleep(0.25)
    raise AssertionError(f"租户开通状态未在限定时间内变为 {expected_status}: {latest}")


def _wait_for_user_grants(
    control: httpx.Client,
    headers: dict[str, str],
    *,
    opening_ids: list[int],
    user_id: int,
    expected_status: str = "succeeded",
    timeout: float = 30,
) -> dict[int, dict]:
    deadline = time.monotonic() + timeout
    latest: dict[int, dict] = {}
    while time.monotonic() < deadline:
        for opening_id in opening_ids:
            members = _response_data(
                control.get(
                    f"/control/tenant-applications/{opening_id}/grants",
                    headers=headers,
                ),
                "查询用户产品授权同步状态",
            )
            assert isinstance(members, list)
            latest[opening_id] = next(item for item in members if item["user_id"] == user_id)
        if len(latest) == len(opening_ids) and all(item["sync_status"] == expected_status for item in latest.values()):
            return latest
        if expected_status != "failed":
            assert not any(item["sync_status"] == "failed" for item in latest.values()), f"授权永久失败: {latest}"
        time.sleep(0.25)
    raise AssertionError(f"用户产品授权未在限定时间内变为 {expected_status}: {latest}")


def _target_entitlement_snapshot(
    database_name: str,
    *,
    issuer: str,
    central_user_uuid: str,
) -> tuple[str, int, str | None, bool]:
    with psycopg.connect(**_connection_kwargs(database_name)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, applied_version, last_event_id, session_cleanup_pending
            FROM sys_federated_access_entitlement
            WHERE issuer = %s AND central_user_uuid = %s
            """,
            (issuer, central_user_uuid),
        )
        row = cursor.fetchone()
    assert row is not None
    return row


def _provisioned_tenant_snapshot(
    database_name: str,
    *,
    issuer: str,
    central_tenant_uuid: str,
    central_user_uuid: str,
    target_tenant_code: str,
) -> ProvisionedTenantSnapshot:
    with psycopg.connect(**_connection_kwargs(database_name)) as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            """
            SELECT COUNT(*), MIN(t.code), MIN(t.uuid::text), MIN(t.unified_social_credit_code)
            FROM platform_tenant t
            JOIN platform_federated_tenant ft ON ft.local_tenant_id = t.id
            WHERE ft.issuer = %s AND ft.central_tenant_uuid = %s
            """,
            (issuer, central_tenant_uuid),
        )
        tenant_count, tenant_code, tenant_uuid, credit_code = cursor.fetchone()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM platform_federated_tenant
            WHERE issuer = %s AND central_tenant_uuid = %s
            """,
            (issuer, central_tenant_uuid),
        )
        mapping_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT MIN(ut.role), COUNT(ur.role_id)
            FROM sys_federated_identity fi
            JOIN platform_user_tenant ut ON ut.user_id = fi.local_user_id
            JOIN platform_tenant t ON t.id = ut.tenant_id
            LEFT JOIN sys_user_roles ur ON ur.user_id = fi.local_user_id
            WHERE fi.issuer = %s AND fi.central_user_uuid = %s AND t.code = %s
            """,
            (issuer, central_user_uuid, target_tenant_code),
        )
        owner_role, owner_role_count = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM sys_user WHERE username = %s", (f"{target_tenant_code}_admin",))
        local_password_admin_count = cursor.fetchone()[0]
    return ProvisionedTenantSnapshot(
        tenant_count=tenant_count,
        mapping_count=mapping_count,
        tenant_code=tenant_code,
        tenant_uuid=tenant_uuid,
        credit_code=credit_code,
        owner_role=owner_role,
        owner_role_count=owner_role_count,
        local_password_admin_count=local_password_admin_count,
    )


def _response_data(response: httpx.Response, action: str) -> dict | list | None:
    assert response.status_code == 200, f"{action}失败: HTTP {response.status_code}: {response.text}"
    payload = response.json()
    assert payload.get("success") is True, f"{action}未返回成功状态"
    return payload.get("data")


def _login(client: httpx.Client, username: str, password: str) -> dict:
    response = client.post(
        "/system/auth/login",
        data={"username": username, "password": password, "login_type": "PC端"},
    )
    data = _response_data(response, f"用户 {username} 登录")
    assert isinstance(data, dict)
    assert data.get("access_token")
    assert data.get("refresh_token")
    return data


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _extract_exact_launch_code(redirect_url: str, callback_url: str) -> str:
    redirect = urlsplit(redirect_url)
    callback = urlsplit(callback_url)
    assert (redirect.scheme, redirect.netloc, redirect.path) == (callback.scheme, callback.netloc, callback.path)

    def extract_appended_code(redirect_query: str, callback_query: str) -> str:
        redirect_items = parse_qsl(redirect_query, keep_blank_values=True)
        callback_items = parse_qsl(callback_query, keep_blank_values=True)
        assert redirect_items[:-1] == callback_items
        assert len(redirect_items) == len(callback_items) + 1
        key, code = redirect_items[-1]
        assert key == "code"
        assert code
        return code

    callback_fragment = urlsplit(callback.fragment)
    if callback_fragment.path.startswith("/"):
        redirect_fragment = urlsplit(redirect.fragment)
        assert redirect.query == callback.query
        assert (
            redirect_fragment.scheme,
            redirect_fragment.netloc,
            redirect_fragment.path,
            redirect_fragment.fragment,
        ) == (
            callback_fragment.scheme,
            callback_fragment.netloc,
            callback_fragment.path,
            callback_fragment.fragment,
        )
        return extract_appended_code(redirect_fragment.query, callback_fragment.query)

    assert redirect.fragment == callback.fragment
    return extract_appended_code(redirect.query, callback.query)


def test_extract_exact_launch_code_reads_vue_hash_query_only() -> None:
    callback = "http://target.example.test/root?outer=kept#/auth/control/callback?from=portal"
    redirect = f"{callback}&code=launch-token"

    assert _extract_exact_launch_code(redirect, callback) == "launch-token"
    with pytest.raises(AssertionError):
        _extract_exact_launch_code(
            "http://target.example.test/root?outer=kept&code=wrong-layer#/auth/control/callback?from=portal",
            callback,
        )


def test_extract_exact_launch_code_reads_non_hash_outer_query_only() -> None:
    callback = "http://target.example.test/callback?from=portal#section"
    redirect = "http://target.example.test/callback?from=portal&code=launch-token#section"

    assert _extract_exact_launch_code(redirect, callback) == "launch-token"
    with pytest.raises(AssertionError):
        _extract_exact_launch_code(
            "http://target.example.test/callback?from=portal#section?code=wrong-layer",
            callback,
        )


def _shadow_snapshot(database_name: str, *, issuer: str, central_user_uuid: str) -> ShadowSnapshot:
    with psycopg.connect(**_connection_kwargs(database_name)) as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT local_user_id), MIN(local_user_id)
            FROM sys_federated_identity
            WHERE issuer = %s AND central_user_uuid = %s
            """,
            (issuer, central_user_uuid),
        )
        identity_count, local_user_count, local_user_id = cursor.fetchone()
        if local_user_id is None:
            return ShadowSnapshot(
                identity_count=identity_count,
                local_user_count=local_user_count,
                local_user_id=None,
                auth_source=None,
                password_login_enabled=None,
                name=None,
                dept_id=None,
                membership_count=0,
                membership_tenant_code=None,
                role_count=0,
            )
        cursor.execute(
            "SELECT auth_source, password_login_enabled, name, dept_id FROM sys_user WHERE id = %s",
            (local_user_id,),
        )
        auth_source, password_login_enabled, name, dept_id = cursor.fetchone()
        cursor.execute(
            """
            SELECT COUNT(*), MIN(t.code)
            FROM platform_user_tenant ut
            JOIN platform_tenant t ON t.id = ut.tenant_id
            WHERE ut.user_id = %s
            """,
            (local_user_id,),
        )
        membership_count, membership_tenant_code = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM sys_user_roles WHERE user_id = %s", (local_user_id,))
        role_count = cursor.fetchone()[0]
    return ShadowSnapshot(
        identity_count=identity_count,
        local_user_count=local_user_count,
        local_user_id=local_user_id,
        auth_source=auth_source,
        password_login_enabled=password_login_enabled,
        name=name,
        dept_id=dept_id,
        membership_count=membership_count,
        membership_tenant_code=membership_tenant_code,
        role_count=role_count,
    )


@dataclass(frozen=True)
class IsolatedDatabases:
    control: str
    product: str
    beta: str


def _database_environment(database_name: str, *, assembly: str, backend_dir: Path):
    # All connection coordinates must be supplied by our disposable-cluster fixture.
    environment = {
        **os.environ,
        "ENVIRONMENT": "dev",
        "DATABASE_TYPE": "postgres",
        "DATABASE_NAME": database_name,
        "DATABASE_HOST": os.environ["MIGRATION_TEST_DATABASE_HOST"],
        "DATABASE_PORT": os.environ["MIGRATION_TEST_DATABASE_PORT"],
        "DATABASE_USER": os.environ["MIGRATION_TEST_DATABASE_USER"],
        "DATABASE_PASSWORD": "test",
        "APP_ASSEMBLY": assembly,
        "APP_ASSEMBLY_FILE": str(backend_dir / "app/assemblies" / f"{assembly}.toml"),
        "CELERY_ENABLED": "true" if assembly == "control" else "false",
        "CONTROL_SSO_ENABLED": "false",
        "CONTROL_USER_ACCESS_SYNC_ENABLED": "false",
        "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED": "false",
        "CONTROL_TENANT_PROVISIONING_ENABLED": "false",
    }
    if assembly in {"federated-saas", "federated-beta"}:
        environment["APP_ASSEMBLY_FILE"] = os.environ["UNIFIED_E2E_PRODUCT_ASSEMBLY" if assembly == "federated-saas" else "UNIFIED_E2E_BETA_ASSEMBLY"]
    return environment


def _connection_kwargs(database_name):
    return {
        "host": os.environ["MIGRATION_TEST_DATABASE_HOST"],
        "port": int(os.environ["MIGRATION_TEST_DATABASE_PORT"]),
        "user": os.environ["MIGRATION_TEST_DATABASE_USER"],
        "password": "test",
        "dbname": database_name,
    }


@pytest.fixture
def isolated_control_and_target_databases(request, monkeypatch, tmp_path):
    cluster = request.getfixturevalue("isolated_postgres")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    for key in ("host", "port", "user"):
        monkeypatch.setenv(f"MIGRATION_TEST_DATABASE_{key.upper()}", str(cluster[key]))
    assembly = (
        (BACKEND_DIR / "app/assemblies/federated-saas.toml")
        .read_text()
        .replace('mode = "manual"', 'mode = "declared"\ncode = "USER"\nname = "示例产品用户"\npermission_codes = ["module_task:integration:query"]\ndata_scope = 1')
    )
    assembly_path = tmp_path / "example-product.toml"
    assembly_path.write_text(assembly)
    monkeypatch.setenv("UNIFIED_E2E_PRODUCT_ASSEMBLY", str(assembly_path))
    beta_assembly_path = tmp_path / "example-beta.toml"
    beta_assembly_path.write_text(assembly.replace("example-product", "example-beta").replace("module_task:integration:query", "module_task:integration_beta:query"))
    monkeypatch.setenv("UNIFIED_E2E_BETA_ASSEMBLY", str(beta_assembly_path))
    run_id = secrets.token_hex(6)
    databases = IsolatedDatabases(control=f"fastapiadmin_control_e2e_{run_id}", product=f"fastapiadmin_product_e2e_{run_id}", beta=f"fastapiadmin_beta_e2e_{run_id}")
    with psycopg.connect(**_connection_kwargs("postgres"), autocommit=True) as admin:
        try:
            for name in (databases.control, databases.product, databases.beta):
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_validated_database_name(name))))
            _migrate_and_seed(databases.control, backend_dir=BACKEND_DIR, assembly="control", revision=CONTROL_REVISION)
            _migrate_and_seed(databases.product, backend_dir=BACKEND_DIR, assembly="federated-saas", revision=TARGET_REVISION)
            _migrate_and_seed(databases.beta, backend_dir=BACKEND_DIR, assembly="federated-beta", revision=TARGET_REVISION)
            # Product capabilities live only in disposable fixtures.
            for database, assembly_name, permission in (
                (databases.product, "federated-saas", "module_task:integration:query"),
                (databases.beta, "federated-beta", "module_task:integration_beta:query"),
            ):
                _run_backend_command(
                    database,
                    backend_dir=BACKEND_DIR,
                    assembly=assembly_name,
                    command=[
                        sys.executable,
                        "-c",
                        """
import asyncio
from sqlalchemy import insert, select
from app.core.database import async_db_session
from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageModel, PackageMenuModel
async def run():
    async with async_db_session() as db:
        menu_id = (await db.execute(insert(MenuModel.__table__).values(name="Example report", type=2, permission="PERMISSION_PLACEHOLDER", route_name="ExampleReport", route_path="/task/integration-report", component_path="module_task/workflow/definition/index", scope="tenant").returning(MenuModel.id))).scalar_one()
        packages = (await db.execute(select(PackageModel.id).where(PackageModel.code == "standard"))).scalars().all()
        for package_id in packages:
            await db.execute(insert(PackageMenuModel.__table__).values(package_id=package_id, menu_id=menu_id))
        await db.commit()
asyncio.run(run())
    """.replace("PERMISSION_PLACEHOLDER", permission),
                    ],
                )
            yield databases
        finally:
            _drop_database(admin, databases.control)
            _drop_database(admin, databases.product)
            _drop_database(admin, databases.beta)


def test_control_and_target_databases_are_migrated_seeded_and_isolated(isolated_control_and_target_databases):
    databases = isolated_control_and_target_databases
    snapshots = [_snapshot(name) for name in (databases.control, databases.product, databases.beta)]
    assert len({item.database for item in snapshots}) == 3
    assert {item.revision for item in snapshots} == {CONTROL_REVISION}
    assert all(item.seeded_site_count > 0 for item in snapshots)
    assert all(item.tables == snapshots[0].tables for item in snapshots), "Shared core schema, separate deployment data"
    for name in (databases.product, databases.beta):
        with psycopg.connect(**_connection_kwargs(name)) as db:
            assert db.execute("SELECT COUNT(*) FROM control_application").fetchone()[0] == 0


def test_control_product_auto_provisioning_uses_real_http_worker_and_redis(isolated_control_and_target_databases):
    databases = isolated_control_and_target_databases
    redis_port, control_port, product_port, beta_port = (_free_port() for _ in range(4))
    queue_name = f"unified-e2e-{secrets.token_hex(4)}"
    key_prefix = f"unified:e2e:{secrets.token_hex(4)}:"
    celery_settings = {
        "CELERY_ENABLED": "true",
        "CELERY_BROKER_URL": f"redis://127.0.0.1:{redis_port}/4",
        "CELERY_DEFAULT_QUEUE": queue_name,
        "CELERY_BROKER_KEY_PREFIX": key_prefix,
        "CELERY_WORKER_CONCURRENCY": "1",
    }
    tenant_code = f"e2e{secrets.token_hex(4)}"
    with (
        _running_redis(redis_port),
        _running_backend(
            "Control API", databases.control, backend_dir=BACKEND_DIR, assembly="control", port=control_port, redis_port=redis_port, redis_database=0, extra_settings=celery_settings
        ) as control_url,
    ):
        with httpx.Client(base_url=control_url, timeout=20) as control:
            platform_headers = _auth_headers(_login(control, "super", "123456")["access_token"])
            packages = _response_data(control.get("/platform/package/list", headers=platform_headers, params={"site_id": 1, "page_no": 1, "page_size": 100}), "读取套餐")
            central_package_id = next(item["id"] for item in packages["items"] if item["code"] == "basic")
            applications = []
            for application_code, port in (("example-product", product_port), ("example-beta", beta_port)):
                product_url = f"http://127.0.0.1:{port}"
                callback = product_url + "/#/auth/control/callback"
                application = _response_data(
                    control.post(
                        "/control/applications",
                        headers=platform_headers,
                        json={
                            "code": application_code,
                            "name": "Example product",
                            "base_url": product_url,
                            "callback_url": callback,
                            "provisioning_url": product_url + "/system/auth/control/tenant/provision",
                            "provisioning_enabled": True,
                            "provisioning_timeout_seconds": 5,
                            "entitlement_sync_url": product_url + "/system/auth/control/access/sync",
                            "entitlement_sync_enabled": True,
                            "entitlement_sync_timeout_seconds": 5,
                            "status": 0,
                            "sort": 1,
                        },
                    ),
                    "登记示例产品",
                )
                assert application["entitlement_sync_enabled"] is True
                assert application["entitlement_sync_url"] == product_url + "/system/auth/control/access/sync"
                package = _response_data(
                    control.post(
                        "/control/application-packages",
                        headers=platform_headers,
                        json={"application_id": application["id"], "code": "standard", "name": "Example package", "target_package_code": "standard", "is_default": True, "status": 0, "sort": 1},
                    ),
                    "登记产品套餐",
                )
                applications.append((application, package, product_url, callback))
            application, package, product_url, callback = applications[0]
            beta_application, beta_package, beta_url, beta_callback = applications[1]
            target_settings = {
                "CONTROL_SSO_ENABLED": "true",
                "CONTROL_SSO_ISSUER": control_url,
                "CONTROL_SSO_CLIENT_ID": application["client_id"],
                "CONTROL_SSO_CLIENT_SECRET": application["client_secret"],
                "CONTROL_SSO_TIMEOUT_SECONDS": "5",
                "CONTROL_TENANT_PROVISIONING_ENABLED": "true",
                "CONTROL_USER_ACCESS_SYNC_ENABLED": "true",
                "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED": "true",
            }
            with (
                _running_backend(
                    "Example Product API",
                    databases.product,
                    backend_dir=BACKEND_DIR,
                    assembly="federated-saas",
                    port=product_port,
                    redis_port=redis_port,
                    redis_database=1,
                    extra_settings=target_settings,
                ),
                _running_control_worker(databases.control, redis_port=redis_port, queue_name=queue_name, key_prefix=key_prefix),
                httpx.Client(base_url=product_url, timeout=20) as product,
                ExitStack() as late_product,
            ):
                provision = _response_data(
                    control.post(
                        "/control/tenants/provision",
                        headers=platform_headers,
                        json={
                            "tenant": {"name": "Example enterprise", "code": tenant_code, "site_id": 1, "package_id": central_package_id, "unified_social_credit_code": "91350100M000100Y43"},
                            "applications": [{"application_id": app["id"], "application_package_id": pkg["id"], "desired_target_tenant_code": tenant_code} for app, pkg, _, _ in applications],
                        },
                    ),
                    "开户",
                )
                tenant = provision["tenant"]
                initial = _wait_for_provisions(
                    control, platform_headers, tenant_id=tenant["id"], expected_status="mixed", expected_by_application={application["id"]: "succeeded", beta_application["id"]: "failed"}, timeout=70
                )
                beta_failed = next(item for item in initial if item["application_id"] == beta_application["id"])
                alpha_done = next(item for item in initial if item["application_id"] == application["id"])
                assert alpha_done["attempt_count"] == 1
                assert beta_failed["attempt_count"] == beta_failed["max_attempts"] == 2
                assert beta_failed["last_error_code"] == "TARGET_CONNECT_ERROR"
                beta_settings = {**target_settings, "CONTROL_SSO_CLIENT_ID": beta_application["client_id"], "CONTROL_SSO_CLIENT_SECRET": beta_application["client_secret"]}
                late_product.enter_context(
                    _running_backend(
                        "Beta Product API", databases.beta, backend_dir=BACKEND_DIR, assembly="federated-beta", port=beta_port, redis_port=redis_port, redis_database=2, extra_settings=beta_settings
                    )
                )
                beta = late_product.enter_context(httpx.Client(base_url=beta_url, timeout=20))
                _response_data(control.post(f"/control/tenant-provisions/{beta_failed['id']}/retry", headers=platform_headers), "恢复 B 后重试开户")
                recovered = _wait_for_provisions(
                    control,
                    platform_headers,
                    tenant_id=tenant["id"],
                    expected_status="succeeded",
                    expected_by_application={application["id"]: "succeeded", beta_application["id"]: "succeeded"},
                    timeout=45,
                )
                assert next(item for item in recovered if item["application_id"] == application["id"])["attempt_count"] == 1
                admin = tenant["initial_admin"]
                owner_headers = _auth_headers(_login(control, admin["username"], admin["password"])["access_token"])
                owner = _response_data(control.get("/system/user/current/info", headers=owner_headers), "读取企业负责人")
                snapshot = _provisioned_tenant_snapshot(databases.product, issuer=control_url, central_tenant_uuid=tenant["uuid"], central_user_uuid=owner["uuid"], target_tenant_code=tenant_code)
                assert snapshot.tenant_count == snapshot.mapping_count == 1
                assert snapshot.owner_role == "owner" and snapshot.owner_role_count > 0
                assert snapshot.local_password_admin_count == 0
                beta_snapshot = _provisioned_tenant_snapshot(databases.beta, issuer=control_url, central_tenant_uuid=tenant["uuid"], central_user_uuid=owner["uuid"], target_tenant_code=tenant_code)
                assert beta_snapshot.tenant_count == beta_snapshot.mapping_count == 1
                assert beta_snapshot.local_password_admin_count == 0
                assert beta_snapshot.tenant_uuid != snapshot.tenant_uuid, "Each product owns its tenant record"
                owner_launch = _response_data(control.post(f"/control/portal/applications/{application['code']}/launch", headers=owner_headers), "负责人启动产品")
                owner_tokens = _response_data(product.post("/system/auth/control/exchange", json={"code": _extract_exact_launch_code(owner_launch["redirect_url"], callback)}), "负责人兑换SSO")
                assert product.get("/system/user/current/info", headers=_auth_headers(owner_tokens["access_token"])).status_code == 200
                assert product.get("/control/applications", headers=_auth_headers(owner_tokens["access_token"])).status_code == 404
                openings = _response_data(control.get("/control/tenant-applications", headers=platform_headers, params={"page_no": 1, "page_size": 100}), "读取开通记录")
                opening = next(item for item in openings["items"] if item["tenant_id"] == tenant["id"] and item["application_id"] == application["id"])
                beta_opening = next(item for item in openings["items"] if item["tenant_id"] == tenant["id"] and item["application_id"] == beta_application["id"])
                username, password = "example-member", "Member-" + secrets.token_urlsafe(12)
                member = _response_data(
                    control.post(
                        "/control/users",
                        headers=owner_headers,
                        json={"user": {"username": username, "password": password, "name": "Example member", "status": 0}, "tenant_application_ids": [opening["id"], beta_opening["id"]]},
                    ),
                    "创建员工并授权产品",
                )["user"]
                assert [role["code"] for role in member["roles"]] == ["CONTROL_PORTAL_USER"]
                _wait_for_user_grants(control, owner_headers, opening_ids=[opening["id"]], user_id=member["id"])
                _wait_for_user_grants(control, owner_headers, opening_ids=[beta_opening["id"]], user_id=member["id"])
                with psycopg.connect(**_connection_kwargs(databases.control)) as central_db:
                    assert central_db.execute("SELECT COUNT(*) FROM sys_user WHERE username = %s", (username,)).fetchone()[0] == 1
                member_headers = _auth_headers(_login(control, username, password)["access_token"])

                def exchange():
                    launch = _response_data(control.post(f"/control/portal/applications/{application['code']}/launch", headers=member_headers), "启动产品")
                    return _response_data(product.post("/system/auth/control/exchange", json={"code": _extract_exact_launch_code(launch["redirect_url"], callback)}), "兑换SSO票据")

                beta_launch = _response_data(control.post(f"/control/portal/applications/{beta_application['code']}/launch", headers=member_headers), "同一员工启动 B")
                beta_tokens = _response_data(beta.post("/system/auth/control/exchange", json={"code": _extract_exact_launch_code(beta_launch["redirect_url"], beta_callback)}), "B 兑换 SSO")
                beta_headers = _auth_headers(beta_tokens["access_token"])
                assert beta.get("/system/user/current/info", headers=beta_headers).status_code == 200
                beta_shadow = _shadow_snapshot(databases.beta, issuer=control_url, central_user_uuid=member["uuid"])
                assert beta_shadow.identity_count == beta_shadow.local_user_count == beta_shadow.role_count == 1
                tokens = exchange()
                product_headers = _auth_headers(tokens["access_token"])
                current = _response_data(product.get("/system/user/current/info", headers=product_headers), "产品普通用户")
                assert current["menus"]
                shadow = _shadow_snapshot(databases.product, issuer=control_url, central_user_uuid=member["uuid"])
                assert shadow.identity_count == shadow.local_user_count == 1
                assert shadow.role_count == 1 and shadow.password_login_enabled is False
                before = _target_entitlement_snapshot(databases.product, issuer=control_url, central_user_uuid=member["uuid"])
                _response_data(control.delete(f"/control/tenant-applications/{opening['id']}/grants/{member['id']}", headers=owner_headers), "撤权")
                _wait_for_user_grants(control, owner_headers, opening_ids=[opening["id"]], user_id=member["id"])
                after = _target_entitlement_snapshot(databases.product, issuer=control_url, central_user_uuid=member["uuid"])
                assert after[0] == "inactive" and after[1] > before[1] and after[3] is False
                assert product.get("/system/user/current/info", headers=product_headers).status_code in {401, 403}
                assert beta.get("/system/user/current/info", headers=beta_headers).status_code == 200, "Revoking A must not revoke B"
                assert _target_entitlement_snapshot(databases.beta, issuer=control_url, central_user_uuid=member["uuid"])[0] == "active"
                _response_data(control.put(f"/control/tenant-applications/{opening['id']}/grants/{member['id']}", headers=owner_headers), "重新授权")
                _wait_for_user_grants(control, owner_headers, opening_ids=[opening["id"]], user_id=member["id"])
                refreshed = exchange()
                assert product.get("/system/user/current/info", headers=_auth_headers(refreshed["access_token"])).status_code == 200
                assert product.get("/system/user/current/info", headers=product_headers).status_code in {401, 403}, "Regrant must not revive revoked sessions"
                final_shadow = _shadow_snapshot(databases.product, issuer=control_url, central_user_uuid=member["uuid"])
                assert final_shadow.local_user_id == shadow.local_user_id and final_shadow.role_count == 1
