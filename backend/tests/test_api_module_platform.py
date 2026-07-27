"""
模块接口测试 —— module_platform（平台管理）
认证数据测试：admin 登录后验证 CRUD 真实数据。
"""

import asyncio

from conftest import assert_route  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.module_platform.order.model import OrderModel
from app.api.v1.module_platform.tenant.controller import TenantRouter
from app.api.v1.module_platform.tenant.model import TenantModel
from app.core.database import async_db_session
from app.core.dependencies import AuthPermission


def _create_order(test_client: TestClient, auth_headers: dict, *, tenant_id: int = 1, package_id: int = 1) -> int:
    response = test_client.post(
        "/platform/order/create",
        headers=auth_headers,
        json={"tenant_id": tenant_id, "package_id": package_id, "order_type": "new"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def _get_order_status(order_id: int) -> int | None:
    async with async_db_session() as db:
        return (
            await db.execute(select(OrderModel.status).where(OrderModel.id == order_id).limit(1))
        ).scalar_one_or_none()


async def _get_tenant_config_snapshot(tenant_id: int) -> dict:
    async with async_db_session() as db:
        tenant = (
            await db.execute(select(TenantModel).where(TenantModel.id == tenant_id).limit(1))
        ).scalar_one()
        return {
            "name": tenant.name,
            "version": tenant.version,
            "logo_url": tenant.logo_url,
            "login_bg": tenant.login_bg,
        }


def _tenant_auth_permissions(path: str, method: str) -> list[str]:
    for route in TenantRouter.routes:
        if getattr(route, "path", None) != path or method not in getattr(route, "methods", set()):
            continue
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, AuthPermission):
                return dep.call.permissions
    raise AssertionError(f"未找到租户路由权限依赖: {method} {path}")


class TestTenant:
    """租户管理接口。"""

    def test_tenant_routes_accept_platform_permissions_with_legacy_system_aliases(self) -> None:
        expected = {
            ("GET", "/tenant/list"): ["module_platform:tenant:query", "module_system:tenant:query"],
            ("POST", "/tenant/create"): ["module_platform:tenant:create", "module_system:tenant:create"],
            ("PUT", "/tenant/update/{id}"): ["module_platform:tenant:update", "module_system:tenant:update"],
            ("DELETE", "/tenant/delete"): ["module_platform:tenant:delete", "module_system:tenant:delete"],
            ("PATCH", "/tenant/status/batch"): ["module_platform:tenant:patch", "module_system:tenant:patch"],
        }

        for (method, path), permissions in expected.items():
            assert _tenant_auth_permissions(path, method) == permissions

    def test_tenant_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/list", auth=auth_headers)

    def test_tenant_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/detail/1", auth=auth_headers)

    def test_tenant_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/tenant/create", auth=auth_headers,
            json={"name": "测试租户", "code": "test_tenant", "site_id": 1, "contact_name": "张三", "contact_phone": "13800000000"},
        )

    def test_tenant_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/tenant/update/1", auth=auth_headers,
            json={"name": "更新租户"},
        )

    def test_tenant_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/tenant/delete", auth=auth_headers, json=[9999])

    def test_tenant_config_info(self, test_client: TestClient, auth_headers: dict) -> None:
        response = test_client.get("/platform/tenant/1/config/info")
        assert response.status_code == 404

    def test_tenant_config(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client,
            "PUT",
            "/platform/tenant/1/config",
            auth=auth_headers,
            json=[{"key": "description", "value": "平台默认租户"}],
        )

    def test_tenant_config_update_accepts_item_list_and_returns_brand_aliases(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        payload = [
            {"key": "name", "value": "测试租户品牌"},
            {"key": "version", "value": "2.1.0"},
            {"config_key": "logo_url", "config_value": "https://example.test/tenant-2-logo.svg"},
            {"key": "login_bg", "value": "https://example.test/tenant-2-login-bg.svg"},
        ]

        resp = test_client.put("/platform/tenant/2/config", headers=auth_headers, json=payload)

        assert resp.status_code == 200, resp.text
        items = {item["config_key"]: item["config_value"] for item in resp.json()["data"]}
        assert items["name"] == "测试租户品牌"
        assert items["tenant_name"] == "测试租户品牌"
        assert items["version"] == "2.1.0"
        assert items["tenant_version"] == "2.1.0"
        assert items["logo_url"] == "https://example.test/tenant-2-logo.svg"
        assert items["tenant_logo"] == "https://example.test/tenant-2-logo.svg"
        assert items["login_bg"] == "https://example.test/tenant-2-login-bg.svg"

        snapshot = asyncio.run(_get_tenant_config_snapshot(2))
        assert snapshot == {
            "name": "测试租户品牌",
            "version": "2.1.0",
            "logo_url": "https://example.test/tenant-2-logo.svg",
            "login_bg": "https://example.test/tenant-2-login-bg.svg",
        }

    def test_tenant_self_service_brand_updates_current_tenant_only(
        self, test_client: TestClient, auth_headers: dict
    ) -> None:
        before = asyncio.run(_get_tenant_config_snapshot(1))
        payload = [
            {"key": "tenant_name", "value": "自助品牌租户"},
            {"key": "tenant_logo", "value": "https://example.test/self-logo.svg"},
            {"key": "favicon", "value": "https://example.test/self-favicon.ico"},
            {"key": "login_bg", "value": "https://example.test/self-login-bg.svg"},
            {"key": "version", "value": "9.9.9"},
            {"key": "git_code", "value": "https://example.test/should-not-write"},
        ]

        resp = test_client.put("/platform/tenant/brand/config", headers=auth_headers, json=payload)

        assert resp.status_code == 200, resp.text
        items = {item["config_key"]: item["config_value"] for item in resp.json()["data"]}
        assert items["tenant_name"] == "自助品牌租户"
        assert items["tenant_logo"] == "https://example.test/self-logo.svg"
        assert items["favicon"] == "https://example.test/self-favicon.ico"
        assert items["login_bg"] == "https://example.test/self-login-bg.svg"
        assert "version" not in items
        assert "git_code" not in items

        after = asyncio.run(_get_tenant_config_snapshot(1))
        assert after["name"] == "自助品牌租户"
        assert after["logo_url"] == "https://example.test/self-logo.svg"
        assert after["login_bg"] == "https://example.test/self-login-bg.svg"
        assert after["version"] == before["version"]

    def test_tenant_renew(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/tenant/renew/1", auth=auth_headers,
            json={"days": 30},
        )

    def test_tenant_status(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/tenant/status/1", auth=auth_headers,
            json={"status": 1},
        )

    def test_tenant_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/platform/tenant/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )

    def test_tenant_package_change_preview(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/1/package-change-preview", auth=auth_headers)

    def test_tenant_users(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/1/users", auth=auth_headers)

    def test_tenant_add_user(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/tenant/1/users", auth=auth_headers,
            json={"user_id": 2},
        )

    def test_tenant_remove_user(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/tenant/1/users/2", auth=auth_headers)

    def test_tenant_invoice_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/invoice/list", auth=auth_headers)

    def test_tenant_invoice_download(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/invoice/1/download", auth=auth_headers)

    def test_tenant_license_download(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/invoice/1/license/download", auth=auth_headers)


class TestPackage:
    """套餐管理接口。"""

    def test_package_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/package/list", auth=auth_headers)

    def test_package_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/package/detail/1", auth=auth_headers)

    def test_package_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/package/create", auth=auth_headers,
            json={"name": "测试套餐", "code": "test_pkg", "site_id": 1, "price": 99.0, "sort": 1},
        )

    def test_package_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/package/update/1", auth=auth_headers,
            json={"name": "更新套餐"},
        )

    def test_package_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/package/delete", auth=auth_headers, json=[9999])

    def test_package_menus(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/package/menus/1", auth=auth_headers)

    def test_package_set_menus(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/package/menus/1/set", auth=auth_headers,
            json={"menu_ids": [1, 2]},
        )

    def test_package_plugins(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/package/plugins/1", auth=auth_headers)

    def test_package_set_plugins(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/package/plugins/1/set", auth=auth_headers,
            json={"plugin_ids": [1]},
        )

    def test_package_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/platform/package/status/batch", auth=auth_headers,
            # 路由冒烟测试不应停用后续订单用例依赖的基础套餐。
            json={"ids": [9999], "status": 1},
        )


class TestPlugin:
    """插件管理接口。"""

    def test_plugin_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/plugin/list", auth=auth_headers)

    def test_plugin_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/plugin/detail/1", auth=auth_headers)

    def test_plugin_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/plugin/create", auth=auth_headers,
            json={"name": "测试插件", "code": "test_plugin"},
        )

    def test_plugin_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/plugin/update/1", auth=auth_headers,
            json={"name": "更新插件"},
        )

    def test_plugin_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/plugin/delete", auth=auth_headers, json=[9999])

    def test_plugin_marketplace(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/plugin/marketplace", auth=auth_headers)

    def test_plugin_my(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/plugin/my", auth=auth_headers)

    def test_plugin_install(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/plugin/install", auth=auth_headers,
            json={"code": "test_plugin"},
        )

    def test_plugin_uninstall(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/plugin/uninstall", auth=auth_headers,
            json={"code": "test_plugin"},
        )

    def test_plugin_toggle(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/plugin/toggle", auth=auth_headers,
            json={"code": "test_plugin"},
        )

    def test_plugin_reload(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "POST", "/platform/plugin/reload", auth=auth_headers)


class TestMenu:
    """菜单管理接口。"""

    def test_menu_tree(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/menu/tree", auth=auth_headers)

    def test_menu_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/menu/create", auth=auth_headers,
            json={"name": "测试菜单", "type": 0, "parent_id": 0, "sort": 1},
        )

    def test_menu_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/menu/update/1", auth=auth_headers,
            json={"name": "更新菜单"},
        )

    def test_menu_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/menu/delete", auth=auth_headers, json=[9999])

    def test_menu_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/menu/detail/1", auth=auth_headers)

    def test_menu_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/platform/menu/status/batch", auth=auth_headers,
            # 路由冒烟测试不能禁用 ID=1 的根菜单，否则会污染 session 级测试数据库，
            # 使后续租户 owner 初始化找不到任何启用的必备菜单。
            json={"ids": [9999], "status": 1},
        )


class TestEmail:
    """邮件服务接口。"""

    def test_email_config_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/email/config/list", auth=auth_headers)

    def test_email_config_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/email/config/create", auth=auth_headers,
            json={"name": "测试配置", "host": "smtp.test.com", "port": 587, "username": "test", "password": "pwd"},
        )

    def test_email_config_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/email/config/update/1", auth=auth_headers,
            json={"host": "smtp.new.com"},
        )

    def test_email_config_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/email/config/delete", auth=auth_headers, json=[9999])

    def test_email_config_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/email/config/detail/1", auth=auth_headers)

    def test_email_config_test(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/email/config/test", auth=auth_headers,
            json={"host": "smtp.test.com", "port": 587, "username": "t", "password": "p"},
        )

    def test_email_send(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/email/send", auth=auth_headers,
            json={"to": "test@test.com", "subject": "test", "body": "hello"},
        )

    def test_email_template_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/email/template/list", auth=auth_headers)

    def test_email_template_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/email/template/detail/1", auth=auth_headers)

    def test_email_template_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/email/template/create", auth=auth_headers,
            json={"name": "测试模板", "code": "test_tpl", "subject": "标题", "content": "内容"},
        )

    def test_email_template_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/platform/email/template/update/1", auth=auth_headers,
            json={"name": "更新模板"},
        )

    def test_email_template_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/platform/email/template/delete", auth=auth_headers, json=[9999])

    def test_email_log_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/email/log/list", auth=auth_headers)


