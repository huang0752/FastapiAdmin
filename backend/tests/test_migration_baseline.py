"""PostgreSQL migration-chain release gates."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "fastapiadmin_migration_gate_"


def _database_environment(database_name: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "dev",
            "DATABASE_TYPE": "postgres",
            "DATABASE_HOST": os.getenv("MIGRATION_TEST_DATABASE_HOST", "localhost"),
            "DATABASE_PORT": os.getenv("MIGRATION_TEST_DATABASE_PORT", "5432"),
            "DATABASE_USER": os.getenv("MIGRATION_TEST_DATABASE_USER", os.getenv("USER", "postgres")),
            "DATABASE_PASSWORD": os.getenv("MIGRATION_TEST_DATABASE_PASSWORD", "12345"),
            "DATABASE_NAME": database_name,
        }
    )
    return environment


def _connection_kwargs(database_name: str) -> dict[str, str | int]:
    environment = _database_environment(database_name)
    return {
        "host": environment["DATABASE_HOST"],
        "port": int(environment["DATABASE_PORT"]),
        "user": environment["DATABASE_USER"],
        "password": environment["DATABASE_PASSWORD"],
        "dbname": database_name,
    }


def _run_alembic(database_name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_DIR,
        env=_database_environment(database_name),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_empty_postgresql_database_can_upgrade_and_downgrade() -> None:
    database_name = f"{DATABASE_PREFIX}{uuid4().hex[:12]}"
    assert re.fullmatch(r"fastapiadmin_migration_gate_[a-f0-9]{12}", database_name)

    admin_connection = psycopg.connect(**_connection_kwargs("postgres"), autocommit=True)
    try:
        with admin_connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

        upgrade = _run_alembic(database_name, "upgrade", "head")
        assert upgrade.returncode == 0, f"{upgrade.stdout}\n{upgrade.stderr}"

        with psycopg.connect(**_connection_kwargs(database_name)) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            tables = {row[0] for row in cursor.fetchall()}
        assert {"platform_tenant", "platform_package", "platform_site", "sys_user"} <= tables

        downgrade = _run_alembic(database_name, "downgrade", "base")
        assert downgrade.returncode == 0, f"{downgrade.stdout}\n{downgrade.stderr}"

        with psycopg.connect(**_connection_kwargs(database_name)) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name <> 'alembic_version'
                """
            )
            assert cursor.fetchall() == []
    finally:
        with admin_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (database_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))
        admin_connection.close()
