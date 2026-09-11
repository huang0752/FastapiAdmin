"""Unified migration graph and PostgreSQL upgrade gates in disposable databases."""

from __future__ import annotations

import ast
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic.script import ScriptDirectory
from psycopg import sql

BACKEND = Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND / "app/alembic"
HEAD = "20260910_fa_control_access"


def test_unified_migration_graph_is_unique_connected_and_product_neutral():
    revisions = {}
    for path in (MIGRATIONS / "versions").glob("*.py"):
        tree = ast.parse(path.read_text())
        values = {
            node.target.id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in {"revision", "down_revision"}
        }
        if "revision" not in values:
            continue
        revision = values["revision"]
        assert revision not in revisions, f"duplicate revision {revision}"
        assert len(revision) <= 32
        revisions[revision] = values["down_revision"]
    parents = set()
    for value in revisions.values():
        parents.update(value if isinstance(value, tuple) else [value] if value else [])
    assert parents <= revisions.keys()
    assert revisions.keys() - parents == {HEAD}
    assert "20260831_01" not in revisions, "Food revision IDs must remain downstream"
    assert set(revisions) == {
        "20260726_00",
        "20260727_01",
        "20260729_f01",
        "20260730_01",
        "20260730_02",
        "20260807_01",
        "20260810_01",
        "20260810_02",
        "20260811_01",
        "20260811_02",
        "20260811_03",
        "20260812_01",
        "20260812_02",
        "20260910_fa_access",
        HEAD,
    }, "Unified core must not depend on downstream product migrations"
    assert revisions["20260910_fa_access"] == "20260812_02"
    assert revisions[HEAD] == "20260910_fa_access"
    assert revisions["20260812_02"] == ("20260811_03", "20260812_01")
    graph = ScriptDirectory(str(MIGRATIONS))
    assert {r.revision for r in graph.iterate_revisions(HEAD, "base")} == revisions.keys()


