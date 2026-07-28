import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.v1.module_monitor.online.schema import OnlineOutSchema
from app.api.v1.module_system.auth.service import LoginService
from app.config.setting import settings
from app.core.base_schema import AuthSchema, RefreshTokenPayloadSchema
from app.core.exceptions import CustomException
from app.core.request_context import RequestContext


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"host", host.encode()), (b"user-agent", b"pytest")],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )


def test_online_session_schema_persists_site_id() -> None:
    session = OnlineOutSchema(
        session_id="session-1",
        user_id=10,
        tenant_id=20,
        site_id=30,
        user_name="member",
        name="Member",
    )

    assert session.model_dump()["site_id"] == 30


def test_super_admin_role_marks_nondefault_platform_tenant_as_global() -> None:
    auth = AuthSchema.model_construct(
        user=SimpleNamespace(
            is_superuser=True,
            roles=[SimpleNamespace(code="SUPER_ADMIN")],
        ),
        tenant_id=2,
        check_data_scope=False,
    )

    assert auth.is_platform_global is True


def test_request_site_resolution_rejects_unknown_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.module_platform.site.service import SiteService
    from app.api.v1.module_system.auth.service import resolve_request_site

    async def not_found(_db, raw_host: str):
        assert raw_host == "unknown.example.com"
        return None

    monkeypatch.setattr(SiteService, "resolve_by_host", not_found)

    with pytest.raises(CustomException, match="站点"):
        asyncio.run(resolve_request_site(SimpleNamespace(), _request("unknown.example.com")))


def test_session_site_rejects_tenant_from_another_site() -> None:
    from app.api.v1.module_system.auth.service import validate_session_site

    with pytest.raises(CustomException, match="站点.*不匹配|跨站点"):
        validate_session_site(
            session_site_id=10,
            request_site_id=10,
            tenant_site_id=20,
        )


def test_create_token_persists_selected_tenant_and_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: dict[str, str] = {}

    async def fake_set(self, key: str, value: str, **_kwargs) -> None:
        writes[key] = value

    async def fake_location(_redis, _ip):
        return "本地"

    monkeypatch.setattr("app.api.v1.module_system.auth.service.RedisCURD.set", fake_set)
    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.IpLocalUtil.resolve_location_for_log",
        fake_location,
    )
    user = SimpleNamespace(
        id=10,
        username="member",
        name="Member",
        is_superuser=False,
        last_login=datetime.now(),
        tenant_id=1,
    )

    asyncio.run(
        LoginService.create_token(
            request=_request("brand.example.com"),
            redis=SimpleNamespace(),
            user=user,
            login_type="PC端",
            tenant_id=20,
            site_id=30,
        )
    )

    session_value = next(value for key, value in writes.items() if "user_session" in key)
    session = json.loads(session_value)
    assert session["tenant_id"] == 20
    assert session["site_id"] == 30


def test_superuser_tenant_options_are_scoped_to_site() -> None:
    statements = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        async def execute(self, statement):
            statements.append(statement)
            return _Result()

    auth = AuthSchema.model_construct(
        db=_DB(),
        user=SimpleNamespace(id=1, is_superuser=True),
        tenant_id=1,
        check_data_scope=False,
    )

    assert asyncio.run(LoginService(auth).get_user_tenants(site_id=9)) == []
    assert "platform_tenant.site_id" in str(statements[0])


def test_authenticated_session_rejects_tenant_site_mismatch() -> None:
    from app.core.dependencies import _validate_session_tenant

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        async def execute(self, _statement):
            return _Result(SimpleNamespace(id=20, site_id=2))

    with pytest.raises(CustomException, match="站点.*不匹配|跨站点"):
        asyncio.run(
            _validate_session_tenant(
                _DB(),
                SimpleNamespace(id=10, is_superuser=True),
                20,
                session_site_id=1,
                request_site_id=1,
            )
        )


def test_select_tenant_rejects_target_from_other_request_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(id=20, site_id=2, name="Other Site Tenant")

    class _DB:
        async def execute(self, _statement):
            return _Result()

    async def current_site(_db, _request):
        return SimpleNamespace(id=1)

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        current_site,
    )
    request = _request("brand.example.com")
    request.state.ctx = RequestContext(
        session_id="session-1",
        session_info={"user_id": 10, "tenant_id": 10, "site_id": 1},
    )
    auth = AuthSchema.model_construct(
        db=_DB(),
        user=SimpleNamespace(id=10, username="admin", is_superuser=True),
        tenant_id=10,
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="站点.*不匹配|跨站点"):
        asyncio.run(
            LoginService(auth).select_tenant(
                request=request,
                redis=SimpleNamespace(),
                tenant_id=20,
            )
        )


def test_refresh_rejects_session_tenant_outside_request_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_values = iter(
        [
            "refresh-token",
            json.dumps({"user_id": 10, "tenant_id": 20, "site_id": 1}),
        ]
    )

    async def fake_get(self, _key):
        return next(redis_values)

    async def current_site(_db, _request):
        return SimpleNamespace(id=1)

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self):
            self.values = iter(
                [
                    SimpleNamespace(id=10, status=0, is_superuser=True),
                    SimpleNamespace(id=20, site_id=2),
                ]
            )

        async def execute(self, _statement):
            return _Result(next(self.values))

    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.decode_access_token",
        lambda token: SimpleNamespace(is_refresh=True, sub="session-1"),
    )
    monkeypatch.setattr("app.api.v1.module_system.auth.service.RedisCURD.get", fake_get)
    monkeypatch.setattr(
        "app.api.v1.module_system.auth.service.resolve_request_site",
        current_site,
    )

    with pytest.raises(CustomException, match="站点.*不匹配|跨站点"):
        asyncio.run(
            LoginService.refresh_token(
                request=_request("brand.example.com"),
                db=_DB(),
                redis=SimpleNamespace(),
                refresh_token=RefreshTokenPayloadSchema(refresh_token="refresh-token"),
            )
        )


def test_tenant_registration_rejects_unknown_host(test_client: TestClient) -> None:
    old_enabled = settings.AUTH_LOGIN_REGISTER_ENABLE
    settings.AUTH_LOGIN_REGISTER_ENABLE = True
    try:
        response = test_client.post(
            "/system/auth/tenant/register",
            headers={"Host": "unknown-registration.example.com"},
            json={
                "username": "unknown_site_user",
                "password": "password123",
                "email": "unknown-site@example.com",
                "tenant_name": "未知站点租户",
            },
        )
    finally:
        settings.AUTH_LOGIN_REGISTER_ENABLE = old_enabled

    assert response.status_code == 403
    assert "站点" in response.text
