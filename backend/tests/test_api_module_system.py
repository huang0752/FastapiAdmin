"""
模块接口测试 —— module_system（系统管理）
认证数据测试：admin 登录后验证 CRUD 真实数据。
"""

from conftest import assert_route  # noqa: F401
from fastapi.testclient import TestClient

from app.api.v1.module_system.ticket.controller import TicketRouter
from app.config.setting import settings
from app.core.dependencies import AuthPermission


def _ticket_auth_permissions(path: str, method: str) -> list[str]:
    for route in TicketRouter.routes:
        if getattr(route, "path", None) != path or method not in getattr(route, "methods", set()):
            continue
        for dependency in route.dependant.dependencies:
            if isinstance(dependency.call, AuthPermission):
                return dependency.call.permissions
    raise AssertionError(f"未找到工单路由: {method} {path}")


class TestAuth:
    """认证授权接口（无需认证）。"""

    def test_auth_captcha(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/system/auth/captcha/get")

    def test_auth_login(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/system/auth/login",
            data={"username": "admin", "password": "admin123"},
            expected_status=200,
        )

    def test_auth_logout(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client,
            "POST",
            "/system/auth/logout",
            auth=auth_headers,
            json={"token": "mock_token"},
        )

    def test_auth_refresh(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/system/auth/token/refresh",
            json={"refresh_token": "mock_refresh_token"},
        )

    def test_auth_tenants(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/auth/tenants", auth=auth_headers)

    def test_auth_select_tenant(self, test_client: TestClient, auth_headers: dict) -> None:
        login_resp = test_client.post(
            "/system/auth/login",
            data={"username": "admin", "password": "admin123", "login_type": "PC端"},
        )
        assert login_resp.status_code == 200
        access_token = login_resp.json()["data"]["access_token"]
        assert_route(
            test_client,
            "POST",
            "/system/auth/select-tenant",
            auth={"Authorization": f"Bearer {access_token}"},
            json={"tenant_id": 1},
        )

    def test_auth_tenant_register(self, test_client: TestClient) -> None:
        old_register = settings.AUTH_LOGIN_REGISTER_ENABLE
        settings.AUTH_LOGIN_REGISTER_ENABLE = True
        try:
            assert_route(
                test_client,
                "POST",
                "/system/auth/tenant/register",
                json={"name": "测试租户", "username": "admin", "password": "admin123"},
            )
        finally:
            settings.AUTH_LOGIN_REGISTER_ENABLE = old_register

    def test_auth_auto_login_users(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/auth/auto-login/users", auth=auth_headers)

    def test_auth_auto_login_token(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "POST", "/system/auth/auto-login/token?user_id=1", auth=auth_headers)


class TestUser:
    """用户管理接口 — 数据验证。"""

    def test_user_current_info(self, test_client: TestClient, auth_headers: dict) -> None:
        resp = test_client.get("/system/user/current/info", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"] == "admin", f"期望 admin，实际 {data['data'].get('username')}"

    def test_user_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/user/list", auth=auth_headers)

    def test_user_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        resp = test_client.get("/system/user/detail/1", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"] == "super", f"id=1 应为 super，实际 {data['data'].get('username')}"

    def test_user_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/user/create", auth=auth_headers,
            json={"username": "test_user", "password": "test123", "name": "测试", "dept_id": 1},
        )

    def test_user_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/user/update/1", auth=auth_headers,
            json={"name": "更新用户"},
        )

    def test_user_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/user/delete", auth=auth_headers, json=[9999])

    def test_user_import_template(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/user/import/template", auth=auth_headers)

    def test_user_export(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/user/export", auth=auth_headers)

    def test_user_import_data(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/user/import/data", auth=auth_headers,
            json={"list": []},
        )

    def test_user_current_info_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/user/current/info/update", auth=auth_headers,
            json={"name": "更新个人信息"},
        )

    def test_user_password_change(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/user/password/change", auth=auth_headers,
            json={"old_password": "old", "new_password": "new123"},
        )

    def test_user_password_forget(self, test_client: TestClient) -> None:
        old_enabled = settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE
        old_mode = settings.AUTH_PASSWORD_RESET_MODE
        settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = True
        settings.AUTH_PASSWORD_RESET_MODE = "legacy_mobile"
        try:
            assert_route(
                test_client,
                "POST",
                "/system/user/password/forget",
                json={"username": "admin", "mobile": "13800000000", "new_password": "newpass123"},
            )
        finally:
            settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = old_enabled
            settings.AUTH_PASSWORD_RESET_MODE = old_mode

    def test_user_password_reset(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/user/password/reset/1", auth=auth_headers,
            json={"password": "newpass123"},
        )

    def test_user_register(self, test_client: TestClient) -> None:
        assert_route(
            test_client, "POST", "/system/user/register",
            json={"username": "new_user", "password": "pass123", "name": "新用户"},
        )

    def test_user_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/user/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )


class TestRole:
    """角色管理接口 — 数据验证。"""

    def test_role_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/role/list", auth=auth_headers)

    def test_role_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/role/detail/1", auth=auth_headers)

    def test_role_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/role/create", auth=auth_headers,
            json={"name": "测试角色", "code": "test_role", "sort": 1},
        )

    def test_role_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/role/update/1", auth=auth_headers,
            json={"name": "更新角色"},
        )

    def test_role_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/role/delete", auth=auth_headers, json=[9999])

    def test_role_export(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/role/export", auth=auth_headers)

    def test_role_permission(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/role/permission", auth=auth_headers,
            json={"role_id": 1, "menu_ids": [1, 2]},
        )

    def test_role_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/role/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )


class TestDept:
    """部门管理接口 — 数据验证。"""

    def test_dept_tree(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/dept/tree", auth=auth_headers)

    def test_dept_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/dept/detail/1", auth=auth_headers)

    def test_dept_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/dept/create", auth=auth_headers,
            json={"name": "测试部门", "parent_id": 0, "sort": 1},
        )

    def test_dept_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/dept/update/1", auth=auth_headers,
            json={"name": "更新部门"},
        )

    def test_dept_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/dept/delete", auth=auth_headers, json=[9999])

    def test_dept_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/dept/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )


