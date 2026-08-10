from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_system.dept.model import DeptModel
from app.scripts.initialize import assign_seed_primary_keys, sync_seed_primary_key_sequence


def test_assign_seed_primary_keys_is_deterministic_after_sequence_drift() -> None:
    site_rows = assign_seed_primary_keys(SiteModel, [{"code": "default"}])
    dept_rows = assign_seed_primary_keys(
        DeptModel,
        [
            {
                "name": "总部",
                "children": [
                    {"name": "研发部", "children": [{"name": "后端组"}]},
                    {"name": "市场部"},
                ],
            }
        ],
    )

    assert site_rows == [{"id": 1, "code": "default"}]
    assert dept_rows[0]["id"] == 1
    assert dept_rows[0]["children"][0]["id"] == 2
    assert dept_rows[0]["children"][0]["children"][0]["id"] == 3
    assert dept_rows[0]["children"][1]["id"] == 4


@pytest.mark.asyncio
async def test_sync_seed_primary_key_sequence_advances_postgresql_sequence() -> None:
    db = AsyncMock()
    db.bind = SimpleNamespace(
        dialect=SimpleNamespace(
            name="postgresql",
            identifier_preparer=SimpleNamespace(quote=lambda value: f'"{value}"'),
        )
    )

    await sync_seed_primary_key_sequence(db, SiteModel)

    statement, params = db.execute.await_args.args
    assert 'FROM "platform_site"' in str(statement)
    assert params == {"table_name": "platform_site"}
