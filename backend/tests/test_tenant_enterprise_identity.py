import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint, select

from app.api.v1.module_platform.federated_tenant.model import FederatedTenantModel
from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.credit_code import validate_unified_social_credit_code
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_platform.tenant.schema import (
    TenantCreateSchema,
    TenantOutSchema,
    TenantQueryParam,
    TenantUpdateSchema,
)
from app.api.v1.module_platform.tenant.service import TenantService
from app.common.enums import QueueEnum
from app.core.database import async_db_session

VALID_USCC = "91350100M000100Y43"
UPDATED_USCC = "91350100M000100Y56"
RACE_USCC = "91350100M000101Y4Q"
UPDATE_RACE_USCC = "91350100M000102Y4E"


def _unique(prefix: str) -> str:
    return f"{prefix}{time.time_ns()}"


async def _create_site() -> int:
    suffix = str(time.time_ns())
    async with async_db_session() as db:
        site = SiteModel(code=f"enterprise{suffix}", name=f"企业站点{suffix}", status=0)
        db.add(site)
        await db.commit()
        return site.id


async def _get_tenant_uscc(tenant_id: int) -> str | None:
    async with async_db_session() as db:
        return (
            await db.execute(
                select(TenantModel.unified_social_credit_code).where(TenantModel.id == tenant_id)
            )
        ).scalar_one()


def test_uscc_normalizes_and_validates_checksum() -> None:
    assert validate_unified_social_credit_code(f"  {VALID_USCC.lower()}  ") == VALID_USCC
    assert validate_unified_social_credit_code(None) is None
    assert validate_unified_social_credit_code("   ") is None
    with pytest.raises(ValueError, match="统一社会信用代码"):
        validate_unified_social_credit_code("91350100M000100Y44")


def test_non_string_uscc_is_a_stable_validation_error(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="统一社会信用代码必须是字符串"):
        TenantUpdateSchema(unified_social_credit_code=123)  # type: ignore[arg-type]

    response = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("非法企业标识"),
            "code": _unique("I"),
            "site_id": 1,
            "unified_social_credit_code": 123,
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["msg"] == "统一社会信用代码必须是字符串"


def test_tenant_schemas_normalize_uscc_and_query_uses_exact_match() -> None:
    create = TenantCreateSchema(
        name="企业租户",
        code="EnterpriseTenant",
        site_id=1,
        unified_social_credit_code=f" {VALID_USCC.lower()} ",
    )
    update = TenantUpdateSchema(unified_social_credit_code=f" {UPDATED_USCC.lower()} ")
    output = TenantOutSchema(
        name="企业租户",
        code="EnterpriseTenant",
        site_id=1,
        unified_social_credit_code=VALID_USCC,
    )
    query = TenantQueryParam(unified_social_credit_code=f" {VALID_USCC.lower()} ")

    assert create.unified_social_credit_code == VALID_USCC
    assert update.unified_social_credit_code == UPDATED_USCC
    assert output.unified_social_credit_code == VALID_USCC
    assert query.unified_social_credit_code == (QueueEnum.eq.value, VALID_USCC)


def test_federated_tenant_has_stable_unique_keys() -> None:
    keys = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in FederatedTenantModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("central_tenant_uuid", "issuer", "site_id") in keys
    assert ("issuer", "local_tenant_id", "site_id") in keys
    assert ("provision_request_uuid",) in keys


def test_database_uscc_create_race_returns_stable_conflict_without_sql_details(
    test_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def skip_preflight_check(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(TenantService, "_ensure_uscc_available", skip_preflight_check)

    first = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("并发企业甲"),
            "code": _unique("RA"),
            "site_id": 1,
            "unified_social_credit_code": RACE_USCC,
        },
    )
    assert first.status_code == 200, first.text

    conflict = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("并发企业乙"),
            "code": _unique("RB"),
            "site_id": 1,
            "unified_social_credit_code": RACE_USCC,
        },
    )

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["msg"] == "统一社会信用代码在当前站点已存在"
    assert conflict.json().get("data") is None
    assert "platform_tenant" not in conflict.text
    assert "UNIQUE constraint" not in conflict.text


def test_database_uscc_update_race_returns_stable_conflict_without_sql_details(
    test_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("更新并发企业甲"),
            "code": _unique("UA"),
            "site_id": 1,
            "unified_social_credit_code": UPDATE_RACE_USCC,
        },
    )
    assert first.status_code == 200, first.text

    second = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("更新并发企业乙"),
            "code": _unique("UB"),
            "site_id": 1,
        },
    )
    assert second.status_code == 200, second.text

    async def skip_preflight_check(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(TenantService, "_ensure_uscc_available", skip_preflight_check)
    conflict = test_client.put(
        f"/platform/tenant/update/{second.json()['data']['id']}",
        headers=auth_headers,
        json={"unified_social_credit_code": UPDATE_RACE_USCC},
    )

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["msg"] == "统一社会信用代码在当前站点已存在"
    assert conflict.json().get("data") is None
    assert "platform_tenant" not in conflict.text
    assert "UNIQUE constraint" not in conflict.text


def test_platform_tenant_uscc_is_scoped_to_site_and_remains_self_service_read_only(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = str(time.time_ns())
    code = f"E{suffix}"
    create = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": f"企业租户{suffix}",
            "code": code,
            "site_id": 1,
            "unified_social_credit_code": f" {VALID_USCC.lower()} ",
        },
    )
    assert create.status_code == 200, create.text
    tenant = create.json()["data"]
    assert tenant["unified_social_credit_code"] == VALID_USCC
    assert tenant["initial_admin"]["username"] == f"{code}_admin"

    duplicate = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("同站点企业"),
            "code": _unique("D"),
            "site_id": 1,
            "unified_social_credit_code": VALID_USCC,
        },
    )
    assert duplicate.status_code == 400, duplicate.text
    assert duplicate.json()["msg"] == "统一社会信用代码在当前站点已存在"

    site_id = asyncio.run(_create_site())
    other_site = test_client.post(
        "/platform/tenant/create",
        headers=auth_headers,
        json={
            "name": _unique("跨站点企业"),
            "code": _unique("C"),
            "site_id": site_id,
            "unified_social_credit_code": VALID_USCC,
        },
    )
    assert other_site.status_code == 200, other_site.text

    update = test_client.put(
        f"/platform/tenant/update/{tenant['id']}",
        headers=auth_headers,
        json={"unified_social_credit_code": f" {UPDATED_USCC.lower()} "},
    )
    assert update.status_code == 200, update.text
    assert update.json()["data"]["unified_social_credit_code"] == UPDATED_USCC

    login = test_client.post(
        "/system/auth/login",
        data={
            "username": tenant["initial_admin"]["username"],
            "password": tenant["initial_admin"]["password"],
        },
    )
    assert login.status_code == 200, login.text
    tenant_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    self_update = test_client.put(
        "/platform/tenant/brand/config",
        headers=tenant_headers,
        json=[{"key": "unified_social_credit_code", "value": VALID_USCC}],
    )
    assert self_update.status_code == 200, self_update.text
    assert asyncio.run(_get_tenant_uscc(tenant["id"])) == UPDATED_USCC
