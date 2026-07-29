"""套餐动态请求限流回归测试。"""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from app.core import http_limit as http_limit_module


def _route_dependency_names(app: FastAPI, path: str, method: str = "GET") -> list[str]:
    for route in app.routes:
        if getattr(route, "path", None) != path or method not in (getattr(route, "methods", None) or set()):
            continue
        return [getattr(dep.call, "__name__", dep.call.__class__.__name__) for dep in route.dependant.dependencies]
    return []


def _request(app: FastAPI, tenant_id: int | None) -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/common/health",
            "raw_path": b"/common/health",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "app": app,
        }
    )
    request.state.tenant_id = tenant_id
    return request


def test_static_and_dynamic_routes_use_tenant_package_rate_limiter(test_client: TestClient) -> None:
    assert "TenantPackageRateLimiter" in _route_dependency_names(test_client.app, "/common/health")
    assert "TenantPackageRateLimiter" in _route_dependency_names(test_client.app, "/ai/chat/list")


def test_rate_limiter_uses_defaults_for_public_and_platform_requests(monkeypatch) -> None:
    limiter_class = getattr(http_limit_module, "TenantPackageRateLimiter")
    limiter = limiter_class(default_times=200, seconds=10)

    async def should_not_load(tenant_id: int) -> int:
        raise AssertionError(f"不应查询公共或平台租户套餐: {tenant_id}")

    monkeypatch.setattr(limiter, "_load_tenant_limit", should_not_load)

    assert asyncio.run(limiter.resolve_times(None)) == 200
    assert asyncio.run(limiter.resolve_times(1)) == 200


def test_rate_limiter_delegates_package_limit_per_tenant(monkeypatch) -> None:
    from fastapi_limiter.depends import RateLimiter

    limiter_class = getattr(http_limit_module, "TenantPackageRateLimiter")
    limiter = limiter_class(default_times=200, seconds=10)
    delegated: list[tuple[int, int]] = []

    async def package_limit(tenant_id: int | None) -> int:
        assert tenant_id == 21
        return 37

    async def capture(self, request: Request, response: Response) -> None:
        delegated.append((self.times, self.milliseconds))

    monkeypatch.setattr(limiter, "resolve_times", package_limit)
    monkeypatch.setattr(RateLimiter, "__call__", capture)
    app = FastAPI()

    asyncio.run(limiter(_request(app, 21), Response()))

    assert delegated == [(37, 10_000)]
