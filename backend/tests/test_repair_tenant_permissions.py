from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel
from app.core.database import async_db_session
from app.scripts.repair_tenant_permissions import apply_repair, build_repair_plan


def test_tenant_permission_repair_command_exists() -> None:
    script = Path(__file__).parents[1] / "app/scripts/repair_tenant_permissions.py"

    assert script.is_file()


async def _package_pairs() -> set[tuple[int, int]]:
    async with async_db_session() as db:
        rows = await db.execute(
            select(PackageMenuModel.package_id, PackageMenuModel.menu_id)
        )
        return {tuple(row) for row in rows.all()}


@pytest.mark.asyncio
async def test_repair_dry_run_is_read_only(test_client: TestClient) -> None:
    _ = test_client
    before = await _package_pairs()
    async with async_db_session() as db:
        plan = await build_repair_plan(db)
        await db.rollback()
    after = await _package_pairs()

    assert after == before
    assert set(plan) == {
        "menu_scope_updates",
        "package_menu_add",
        "package_menu_remove",
        "tenant_owner_repairs",
        "ownerless_tenants",
        "tenant_count",
    }


@pytest.mark.asyncio
async def test_repair_apply_can_be_rolled_back(test_client: TestClient) -> None:
    _ = test_client
    before = await _package_pairs()
    async with async_db_session() as db:
        plan = await apply_repair(db)
        repaired = {
            tuple(row)
            for row in (
                await db.execute(
                    select(PackageMenuModel.package_id, PackageMenuModel.menu_id)
                )
            ).all()
        }
        await db.rollback()
    after = await _package_pairs()

    removed = {tuple(pair) for pair in plan["package_menu_remove"]}
    added = {tuple(pair) for pair in plan["package_menu_add"]}
    assert repaired == (before - removed) | added
    assert after == before


@pytest.mark.asyncio
async def test_repair_plans_and_applies_known_menu_scope_migration(
    test_client: TestClient,
) -> None:
    _ = test_client
    async with async_db_session() as db:
        await db.execute(
            update(MenuModel)
            .where(MenuModel.permission == "module_platform:workspace:query")
            .values(scope="platform")
        )
        plan = await build_repair_plan(db)
        assert len(plan["menu_scope_updates"]) == 2

        await apply_repair(db)
        scopes = set(
            (
                await db.execute(
                    select(MenuModel.scope).where(
                        MenuModel.permission == "module_platform:workspace:query"
                    )
                )
            ).scalars().all()
        )
        assert scopes == {"tenant"}
        await db.rollback()
