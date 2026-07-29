"""
模块接口测试 —— module_common（公共模块）
每个接口一个测试用例，验证路由存在且返回码正确。
"""

import asyncio
from io import BytesIO

import pytest
from conftest import assert_route
from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.api.v1.module_common.file.service import FileService
from app.config.setting import settings
from app.core.exceptions import CustomException

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestHealth:
    """健康检查接口。"""

    def test_health_check(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/common/health", expected_status=200)

    def test_health_ready(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/common/health/ready", expected_status=200)

    def test_health_live(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/common/health/live", expected_status=200)


class TestFile:
    """文件管理接口。"""

    def test_upload_file(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/common/file/upload")

    def test_upload_tenant_brand_file_uses_tenant_brand_directory(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        response = test_client.post(
            "/common/file/upload?upload_type=tenant_logo",
            headers=auth_headers,
            files={"file": ("logo.png", PNG_1X1, "image/png")},
        )

        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert "/tenant/brand/logo/" in payload["file_path"].replace("\\", "/")
        assert "/tenant/brand/logo/" in payload["file_url"].replace("\\", "/")

    def test_download_file(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/common/file/download",
            json={"file_path": "test.txt", "delete": False},
        )

    def test_private_file_storage_key_is_tenant_scoped_and_not_public(self, tmp_path) -> None:
        upload = UploadFile(filename="evidence.png", file=BytesIO(PNG_1X1))

        result = asyncio.run(
            FileService.save_private_service(
                file=upload,
                tenant_id=21,
                namespace="evidence",
                storage_root=tmp_path,
            )
        )

        assert result.storage_key.startswith("tenant/21/evidence/")
        assert result.file_name.endswith(".png")
        assert result.size_bytes == len(PNG_1X1)
        assert "static" not in result.storage_key
        assert not hasattr(result, "file_url")
        assert (tmp_path / result.storage_key).read_bytes() == PNG_1X1

    def test_private_file_download_and_delete_reject_cross_tenant_keys(self, tmp_path) -> None:
        upload = UploadFile(filename="evidence.png", file=BytesIO(PNG_1X1))
        stored = asyncio.run(
            FileService.save_private_service(
                file=upload,
                tenant_id=21,
                namespace="evidence",
                storage_root=tmp_path,
            )
        )

        with pytest.raises(CustomException, match="无权访问该文件") as download_error:
            FileService.resolve_private_service(
                storage_key=stored.storage_key,
                tenant_id=22,
                storage_root=tmp_path,
            )
        assert download_error.value.status_code == 403

        with pytest.raises(CustomException, match="无权删除该文件") as delete_error:
            asyncio.run(
                FileService.delete_private_service(
                    storage_key=stored.storage_key,
                    tenant_id=22,
                    storage_root=tmp_path,
                )
            )
        assert delete_error.value.status_code == 403
        assert (tmp_path / stored.storage_key).exists()

        deleted_size = asyncio.run(
            FileService.delete_private_service(
                storage_key=stored.storage_key,
                tenant_id=21,
                storage_root=tmp_path,
            )
        )
        assert deleted_size == len(PNG_1X1)
        assert not (tmp_path / stored.storage_key).exists()

    def test_private_file_rejects_normalized_cross_tenant_traversal(self, tmp_path) -> None:
        upload = UploadFile(filename="secret.png", file=BytesIO(PNG_1X1))
        stored = asyncio.run(
            FileService.save_private_service(
                file=upload,
                tenant_id=22,
                namespace="evidence",
                storage_root=tmp_path,
            )
        )
        suffix = stored.storage_key.removeprefix("tenant/22/")
        crafted_key = f"tenant/21/../../tenant/22/{suffix}"

        with pytest.raises(CustomException, match="非法的文件存储键"):
            FileService.resolve_private_service(
                storage_key=crafted_key,
                tenant_id=21,
                storage_root=tmp_path,
            )

    def test_private_file_api_returns_storage_key_and_supports_authenticated_lifecycle(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "PRIVATE_FILE_PATH", tmp_path)

        uploaded = test_client.post(
            "/common/file/private/upload?namespace=evidence",
            headers=auth_headers,
            files={"file": ("evidence.png", PNG_1X1, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        payload = uploaded.json()["data"]
        assert payload["storage_key"].startswith("tenant/1/evidence/")
        assert "file_path" not in payload
        assert "file_url" not in payload

        downloaded = test_client.post(
            "/common/file/private/download",
            headers=auth_headers,
            json={"storage_key": payload["storage_key"]},
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == PNG_1X1

        deleted = test_client.request(
            "DELETE",
            "/common/file/private/delete",
            headers=auth_headers,
            json={"storage_key": payload["storage_key"]},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"]["released_bytes"] == len(PNG_1X1)