class TestOrder:
    """订单管理接口。"""

    def test_order_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/order/list", auth=auth_headers)

    def test_order_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        order_id = _create_order(test_client, auth_headers)
        assert_route(test_client, "GET", f"/platform/order/detail/{order_id}", auth=auth_headers)

    def test_order_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/order/create", auth=auth_headers,
            json={"tenant_id": 1, "package_id": 1, "order_type": "new"},
        )

    def test_order_cancel(self, test_client: TestClient, auth_headers: dict) -> None:
        order_id = _create_order(test_client, auth_headers)
        assert_route(test_client, "POST", f"/platform/order/cancel/{order_id}", auth=auth_headers)

    def test_order_refund_apply(self, test_client: TestClient, auth_headers: dict) -> None:
        order_id = _create_order(test_client, auth_headers)
        assert_route(
            test_client, "POST", f"/platform/order/refund/apply/{order_id}", auth=auth_headers,
            json={"reason": "测试退款"},
        )


class TestPayment:
    """支付管理接口。"""

    def test_payment_record_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/payment/record/list", auth=auth_headers)

    def test_payment_pay(self, test_client: TestClient, auth_headers: dict) -> None:
        order_id = _create_order(test_client, auth_headers)
        assert_route(
            test_client, "POST", f"/platform/payment/pay/{order_id}", auth=auth_headers,
            json={"method": "wechat"},
        )

    def test_payment_status(self, test_client: TestClient, auth_headers: dict) -> None:
        order_id = _create_order(test_client, auth_headers)
        assert_route(test_client, "GET", f"/platform/payment/status/{order_id}", auth=auth_headers)

    def test_payment_callback(self, test_client: TestClient) -> None:
        response = test_client.post("/platform/payment/callback/wechat", json={})

        assert response.status_code == 200, response.text
        assert response.json()["code"] == 400

    def test_payment_mock_callback(self, test_client: TestClient, auth_headers: dict) -> None:
        order_id = _create_order(test_client, auth_headers, package_id=2)
        response = test_client.post(
            "/platform/payment/mock/callback",
            headers=auth_headers,
            json=order_id,
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == 1
        assert asyncio.run(_get_order_status(order_id)) == 1


class TestRefund:
    """退款管理接口。"""

    def test_refund_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/refund/list", auth=auth_headers)

    def test_refund_approve(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "PUT", "/platform/refund/approve/1", auth=auth_headers)

    def test_refund_reject(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "PUT", "/platform/refund/reject/1", auth=auth_headers)


class TestInvoice:
    """发票管理接口。"""

    def test_invoice_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/invoice/list", auth=auth_headers)

    def test_invoice_apply(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/tenant/invoice/apply", auth=auth_headers,
            json={"order_id": 1, "invoice_type": 0, "title": "测试发票", "tax_no": "123456"},
        )

    def test_invoice_issue(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "PUT", "/platform/invoice/issue/1", auth=auth_headers, json={"status": 1})

    def test_invoice_void(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "PUT", "/platform/invoice/void/1", auth=auth_headers, json={"status": 2})


class TestSelfService:
    """租户自助服务接口。"""

    def test_package_available(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/package/available", auth=auth_headers)

    def test_package_preview(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/package/preview", auth=auth_headers)

    def test_plugin_purchase(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/tenant/plugin/purchase", auth=auth_headers,
            json={"plugin_code": "test_plugin"},
        )

    def test_self_order_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/order/list", auth=auth_headers)

    def test_self_order_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        order_id = _create_order(test_client, auth_headers)
        assert_route(test_client, "GET", f"/platform/tenant/order/detail/{order_id}", auth=auth_headers)

    def test_self_order_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/platform/tenant/order/create", auth=auth_headers,
            json={"package_id": 1, "order_type": "new"},
        )

    def test_self_workspace(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/platform/tenant/workspace", auth=auth_headers)
