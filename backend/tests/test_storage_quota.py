"""租户私有文件存储配额回归测试。"""

import asyncio
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.module_common.file.service import FileService
from app.api.v1.module_platform.package.model import PackageModel
from app.api.v1.module_platform.tenant import model as tenant_models
from app.api.v1.module_platform.tenant import service as tenant_service_module
from app.api.v1.module_platform.tenant.model import TenantModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def _exercise_storage_quota() -> tuple[int, int, int]:
    suffix = str(time.time_ns())
    usage_model = getattr(tenant_models, "TenantStorageUsageModel")
    quota_service = getattr(tenant_service_module, "TenantStorageQuotaService")

    async with async_db_session() as db:
        package = PackageModel(
            name=f"存储配额套餐{suffix}",
            code=f"SQ{suffix}",
            site_id=1,
            max_storage_mb=1,
            rate_limit=60,
        )
        db.add(package)
        await db.flush()
        tenant = TenantModel(
            name=f"存储配额租户{suffix}",
            code=f"ST{suffix}",
            site_id=1,
            package_id=package.id,
        )
        db.add(tenant)
        await db.flush()

        auth = AuthSchema(db=db, tenant_id=tenant.id, check_data_scope=False)
        auth.user = SimpleNamespace(is_superuser=False, roles=[])
        service = quota_service(auth)

        await service.reserve(700 * 1024)
        usage = (
            await db.execute(select(usage_model).where(usage_model.tenant_id == tenant.id))
        ).scalar_one()
        reserved_after_first = usage.reserved_bytes

        with pytest.raises(CustomException, match="存储空间已达套餐上限") as error:
            await service.reserve(400 * 1024)
        assert error.value.status_code == 413

        await service.commit_reservation(700 * 1024)
        await service.release_used(200 * 1024)
        await db.flush()
        used_after_release = usage.used_bytes
        reserved_after_commit = usage.reserved_bytes
        await db.rollback()

    return reserved_after_first, used_after_release, reserved_after_commit


def test_storage_quota_reserves_commits_and_releases_atomically(test_client: TestClient) -> None:
    reserved, used, final_reserved = asyncio.run(_exercise_storage_quota())

    assert reserved == 700 * 1024
    assert used == 500 * 1024
    assert final_reserved == 0


def test_storage_quota_migration_has_framework_revision_contract() -> None:
    migration = (
        __import__(
            "app.alembic.versions.20260729_f01_add_tenant_storage_usage",
            fromlist=["revision"],
        )
    )

    assert migration.revision == "20260729_f01"
    assert migration.down_revision == "20260727_01"


def test_private_file_lifecycle_updates_storage_quota(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    async def fake_reserve(self, size_bytes: int) -> None:
        calls.append(("reserve", size_bytes))

    async def fake_commit(self, size_bytes: int) -> None:
        calls.append(("commit", size_bytes))

    async def fake_release_used(self, size_bytes: int) -> None:
        calls.append(("release_used", size_bytes))

    monkeypatch.setattr(tenant_service_module.TenantStorageQuotaService, "reserve", fake_reserve)
    monkeypatch.setattr(tenant_service_module.TenantStorageQuotaService, "commit_reservation", fake_commit)
    monkeypatch.setattr(tenant_service_module.TenantStorageQuotaService, "release_used", fake_release_used)
    auth = AuthSchema.model_construct(db=None, tenant_id=21, site_id=1, check_data_scope=False, user=None)
    upload = UploadFile(filename="evidence.png", file=BytesIO(PNG_1X1))

    stored = asyncio.run(
        FileService.save_private_service(
            file=upload,
            auth=auth,
            namespace="evidence",
            storage_root=tmp_path,
        )
    )
    deleted_size = asyncio.run(
        FileService.delete_private_service(
            storage_key=stored.storage_key,
            auth=auth,
            storage_root=tmp_path,
        )
    )

    assert deleted_size == len(PNG_1X1)
    assert calls == [
        ("reserve", len(PNG_1X1)),
        ("commit", len(PNG_1X1)),
        ("release_used", len(PNG_1X1)),
    ]
