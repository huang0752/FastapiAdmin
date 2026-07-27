import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.dict.model import DictDataModel, DictTypeModel
from app.api.v1.module_system.dict.service import DictDataService
from app.api.v1.module_system.params.model import ParamsModel
from app.api.v1.module_system.params.service import ParamsService
from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.redis_crud import RedisCURD


def _unique(prefix: str) -> str:
    return f"{prefix}_{time.time_ns()}"


async def _create_tenant(prefix: str) -> int:
    suffix = str(time.time_ns())
    async with async_db_session() as db:
        tenant = TenantModel(name=f"{prefix}{suffix}", code=f"T{suffix}", site_id=1)
        db.add(tenant)
        await db.commit()
        return tenant.id


def test_explicit_platform_global_reader_can_read_all_tenants_but_not_write(
    test_client: TestClient,
) -> None:
    async def exercise() -> tuple[int, set[int]]:
        tenant_id = await _create_tenant("全局只读租户")
        async with async_db_session() as db:
            db.add(
                DictTypeModel(
                    tenant_id=tenant_id,
                    dict_name="全局只读字典",
                    dict_type=_unique("global_read"),
                    status=0,
                )
            )
            await db.commit()

        async with async_db_session() as db:
            auth = AuthSchema.for_platform_global_read(db)
            crud = CRUDBase(model=DictTypeModel, auth=auth)
            rows = await crud.get_list()
            with pytest.raises(CustomException, match="租户上下文缺失"):
                await crud.create(
                    {
                        "dict_name": "禁止写入",
                        "dict_type": _unique("global_write"),
                        "status": 0,
                    }
                )
            return tenant_id, {row.tenant_id for row in rows}

    tenant_id, visible_tenants = asyncio.run(exercise())
    assert 1 in visible_tenants
    assert tenant_id in visible_tenants


def test_params_cache_loader_uses_explicit_platform_global_read(
    test_client: TestClient,
) -> None:
    async def exercise() -> tuple[int, set[int]]:
        tenant_id = await _create_tenant("参数缓存租户")
        async with async_db_session() as db:
            db.add(
                ParamsModel(
                    tenant_id=tenant_id,
                    config_name="租户缓存参数",
                    config_key=_unique("tenant_cache_param"),
                    config_value="ok",
                    status=0,
                )
            )
            await db.commit()

        rows = await ParamsService._load_all_configs_from_db()
        return tenant_id, {row.tenant_id for row in rows}

    tenant_id, visible_tenants = asyncio.run(exercise())
    assert 1 in visible_tenants
    assert tenant_id in visible_tenants


def test_dict_cache_loader_reads_each_tenant_with_explicit_global_context(
    test_client: TestClient,
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}

    async def capture_set(self, key: str, value: str, expire=None) -> bool:
        captured[key] = value
        return True

    monkeypatch.setattr(RedisCURD, "set", capture_set)

    async def exercise() -> tuple[str, str]:
        tenant_id = await _create_tenant("字典缓存租户")
        dict_type = _unique("tenant_cache_dict")
        async with async_db_session() as db:
            type_obj = DictTypeModel(
                tenant_id=tenant_id,
                dict_name="租户缓存字典",
                dict_type=dict_type,
                status=0,
            )
            db.add(type_obj)
            await db.flush()
            db.add(
                DictDataModel(
                    tenant_id=tenant_id,
                    dict_type_id=type_obj.id,
                    dict_type=dict_type,
                    dict_label="租户值",
                    dict_value="tenant-value",
                    dict_sort=1,
                    status=0,
                )
            )
            await db.commit()

        redis = test_client.app.state.redis
        redis_key = f"system_dict:{tenant_id}:{dict_type}"
        await redis.delete(redis_key)
        await DictDataService.init_cache(redis)
        return redis_key, dict_type

    redis_key, dict_type = asyncio.run(exercise())
    payload = json.loads(captured[redis_key])
    assert [item["dict_type"] for item in payload] == [dict_type]


def test_tenant_config_cache_marks_database_fallback_as_platform_global_read(
    test_client: TestClient,
    monkeypatch,
) -> None:
    captured: list[tuple[bool, int | None]] = []

    async def fake_get_config(self, tenant_id: int) -> dict:
        captured.append(
            (self.auth.has_platform_global_read, self.auth.tenant_id)
        )
        return {"version": f"tenant-{tenant_id}"}

    async def fake_sync(redis, tenant_id: int, config: dict) -> None:
        return None

    monkeypatch.setattr(TenantService, "get_config", fake_get_config)
    monkeypatch.setattr(TenantService, "_sync_configs_to_redis", fake_sync)

    async def exercise() -> dict:
        tenant_id = await _create_tenant("租户配置缓存")
        redis = test_client.app.state.redis
        await redis.delete(f"tenant_config:{tenant_id}")
        return await TenantService.get_config_cache(redis, tenant_id)

    config = asyncio.run(exercise())
    assert config["version"].startswith("tenant-")
    assert captured == [(True, None)]