class TestPosition:
    """岗位管理接口 — 数据验证。"""

    def test_position_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/position/list", auth=auth_headers)

    def test_position_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/position/detail/1", auth=auth_headers)

    def test_position_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/position/create", auth=auth_headers,
            json={"name": "测试岗位", "code": "test_pos", "sort": 1},
        )

    def test_position_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/position/update/1", auth=auth_headers,
            json={"name": "更新岗位"},
        )

    def test_position_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/position/delete", auth=auth_headers, json=[9999])

    def test_position_export(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/position/export", auth=auth_headers)

    def test_position_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/position/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )


class TestDict:
    """字典管理接口 — 数据验证。"""

    def test_dict_type_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/dict/type/list", auth=auth_headers)

    def test_dict_type_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/dict/type/detail/1", auth=auth_headers)

    def test_dict_type_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/dict/type/create", auth=auth_headers,
            json={"dict_name": "测试字典", "dict_type": "test_dict", "status": 0},
        )

    def test_dict_type_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/dict/type/update/1", auth=auth_headers,
            json={"dict_name": "更新字典"},
        )

    def test_dict_type_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/dict/type/delete", auth=auth_headers, json=[9999])

    def test_dict_data_by_type(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/dict/data/info/sys_normal_disable", auth=auth_headers)

    def test_dict_data_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/dict/data/list", auth=auth_headers)

    def test_dict_data_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/dict/data/create", auth=auth_headers,
            json={"dict_type": "sys_normal_disable", "dict_label": "测试", "dict_value": "0", "sort": 1},
        )

    def test_dict_data_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/dict/data/update/1", auth=auth_headers,
            json={"dict_label": "更新标签"},
        )

    def test_dict_data_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/dict/data/delete", auth=auth_headers, json=[9999])

    def test_dict_data_export(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "POST", "/system/dict/data/export", auth=auth_headers)

    def test_dict_data_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/dict/data/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )

    def test_dict_type_export(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "POST", "/system/dict/type/export", auth=auth_headers)

    def test_dict_type_optionselect(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/dict/type/optionselect", auth=auth_headers)

    def test_dict_type_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/dict/type/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )


class TestNotice:
    """公告通知接口 — 数据验证。"""

    def test_notice_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/notice/list", auth=auth_headers)

    def test_notice_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/notice/detail/1", auth=auth_headers)

    def test_notice_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/notice/create", auth=auth_headers,
            json={"notice_title": "测试公告", "notice_content": "内容", "status": 0},
        )

    def test_notice_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/notice/update/1", auth=auth_headers,
            json={"notice_title": "更新公告"},
        )

    def test_notice_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/notice/delete", auth=auth_headers, json=[9999])

    def test_notice_unread_count(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/notice/unread-count", auth=auth_headers)

    def test_notice_available(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/notice/available", auth=auth_headers)

    def test_notice_export(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "POST", "/system/notice/export", auth=auth_headers)

    def test_notice_panel(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/notice/panel", auth=auth_headers)

    def test_notice_read(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "POST", "/system/notice/read/1", auth=auth_headers)

    def test_notice_read_all(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "POST", "/system/notice/read-all", auth=auth_headers)

    def test_notice_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/notice/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )

    def test_business_notification_create_panel_and_handle(self, test_client: TestClient, auth_headers: dict) -> None:
        create_resp = test_client.post(
            "/system/notice/business/create",
            headers=auth_headers,
            json={
                "module": "wms",
                "biz_type": "stock_warning",
                "biz_id": "SKU-001",
                "title": "库存预警",
                "content": "SKU-001 库存低于安全线",
                "level": "warning",
                "action_url": "/wms/stock/SKU-001",
            },
        )
        assert create_resp.status_code == 200, create_resp.text
        notification = create_resp.json()["data"]
        assert notification["handled"] is False
        assert notification["module"] == "wms"
        assert notification["biz_type"] == "stock_warning"

        panel_resp = test_client.get("/system/notice/panel", headers=auth_headers)
        assert panel_resp.status_code == 200, panel_resp.text
        pendings = panel_resp.json()["data"]["pendings"]
        assert any(item["id"] == notification["id"] and item["level"] == "warning" for item in pendings)

        handle_resp = test_client.patch(
            f"/system/notice/business/handle/{notification['id']}",
            headers=auth_headers,
            json={"handled": True},
        )
        assert handle_resp.status_code == 200, handle_resp.text
        assert handle_resp.json()["data"]["handled"] is True

        panel_after = test_client.get("/system/notice/panel", headers=auth_headers)
        assert panel_after.status_code == 200, panel_after.text
        assert all(item["id"] != notification["id"] for item in panel_after.json()["data"]["pendings"])

    def test_business_notification_is_tenant_isolated(self, test_client: TestClient) -> None:
        old_register = settings.AUTH_LOGIN_REGISTER_ENABLE
        settings.AUTH_LOGIN_REGISTER_ENABLE = True
        try:
            register_a_resp = test_client.post(
                "/system/auth/tenant/register",
                json={
                    "username": "notice_tenant_a",
                    "password": "admin123",
                    "email": "notice_tenant_a@example.com",
                    "tenant_name": "通知隔离租户A",
                },
            )
            assert register_a_resp.status_code == 200, register_a_resp.text
            tenant_a = test_client.post(
                "/system/auth/login",
                data={"username": "notice_tenant_a", "password": "admin123", "login_type": "PC端"},
            )
            assert tenant_a.status_code == 200, tenant_a.text
            tenant_a_headers = {"Authorization": f"Bearer {tenant_a.json()['data']['access_token']}"}

            register_resp = test_client.post(
                "/system/auth/tenant/register",
                json={
                    "username": "notice_tenant_b",
                    "password": "admin123",
                    "email": "notice_tenant_b@example.com",
                    "tenant_name": "通知隔离租户",
                },
            )
            assert register_resp.status_code == 200, register_resp.text
            tenant_b = test_client.post(
                "/system/auth/login",
                data={"username": "notice_tenant_b", "password": "admin123", "login_type": "PC端"},
            )
            assert tenant_b.status_code == 200, tenant_b.text
            tenant_b_headers = {"Authorization": f"Bearer {tenant_b.json()['data']['access_token']}"}
        finally:
            settings.AUTH_LOGIN_REGISTER_ENABLE = old_register

        create_resp = test_client.post(
            "/system/notice/business/create",
            headers=tenant_a_headers,
            json={
                "module": "crm",
                "biz_type": "followup",
                "biz_id": "TENANT-A-NOTICE",
                "title": "租户A通知",
            },
        )
        assert create_resp.status_code == 200, create_resp.text

        list_resp = test_client.get(
            "/system/notice/business/list?biz_id=TENANT-A-NOTICE",
            headers=tenant_b_headers,
        )
        assert list_resp.status_code == 200, list_resp.text
        assert list_resp.json()["data"]["total"] == 0


class TestParams:
    """参数管理接口 — 数据验证。"""

    def test_params_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/param/list", auth=auth_headers)

    def test_params_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/param/create", auth=auth_headers,
            json={"param_name": "测试参数", "param_key": "test.key", "param_value": "val", "param_type": "string"},
        )

    def test_params_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/param/update/1", auth=auth_headers,
            json={"param_value": "new_val"},
        )

    def test_params_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/param/delete", auth=auth_headers, json=[9999])

    def test_params_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/param/detail/1", auth=auth_headers)

    def test_params_export(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/param/export", auth=auth_headers)

    def test_params_info(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/param/info", auth=auth_headers)

    def test_params_by_key(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/param/key/test.key", auth=auth_headers)

    def test_params_value_by_key(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/param/value/test.key", auth=auth_headers)

    def test_params_status_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PATCH", "/system/param/status/batch", auth=auth_headers,
            json={"ids": [1], "status": 1},
        )


class TestLog:
    """日志管理接口 — 数据验证。"""

    def test_login_log_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/log/login/list", auth=auth_headers)

    def test_login_log_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/log/login/detail/1", auth=auth_headers)

    def test_operation_log_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/log/operation/list", auth=auth_headers)

    def test_operation_log_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/log/operation/detail/1", auth=auth_headers)


class TestTicket:
    """工单管理接口 — 数据验证。"""

    def test_ticket_route_permissions_match_seed_permissions(self) -> None:
        expected = {
            ("GET", "/ticket/list"): ["module_system:ticket:query"],
            ("PUT", "/ticket/batch"): ["module_system:ticket:patch"],
            ("GET", "/ticket/export"): ["module_system:ticket:export"],
            ("GET", "/ticket/{ticket_id}/comments"): ["module_system:ticket:detail"],
            ("POST", "/ticket/{ticket_id}/comments"): ["module_system:ticket:detail"],
        }

        for (method, path), permissions in expected.items():
            assert _ticket_auth_permissions(path, method) == permissions

    def test_ticket_list(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/ticket/list", auth=auth_headers)

    def test_ticket_detail(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "GET", "/system/ticket/detail/1", auth=auth_headers)

    def test_ticket_create(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "POST", "/system/ticket/create", auth=auth_headers,
            json={"title": "测试工单", "ticket_type": "bug", "ticket_content": "内容描述"},
        )

    def test_ticket_update(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(
            test_client, "PUT", "/system/ticket/update/1", auth=auth_headers,
            json={"title": "更新工单"},
        )

    def test_ticket_delete(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "DELETE", "/system/ticket/delete", auth=auth_headers, json=[9999])

    def test_ticket_batch(self, test_client: TestClient, auth_headers: dict) -> None:
        assert_route(test_client, "PUT", "/system/ticket/batch", auth=auth_headers)

    def test_ticket_export(self, test_client: TestClient, auth_headers: dict) -> None:
        response = test_client.get("/system/ticket/export", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert response.content.startswith(b"PK")

    def test_ticket_comment_create_and_list(self, test_client: TestClient, auth_headers: dict) -> None:
        create_ticket = test_client.post(
            "/system/ticket/create",
            headers=auth_headers,
            json={"title": "评论接口测试工单", "ticket_type": "bug", "ticket_content": "待评论内容"},
        )
        assert create_ticket.status_code == 200, create_ticket.text
        ticket_id = create_ticket.json()["data"]["id"]

        create_comment = test_client.post(
            f"/system/ticket/{ticket_id}/comments",
            headers=auth_headers,
            json={"content": "第一条评论"},
        )
        assert create_comment.status_code == 200, create_comment.text
        comment = create_comment.json()["data"]
        assert comment["ticket_id"] == ticket_id
        assert comment["tenant_id"] == create_ticket.json()["data"]["tenant_id"]
        assert comment["content"] == "第一条评论"

        comment_list = test_client.get(
            f"/system/ticket/{ticket_id}/comments",
            headers=auth_headers,
            params={"page_no": 1, "page_size": 50},
        )
        assert comment_list.status_code == 200, comment_list.text
        result = comment_list.json()["data"]
        assert result["total"] == 1
        assert result["items"][0]["id"] == comment["id"]

    def test_ticket_comments_are_tenant_isolated(self, test_client: TestClient) -> None:
        old_register = settings.AUTH_LOGIN_REGISTER_ENABLE
        settings.AUTH_LOGIN_REGISTER_ENABLE = True
        try:
            tenants = []
            for suffix in ("a", "b"):
                register = test_client.post(
                    "/system/auth/tenant/register",
                    json={
                        "username": f"ticket_tenant_{suffix}",
                        "password": "admin123",
                        "email": f"ticket_tenant_{suffix}@example.com",
                        "tenant_name": f"工单隔离租户{suffix.upper()}",
                    },
                )
                assert register.status_code == 200, register.text
                tenants.append(register.json()["data"])
        finally:
            settings.AUTH_LOGIN_REGISTER_ENABLE = old_register

        async def assert_isolation() -> None:
            from sqlalchemy import select

            from app.api.v1.module_system.ticket.schema import TicketCommentCreateSchema, TicketCreateSchema
            from app.api.v1.module_system.ticket.service import TicketCommentService, TicketService
            from app.api.v1.module_system.user.model import UserModel
            from app.core.base_schema import AuthSchema
            from app.core.database import async_db_session
            from app.core.exceptions import CustomException

            async with async_db_session() as db:
                users = []
                for tenant in tenants:
                    result = await db.execute(select(UserModel).where(UserModel.id == tenant["user_id"]))
                    users.append(result.scalar_one())

                auth_a = AuthSchema(user=users[0], db=db, tenant_id=tenants[0]["tenant_id"])
                auth_b = AuthSchema(user=users[1], db=db, tenant_id=tenants[1]["tenant_id"])
                ticket = await TicketService(auth_a).create(
                    TicketCreateSchema(title="租户A工单", ticket_type="bug", ticket_content="隔离测试")
                )
                comment = await TicketCommentService(auth_a).create(
                    ticket_id=ticket.id,
                    data=TicketCommentCreateSchema(content="租户A评论"),
                )
                assert comment.tenant_id == tenants[0]["tenant_id"]

                platform_user = (
                    await db.execute(select(UserModel).where(UserModel.is_superuser.is_(True)))
                ).scalars().first()
                platform_auth = AuthSchema(user=platform_user, db=db, tenant_id=1)
                platform_comment = await TicketCommentService(platform_auth).create(
                    ticket_id=ticket.id,
                    data=TicketCommentCreateSchema(content="平台协助评论"),
                )
                assert platform_comment.tenant_id == tenants[0]["tenant_id"]

                tenant_b_tickets = await TicketService(auth_b).get_list()
                assert all(item.tenant_id == tenants[1]["tenant_id"] for item in tenant_b_tickets)
                assert all(item.id != ticket.id for item in tenant_b_tickets)

                import pytest

                with pytest.raises(CustomException):
                    await TicketCommentService(auth_b).page(ticket_id=ticket.id, page_no=1, page_size=50)
                with pytest.raises(CustomException):
                    await TicketCommentService(auth_b).create(
                        ticket_id=ticket.id,
                        data=TicketCommentCreateSchema(content="越权评论"),
                    )

                await db.rollback()

        import asyncio

        asyncio.run(assert_isolation())
