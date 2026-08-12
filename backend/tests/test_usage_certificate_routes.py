import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.module_platform.tenant.model import TenantModel
from app.core.database import async_db_session


async def _certificate_token(tenant_id: int) -> str:
    async with async_db_session() as db:
        return (await db.execute(select(TenantModel.usage_certificate_token).where(TenantModel.id == tenant_id))).scalar_one()


def test_usage_certificate_routes_are_mounted(test_client: TestClient) -> None:
    paths = {route.path for route in test_client.app.routes}
    assert "/platform/tenant/usage-certificate/preview" in paths
    assert "/platform/tenant/usage-certificate/download" in paths
    assert "/platform/usage-certificate/list" in paths
    assert "/platform/usage-certificate/{id}/preview" in paths
    assert "/platform/usage-certificate/{id}/download" in paths
    assert "/platform/public/usage-certificate/{token}" in paths


def test_public_usage_certificate_does_not_require_login(test_client: TestClient) -> None:
    response = test_client.get("/platform/public/usage-certificate/not-a-real-token")
    assert response.status_code == 404
    assert "无法核验此证明" in response.text


def test_platform_admin_can_list_preview_download_and_public_verify(test_client: TestClient, auth_headers: dict[str, str]) -> None:
    listing = test_client.get("/platform/usage-certificate/list", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["items"]

    preview = test_client.get("/platform/usage-certificate/1/preview", headers=auth_headers)
    assert preview.status_code == 200, preview.text
    assert "企业软件使用证明" in preview.json()["data"]["html"]
    assert "testclient" in preview.json()["data"]["html"]

    download = test_client.get("/platform/usage-certificate/1/download", headers=auth_headers)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/pdf"
    assert download.content.startswith(b"%PDF")

    token = asyncio.run(_certificate_token(1))
    public = test_client.get(f"/platform/public/usage-certificate/{token}")
    assert public.status_code == 200, public.text
    assert "request_ip" not in public.json()["data"]
