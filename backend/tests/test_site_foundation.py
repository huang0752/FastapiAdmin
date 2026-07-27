
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_normalize_site_host_removes_scheme_port_path_and_case() -> None:
    from app.api.v1.module_platform.site.service import normalize_host

    assert normalize_host(" HTTPS://Brand.Example.COM:8443/login ") == "brand.example.com"
    assert normalize_host("brand.example.com.:443") == "brand.example.com"


def test_site_create_and_public_host_resolution(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    create_response = test_client.post(
        "/platform/site/create",
        headers=auth_headers,
        json={
            "code": "carbon",
            "name": "能碳云",
            "domains": [
                {"host": "Carbon.Example.COM:443", "is_primary": True},
                {"host": "www.carbon.example.com", "is_primary": False},
            ],
            "logo_url": "https://cdn.example.com/carbon/logo.svg",
            "favicon": "https://cdn.example.com/carbon/favicon.ico",
            "login_bg": "https://cdn.example.com/carbon/login.webp",
            "copyright": "能碳云 版权所有",
            "keep_record": "沪ICP备00000000号",
            "help_doc": "https://docs.example.com/carbon",
            "privacy": "https://example.com/carbon/privacy",
            "clause": "https://example.com/carbon/terms",
        },
    )

    assert create_response.status_code == 200, create_response.text
    created = create_response.json()["data"]
    assert created["site_code"] == "carbon"
    assert [item["host"] for item in created["domains"]] == [
        "carbon.example.com",
        "www.carbon.example.com",
    ]

    public_response = test_client.get(
        "/platform/site/public/config",
        headers={"Host": "CARBON.EXAMPLE.COM:9443"},
    )

    assert public_response.status_code == 200, public_response.text
    assert public_response.json()["data"] == {
        "site_code": "carbon",
        "name": "能碳云",
        "logo_url": "https://cdn.example.com/carbon/logo.svg",
        "favicon": "https://cdn.example.com/carbon/favicon.ico",
        "login_bg": "https://cdn.example.com/carbon/login.webp",
        "copyright": "能碳云 版权所有",
        "keep_record": "沪ICP备00000000号",
        "help_doc": "https://docs.example.com/carbon",
        "privacy": "https://example.com/carbon/privacy",
        "clause": "https://example.com/carbon/terms",
        "status": 0,
    }

    unknown_response = test_client.get(
        "/platform/site/public/config",
        headers={"Host": "unknown.example.com"},
    )
    assert unknown_response.status_code == 404

    delete_response = test_client.request(
        "DELETE",
        "/platform/site/delete",
        headers=auth_headers,
        json=[created["id"]],
    )
    assert delete_response.status_code == 200, delete_response.text


def test_legacy_public_tenant_config_is_not_enumerable(test_client: TestClient) -> None:
    response = test_client.get("/platform/tenant/1/config/info")

    assert response.status_code == 404


def test_site_host_is_globally_unique(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    first = test_client.post(
        "/platform/site/create",
        headers=auth_headers,
        json={
            "code": "uniquea",
            "name": "唯一域名甲",
            "domains": [{"host": "unique.example.com", "is_primary": True}],
        },
    )
    assert first.status_code == 200, first.text
    first_id = first.json()["data"]["id"]

    second = test_client.post(
        "/platform/site/create",
        headers=auth_headers,
        json={
            "code": "uniqueb",
            "name": "唯一域名乙",
            "domains": [{"host": "UNIQUE.EXAMPLE.COM:443", "is_primary": True}],
        },
    )
    assert second.status_code == 400, second.text
    assert "域名已被其他站点使用" in second.text

    response = test_client.request(
        "DELETE",
        "/platform/site/delete",
        headers=auth_headers,
        json=[first_id],
    )
    assert response.status_code == 200, response.text


def test_tenant_schema_accepts_site_id() -> None:
    from app.api.v1.module_platform.tenant.schema import TenantCreateSchema

    data = TenantCreateSchema(name="站点租户", code="SITE100", site_id=8)

    assert data.site_id == 8


def test_package_schema_accepts_site_id() -> None:
    from app.api.v1.module_platform.package.schema import PackageCreateSchema

    data = PackageCreateSchema(name="能碳基础版", code="CARBONBASIC", site_id=8)

    assert data.site_id == 8


def test_tenant_and_package_creation_require_explicit_site_id() -> None:
    from app.api.v1.module_platform.package.schema import PackageCreateSchema
    from app.api.v1.module_platform.tenant.schema import TenantCreateSchema

    with pytest.raises(ValidationError):
        TenantCreateSchema(name="缺站点租户", code="NOSITE")
    with pytest.raises(ValidationError):
        PackageCreateSchema(name="缺站点套餐", code="NOSITE")


def test_package_name_and_code_are_unique_within_site(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    site_ids: list[int] = []
    for suffix in ("a", "b"):
        response = test_client.post(
            "/platform/site/create",
            headers=auth_headers,
            json={
                "code": f"package_site_{suffix}",
                "name": f"套餐站点{suffix}",
                "domains": [{"host": f"package-{suffix}.example.com", "is_primary": True}],
            },
        )
        assert response.status_code == 200, response.text
        site_ids.append(response.json()["data"]["id"])

    package_ids: list[int] = []
    for site_id in site_ids:
        response = test_client.post(
            "/platform/package/create",
            headers=auth_headers,
            json={"site_id": site_id, "name": "站点专属版", "code": "SITEEDITION"},
        )
        assert response.status_code == 200, response.text
        package_ids.append(response.json()["data"]["id"])

    duplicate = test_client.post(
        "/platform/package/create",
        headers=auth_headers,
        json={"site_id": site_ids[0], "name": "站点专属版", "code": "SITEEDITION"},
    )
    assert duplicate.status_code == 500
    assert "套餐名称已存在" in duplicate.text

    filtered = test_client.get(
        f"/platform/package/list?site_id={site_ids[1]}&page_no=1&page_size=100",
        headers=auth_headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert {item["id"] for item in filtered.json()["data"]["items"]} == {package_ids[1]}

    blocked = test_client.request(
        "DELETE",
        "/platform/site/delete",
        headers=auth_headers,
        json=[site_ids[0]],
    )
    assert blocked.status_code == 409
    assert "关联套餐" in blocked.text

    assert test_client.request("DELETE", "/platform/package/delete", headers=auth_headers, json=package_ids).status_code == 200
    assert test_client.request("DELETE", "/platform/site/delete", headers=auth_headers, json=site_ids).status_code == 200


def test_tenant_cannot_use_package_from_another_site(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    site_response = test_client.post(
        "/platform/site/create",
        headers=auth_headers,
        json={
            "code": "tenant_boundary",
            "name": "租户边界站点",
            "domains": [{"host": "tenant-boundary.example.com", "is_primary": True}],
        },
    )
    assert site_response.status_code == 200, site_response.text
    site_id = site_response.json()["data"]["id"]

    response = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "site_id": site_id,
            "package_id": 1,
            "name": "跨站点租户",
            "code": "CROSSSITE",
        },
    )
    assert response.status_code == 500
    assert "其他站点的套餐" in response.text

    assert test_client.request("DELETE", "/platform/site/delete", headers=auth_headers, json=[site_id]).status_code == 200


def test_assigned_package_cannot_move_to_another_site(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.module_platform.package.crud import PackageCRUD
    from app.api.v1.module_platform.package.schema import PackageUpdateSchema
    from app.api.v1.module_platform.package.service import PackageService
    from app.core.base_schema import AuthSchema
    from app.core.exceptions import CustomException

    class _CountResult:
        def scalar(self):
            return 1

    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=2, status=0, is_deleted=False)),
        execute=AsyncMock(return_value=_CountResult()),
    )
    auth = AuthSchema.model_construct(
        db=db,
        user=SimpleNamespace(is_superuser=True),
        tenant_id=1,
        site_id=1,
        check_data_scope=False,
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(
        PackageCRUD,
        "get_or_404",
        AsyncMock(return_value=SimpleNamespace(id=7, site_id=1, status=0)),
    )
    monkeypatch.setattr(PackageCRUD, "update", update_mock)

    with pytest.raises(CustomException, match="租户.*站点|站点.*租户"):
        asyncio.run(PackageService(auth).update(7, PackageUpdateSchema(site_id=2)))

    update_mock.assert_not_awaited()


def test_tenant_with_package_cannot_move_to_another_site(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.module_platform.package.model import PackageModel
    from app.api.v1.module_platform.site.model import SiteModel
    from app.api.v1.module_platform.tenant.crud import TenantCRUD
    from app.api.v1.module_platform.tenant.schema import TenantUpdateSchema
    from app.api.v1.module_platform.tenant.service import TenantService
    from app.core.base_schema import AuthSchema
    from app.core.exceptions import CustomException

    async def get_model(model, _id):
        if model is SiteModel:
            return SimpleNamespace(id=2, status=0, is_deleted=False)
        if model is PackageModel:
            return SimpleNamespace(id=9, site_id=1, status=0, is_deleted=False)
        return None

    db = SimpleNamespace(get=AsyncMock(side_effect=get_model))
    auth = AuthSchema.model_construct(
        db=db,
        user=SimpleNamespace(is_superuser=True),
        tenant_id=1,
        site_id=1,
        check_data_scope=False,
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(
        TenantCRUD,
        "get_or_404",
        AsyncMock(return_value=SimpleNamespace(id=8, code="TENANT8", name="租户8", site_id=1, package_id=9)),
    )
    monkeypatch.setattr(TenantCRUD, "update", update_mock)

    with pytest.raises(CustomException, match="套餐.*站点|站点.*套餐"):
        asyncio.run(TenantService(auth).update(8, TenantUpdateSchema(site_id=2)))

    update_mock.assert_not_awaited()