@pytest.fixture(scope="module")
def isolated_postgres(tmp_path_factory):
    """Own a temporary local cluster; never connect to a configured user database."""
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("PostgreSQL initdb and pg_ctl required for isolated migration gate")
    root = tmp_path_factory.mktemp("unified-postgres")
    data = root / "data"
    result = subprocess.run([initdb, "-D", str(data), "-A", "trust", "-U", "migration_gate", "--no-locale", "-E", "UTF8"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    result = subprocess.run(
        [pg_ctl, "-D", str(data), "-l", str(root / "postgres.log"), "-o", f"-h 127.0.0.1 -p {port} -c unix_socket_directories=''", "-w", "start"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    try:
        yield {"host": "127.0.0.1", "port": port, "user": "migration_gate"}
    finally:
        subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"], capture_output=True, text=True, timeout=60, check=True)


@pytest.fixture
def migration_database(isolated_postgres):
    name = f"unified_gate_{uuid4().hex}"
    with psycopg.connect(**isolated_postgres, dbname="postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield {**isolated_postgres, "dbname": name}
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def _migrate(db, command, revision):
    environment = {
        **os.environ,
        "ENVIRONMENT": "dev",
        "DATABASE_TYPE": "postgres",
        "DATABASE_HOST": db["host"],
        "DATABASE_PORT": str(db["port"]),
        "DATABASE_USER": db["user"],
        "DATABASE_PASSWORD": "test",
        "DATABASE_NAME": db["dbname"],
        "APP_ASSEMBLY": "saas-admin",
        "APP_ASSEMBLY_FILE": "app/assemblies/saas-admin.toml",
    }
    return subprocess.run([sys.executable, "-m", "alembic", command, revision], cwd=BACKEND, env=environment, capture_output=True, text=True, timeout=180)


def _upgrade(db, revision="head"):
    result = _migrate(db, "upgrade", revision)
    assert result.returncode == 0, result.stdout + result.stderr


def _insert(cursor, table, **values):
    """Use historical SQL, without importing current ORM into an old schema."""
    values = {"uuid": uuid4().hex, "is_deleted": False, "created_time": "2026-08-01", "updated_time": "2026-08-01", **values}
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, values)), sql.SQL(",").join(sql.Placeholder() for _ in values)
    )
    cursor.execute(statement, list(values.values()))
    return cursor.fetchone()[0]


def _legacy_rows(db, control):
    with psycopg.connect(**db) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM platform_site WHERE code='default'")
        site = cursor.fetchone()[0]
        tenant = _insert(
            cursor,
            "platform_tenant",
            site_id=site,
            name="Upgrade tenant",
            code="upgrade-tenant",
            sort=0,
            status=0,
            usage_certificate_no="TEST-CERT",
            usage_certificate_token="test-cert-token",
            usage_certificate_created_at="2026-08-01",
        )
        user = _insert(cursor, "sys_user", tenant_id=tenant, username="upgrade-user", password="hash-sentinel", name="User", is_superuser=False, status=0)
        role = _insert(cursor, "sys_role", tenant_id=tenant, name="Manual role", code="MANUAL", order=0, status=0, data_scope=1)
        cursor.execute("INSERT INTO sys_user_roles (user_id,role_id) VALUES (%s,%s)", (user, role))
        grants = []
        if control:
            app = _insert(
                cursor,
                "control_application",
                site_id=site,
                code="example",
                name="Example",
                base_url="https://example.invalid",
                callback_url="https://example.invalid/callback",
                client_id="test-client",
                client_secret_hash="hash-only",
                status=0,
                sort=0,
            )
            opening = _insert(cursor, "control_tenant_application", site_id=site, tenant_id=tenant, application_id=app, target_tenant_code="target", status=0, opened_at="2026-08-01")
            for index, (status, deleted) in enumerate(((0, False), (1, False), (0, True))):
                grant_user = user if index == 0 else _insert(cursor, "sys_user", tenant_id=tenant, username=f"user-{index}", password="hash-sentinel", name="User", is_superuser=False, status=0)
                grants.append(
                    _insert(
                        cursor,
                        "control_user_application_grant",
                        site_id=site,
                        tenant_application_id=opening,
                        tenant_id=tenant,
                        user_id=grant_user,
                        status=status,
                        granted_at="2026-08-01",
                        is_deleted=deleted,
                    )
                )
    return tenant, user, role, grants


@pytest.mark.parametrize("legacy_head", [None, "20260812_01", "20260812_02"])
def test_upgrade_preserves_old_users_roles_and_control_grants(migration_database, legacy_head):
    db = migration_database
    existing = None
    if legacy_head:
        _upgrade(db, legacy_head)
        existing = _legacy_rows(db, control=legacy_head == "20260812_02")
    _upgrade(db)
    with psycopg.connect(**db) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        assert cursor.fetchall() == [(HEAD,)]
        for table in ("sys_federated_access_entitlement", "sys_federated_access_event", "control_user_entitlement_ticket"):
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            assert cursor.fetchone()[0] == 0, "Migration must not invent active user entitlements"
        if existing:
            tenant, user, role, grants = existing
            cursor.execute("SELECT username,password FROM sys_user WHERE id=%s", (user,))
            assert cursor.fetchone() == ("upgrade-user", "hash-sentinel")
            cursor.execute("SELECT role_id FROM sys_user_roles WHERE user_id=%s", (user,))
            assert cursor.fetchall() == [(role,)]
            cursor.execute("SELECT code,is_system FROM sys_role WHERE id=%s", (role,))
            assert cursor.fetchone() == ("MANUAL", False)
            cursor.execute("SELECT code FROM platform_tenant WHERE id=%s", (tenant,))
            assert cursor.fetchone() == ("upgrade-tenant",)
            if grants:
                cursor.execute("SELECT desired_state,sync_status,sync_version,is_deleted FROM control_user_application_grant ORDER BY id")
                assert cursor.fetchall() == [("active", "pending", 1, False), ("inactive", "succeeded", 1, False), ("inactive", "succeeded", 1, False)]
    if legacy_head == "20260812_02":
        result = _migrate(db, "downgrade", legacy_head)
        assert result.returncode == 0, result.stdout + result.stderr
        with psycopg.connect(**db) as connection:
            assert connection.execute("SELECT status,is_deleted FROM control_user_application_grant ORDER BY id").fetchall() == [(0, False), (1, False), (0, True)]
        _upgrade(db)


def test_conflicting_preexisting_table_fails_without_stamping_head(migration_database):
    db = migration_database
    _upgrade(db, "20260812_02")
    with psycopg.connect(**db) as connection:
        connection.execute("CREATE TABLE sys_federated_access_entitlement (wrong_definition text)")
        connection.execute("INSERT INTO sys_federated_access_entitlement VALUES ('preserve')")
    result = _migrate(db, "upgrade", "head")
    assert result.returncode != 0
    with psycopg.connect(**db) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [("20260812_02",)]
        assert connection.execute("SELECT wrong_definition FROM sys_federated_access_entitlement").fetchall() == [("preserve",)]


def test_downgrade_keeps_post_upgrade_revocation_and_regrant(migration_database):
    """A rollback must not revive a newly revoked account using old backup state."""
    db = migration_database
    _upgrade(db, "20260812_02")
    _legacy_rows(db, control=True)
    _upgrade(db)
    with psycopg.connect(**db) as connection:
        grants = connection.execute("SELECT id FROM control_user_application_grant ORDER BY id").fetchall()
        connection.execute("UPDATE control_user_application_grant SET desired_state='inactive',sync_version=2,last_event_id='revoke' WHERE id=%s", grants[0])
        connection.execute("UPDATE control_user_application_grant SET desired_state='active',sync_version=2,last_event_id='regrant' WHERE id=%s", grants[2])
    result = _migrate(db, "downgrade", "20260812_02")
    assert result.returncode == 0, result.stdout + result.stderr
    with psycopg.connect(**db) as connection:
        assert connection.execute("SELECT status,is_deleted FROM control_user_application_grant ORDER BY id").fetchall() == [(1, True), (1, False), (0, False)]


def test_access_ledger_constraints_reject_duplicate_and_invalid_events(migration_database):
    db = migration_database
    _upgrade(db, "20260812_02")
    tenant, user, _, _ = _legacy_rows(db, control=False)
    _upgrade(db)
    with psycopg.connect(**db) as connection:
        site = connection.execute("SELECT site_id FROM platform_tenant WHERE id=%s", (tenant,)).fetchone()[0]
        entitlement = connection.execute(
            "INSERT INTO sys_federated_access_entitlement (site_id,tenant_id,local_user_id,issuer,central_user_uuid) VALUES (%s,%s,%s,'https://control.invalid','central-user') RETURNING id",
            (site, tenant, user),
        ).fetchone()[0]
        assert connection.execute("SELECT status,applied_version,session_cleanup_pending FROM sys_federated_access_entitlement WHERE id=%s", (entitlement,)).fetchone() == ("inactive", 0, False)
        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            connection.execute(
                "INSERT INTO sys_federated_access_entitlement (site_id,tenant_id,issuer,central_user_uuid) VALUES (%s,%s,'https://control.invalid','central-user')",
                (site, tenant),
            )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute("UPDATE sys_federated_access_entitlement SET applied_version=-1 WHERE id=%s", (entitlement,))
        connection.execute(
            "INSERT INTO sys_federated_access_event (event_id,entitlement_id,sync_version,desired_state,request_fingerprint,result_json) VALUES ('event-1',%s,1,'active','fingerprint','{}')",
            (entitlement,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            connection.execute(
                "INSERT INTO sys_federated_access_event (event_id,entitlement_id,sync_version,desired_state,request_fingerprint,result_json) VALUES ('event-1',%s,2,'inactive','different','{}')",
                (entitlement,),
            )
