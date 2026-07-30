"""
模块接口测试 —— 插件模块 - task（任务调度）

动态路由映射：module_task → /task
包含 cronjob（调度器/任务/日志/节点）和 workflow（工作流定义/节点类型）。

每个接口一个测试用例，覆盖查询 / 新增 / 修改 / 删除 等操作。
"""

from conftest import assert_route
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.dependencies import AuthPermission
from app.plugin.module_task.business.task.controller import BusinessTaskRouter, DemoBatchRouter


def _route_permissions(router, path: str, method: str) -> list[str]:
    for route in router.routes:
        if getattr(route, "path", None) != path or method not in getattr(route, "methods", set()):
            continue
        for dependency in route.dependant.dependencies:
            if isinstance(dependency.call, AuthPermission):
                return dependency.call.permissions
    raise AssertionError(f"未找到任务路由: {method} {path}")

# ============================================================
# /task/cronjob — 调度器与任务
# ============================================================


class TestCronjobScheduler:
    """调度器管理接口。"""

    def test_scheduler_status(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/job/scheduler/status")

    def test_scheduler_jobs(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/job/scheduler/jobs")

    def test_scheduler_start(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/scheduler/start")

    def test_scheduler_pause(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/scheduler/pause")

    def test_scheduler_resume(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/scheduler/resume")

    def test_scheduler_console(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/job/scheduler/console")

    def test_scheduler_sync(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/scheduler/sync")

    def test_scheduler_shutdown(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/scheduler/shutdown")

    def test_scheduler_jobs_clear(self, test_client: TestClient) -> None:
        assert_route(test_client, "DELETE", "/task/cronjob/job/scheduler/jobs/clear")


class TestCronjobTask:
    """任务管理接口。"""

    def test_task_pause(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/task/pause/test_job")

    def test_task_resume(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/task/resume/test_job")

    def test_task_run(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/cronjob/job/task/run/test_job")

    def test_task_remove(self, test_client: TestClient) -> None:
        assert_route(test_client, "DELETE", "/task/cronjob/job/task/remove/test_job")


class TestCronjobLog:
    """执行日志接口。"""

    def test_job_log_list(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/job/log/list")

    def test_job_log_detail(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/job/log/detail/1")

    def test_job_log_delete(self, test_client: TestClient) -> None:
        assert_route(test_client, "DELETE", "/task/cronjob/job/log/delete", json=[9999])


class TestCronjobNode:
    """节点管理接口。"""

    def test_node_options(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/node/options")

    def test_node_list(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/node/list")

    def test_node_detail(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/cronjob/node/detail/1")

    def test_node_create(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/task/cronjob/node/create",
            json={"name": "测试节点", "node_type": "http"},
        )

    def test_node_update(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "PUT",
            "/task/cronjob/node/update/1",
            json={"name": "更新节点"},
        )

    def test_node_delete(self, test_client: TestClient) -> None:
        assert_route(test_client, "DELETE", "/task/cronjob/node/delete", json=[9999])

    def test_node_execute(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/task/cronjob/node/execute/1",
            json={},
        )

    def test_node_clear(self, test_client: TestClient) -> None:
        assert_route(test_client, "DELETE", "/task/cronjob/node/clear")

    def test_node_status_batch(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "PATCH",
            "/task/cronjob/node/status/batch",
            json={"ids": [1], "status": 1},
        )


# ============================================================
# /task/workflow — 工作流
# ============================================================


class TestWorkflowDefinition:
    """工作流定义接口。"""

    def test_workflow_list(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/workflow/definition/list")

    def test_workflow_detail(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/workflow/definition/detail/1")

    def test_workflow_create(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/task/workflow/definition/create",
            json={"name": "测试工作流", "node_graph": {"nodes": [], "edges": []}},
        )

    def test_workflow_update(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "PUT",
            "/task/workflow/definition/update/1",
            json={"name": "更新工作流"},
        )

    def test_workflow_delete(self, test_client: TestClient) -> None:
        assert_route(test_client, "DELETE", "/task/workflow/definition/delete", json=[9999])

    def test_workflow_publish(self, test_client: TestClient) -> None:
        assert_route(test_client, "POST", "/task/workflow/definition/publish/1")

    def test_workflow_execute(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/task/workflow/definition/execute",
            json={"definition_id": 1, "input_data": {}},
        )


class TestWorkflowNodeType:
    """工作流节点类型接口。"""

    def test_wf_node_type_options(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/workflow/node-type/options")

    def test_wf_node_type_list(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/workflow/node-type/list")

    def test_wf_node_type_detail(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/workflow/node-type/detail/1")

    def test_wf_node_type_create(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "POST",
            "/task/workflow/node-type/create",
            json={"name": "测试节点类型", "code": "test_type"},
        )

    def test_wf_node_type_update(self, test_client: TestClient) -> None:
        assert_route(
            test_client,
            "PUT",
            "/task/workflow/node-type/update/1",
            json={"name": "更新节点类型"},
        )

    def test_wf_node_type_delete(self, test_client: TestClient) -> None:
        assert_route(test_client, "DELETE", "/task/workflow/node-type/delete", json=[9999])

    def test_wf_node_type_select(self, test_client: TestClient) -> None:
        assert_route(test_client, "GET", "/task/workflow/node-type/select")


# ============================================================
# /task/business — 通用业务任务/试用数据/行业样例框架
# ============================================================


class TestBusinessTask:
    """通用业务长任务中心。"""

    def test_business_task_routes_require_explicit_permissions(self) -> None:
        assert _route_permissions(BusinessTaskRouter, "/business/task/list", "GET") == [
            "module_task:business_task:query"
        ]
        assert _route_permissions(BusinessTaskRouter, "/business/task/detail/{id}", "GET") == [
            "module_task:business_task:detail"
        ]
        assert _route_permissions(BusinessTaskRouter, "/business/task/cancel/{id}", "POST") == [
            "module_task:business_task:cancel"
        ]
        assert _route_permissions(BusinessTaskRouter, "/business/task/retry/{id}", "POST") == [
            "module_task:business_task:retry"
        ]
        assert _route_permissions(BusinessTaskRouter, "/business/task/monitor/health", "GET") == [
            "module_task:business_task:monitor"
        ]
        assert _route_permissions(DemoBatchRouter, "/demo-batch/trigger", "POST") == [
            "module_task:demo_batch:execute"
        ]
        assert _route_permissions(DemoBatchRouter, "/demo-batch/clean/{demo_batch_id}", "DELETE") == [
            "module_task:demo_batch:delete"
        ]

    def test_business_task_has_no_arbitrary_create_or_status_api(self, test_client: TestClient) -> None:
        paths = test_client.app.openapi()["paths"]
        assert "/task/business/task/create" not in paths
        assert "/task/business/task/status/{id}" not in paths

    async def test_business_task_is_tenant_isolated(self, test_client: TestClient) -> None:
        _ = test_client
        from app.api.v1.module_system.user.model import UserModel
        from app.core.base_schema import AuthSchema
        from app.core.database import async_db_session
        from app.plugin.module_task.business.task.schema import BusinessTaskCreateSchema, BusinessTaskQueryParam
        from app.plugin.module_task.business.task.service import BusinessTaskService

        async with async_db_session() as db:
            tenant_a_user = (
                await db.execute(select(UserModel).where(UserModel.username == "user"))
            ).scalar_one()
            tenant_b_user = (
                await db.execute(select(UserModel).where(UserModel.username == "test_user"))
            ).scalar_one()

            tenant_a_auth = AuthSchema(db=db, user=tenant_a_user, tenant_id=tenant_a_user.tenant_id)
            tenant_b_auth = AuthSchema(db=db, user=tenant_b_user, tenant_id=tenant_b_user.tenant_id)

            await BusinessTaskService(tenant_a_auth).create(
                BusinessTaskCreateSchema(
                    module="crm",
                    biz_type="customer_import",
                    biz_id="TENANT-A-ONLY",
                    title="租户A任务",
                )
            )
            await db.flush()

            search = BusinessTaskQueryParam(biz_id="TENANT-A-ONLY")
            page = await BusinessTaskService(tenant_b_auth).page(page_no=1, page_size=10, search=search)

        assert page.total == 0
        assert page.items == []

    async def test_business_task_detail_cancel_and_retry_are_tenant_isolated(self, test_client: TestClient) -> None:
        _ = test_client
        import pytest

        from app.api.v1.module_system.user.model import UserModel
        from app.core.base_schema import AuthSchema
        from app.core.database import async_db_session
        from app.core.exceptions import CustomException
        from app.plugin.module_task.business.task.schema import BusinessTaskCreateSchema
        from app.plugin.module_task.business.task.service import BusinessTaskService

        async with async_db_session() as db:
            tenant_a_user = (await db.execute(select(UserModel).where(UserModel.username == "user"))).scalar_one()
            tenant_b_user = (await db.execute(select(UserModel).where(UserModel.username == "test_user"))).scalar_one()
            tenant_a_auth = AuthSchema(db=db, user=tenant_a_user, tenant_id=tenant_a_user.tenant_id)
            tenant_b_auth = AuthSchema(db=db, user=tenant_b_user, tenant_id=tenant_b_user.tenant_id)
            task = await BusinessTaskService(tenant_a_auth).create(
                BusinessTaskCreateSchema(module="sample", biz_type="tenant_guard", title="租户隔离动作")
            )
            await db.flush()

            service = BusinessTaskService(tenant_b_auth)
            with pytest.raises(CustomException):
                await service.detail(task.id)
            with pytest.raises(CustomException):
                await service.cancel(task.id)
            with pytest.raises(CustomException):
                await service.retry(task.id)

    def test_business_task_health_distinguishes_disabled_runtime(self, test_client: TestClient, auth_headers: dict) -> None:
        response = test_client.get("/task/business/task/monitor/health", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "celery_disabled"


class TestDemoBatchAndIndustrySamples:
    """试用数据初始化任务框架和行业样例包。"""

    def test_demo_batch_trigger_and_clean_framework(self, test_client: TestClient, auth_headers: dict) -> None:
        trigger_resp = test_client.post(
            "/task/demo-batch/trigger",
            headers=auth_headers,
            json={"module": "sample", "scenario": "starter"},
        )
        assert trigger_resp.status_code == 200, trigger_resp.text
        batch = trigger_resp.json()["data"]
        assert batch["module"] == "sample"
        assert batch["scenario"] == "starter"
        assert batch["is_demo"] is True
        assert batch["demo_batch_id"]
        assert batch["task_id"] > 0

        clean_resp = test_client.delete(
            f"/task/demo-batch/clean/{batch['demo_batch_id']}",
            headers=auth_headers,
        )
        assert clean_resp.status_code == 200, clean_resp.text
        assert clean_resp.json()["data"]["demo_batch_id"] == batch["demo_batch_id"]

    def test_framework_does_not_ship_product_sample_data(self, test_client: TestClient, auth_headers: dict) -> None:
        packs_resp = test_client.get("/task/industry/sample-packs", headers=auth_headers)
        assert packs_resp.status_code == 200, packs_resp.text
        assert packs_resp.json()["data"] == []

        terms_resp = test_client.get("/task/industry/terms?module=sample", headers=auth_headers)
        assert terms_resp.status_code == 200, terms_resp.text
        assert terms_resp.json()["data"] == []

    def test_industry_terms_only_exposes_generic_module_filter(self, test_client: TestClient) -> None:
        operation = test_client.app.openapi()["paths"]["/task/industry/terms"]["get"]
        query_parameters = {
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter.get("in") == "query"
        }

        assert "module" in query_parameters
        assert "wms" not in query_parameters
