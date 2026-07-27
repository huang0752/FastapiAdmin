from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from starlette.requests import Request

from app.api.v1.module_platform.order.model import OrderModel, PaymentRecordModel
from app.api.v1.module_platform.order.service import PaymentService
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.dict.model import DictTypeModel
from app.api.v1.module_system.user.model import UserModel
from app.api.v1.module_system.user.service import UserService
from app.config.setting import settings
from app.core import ap_scheduler as scheduler_module
from app.core.ap_scheduler import SchedulerUtil
from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.middlewares import RequestLogMiddleware, _tenant_is_whitelisted
from app.core.router_class import _write_operation_log_async
from app.plugin.module_task.cronjob.job.model import JobModel
from app.plugin.module_task.workflow.flows.crud import WorkflowCRUD
from app.plugin.module_task.workflow.flows.schema import WorkflowExecuteSchema
from app.plugin.module_task.workflow.flows.service import WorkflowService
from app.plugin.module_task.workflow.handlers.workflow_engine import run_workflow_sync
from app.plugin.module_task.workflow.nodes.schema import (
    WorkflowNodeTypeCreateSchema,
    WorkflowNodeTypeUpdateSchema,
)
from app.plugin.module_task.workflow.nodes.service import WorkflowNodeTypeService
from app.utils.hash_bcrpy_util import PwdUtil


def _unique(prefix: str) -> str:
    return f"{prefix[:10]}_{time.time_ns() % 1_000_000_000_000}"


async def _create_tenant(name_prefix: str = "隔离租户") -> int:
    suffix = str(time.time_ns() % 1_000_000_000_000)
    async with async_db_session() as db:
        tenant = TenantModel(name=f"{name_prefix}{suffix}", code=f"T{suffix}", site_id=1)
        db.add(tenant)
        await db.commit()
        return tenant.id


async def _create_duplicate_users(
    username: str,
    password: str,
    *,
    mobile: str = "13900000000",
) -> tuple[int, int]:
    tenant_ids = [await _create_tenant("重复账号甲"), await _create_tenant("重复账号乙")]
    async with async_db_session() as db:
        for index, tenant_id in enumerate(tenant_ids):
            db.add(
                UserModel(
                    username=username,
                    password=PwdUtil.hash_password(password),
                    name=f"重复账号{index}",
                    mobile=mobile,
                    email=f"{username}@example.com",
                    tenant_id=tenant_id,
                    status=0,
                    is_superuser=False,
                )
            )
        await db.commit()
    return tenant_ids[0], tenant_ids[1]


def _route_permissions(app, path: str, method: str) -> list[str]:
    from app.core.dependencies import AuthPermission

    for route in app.routes:
        if getattr(route, "path", None) != path or method not in (getattr(route, "methods", None) or set()):
            continue
        permissions = [
            dep.call.permissions
            for dep in route.dependant.dependencies
            if isinstance(dep.call, AuthPermission)
        ]
        assert len(permissions) == 1
        return permissions[0]
    raise AssertionError(f"未找到路由: {method} {path}")


def test_login_fails_closed_when_username_exists_in_multiple_tenants(test_client: TestClient) -> None:
    username = _unique("dup_login")
    asyncio.run(_create_duplicate_users(username, "duplicate123"))

    response = test_client.post(
        "/system/auth/login",
        data={"username": username, "password": "duplicate123", "login_type": "PC端"},
    )

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert "不唯一" in response.json()["msg"]


def test_legacy_password_reset_fails_closed_for_duplicate_username(test_client: TestClient) -> None:
    username = _unique("dup_reset")
    mobile = "13900000001"
    asyncio.run(_create_duplicate_users(username, "duplicate123", mobile=mobile))

    old_enabled = settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE
    old_mode = settings.AUTH_PASSWORD_RESET_MODE
    settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = True
    settings.AUTH_PASSWORD_RESET_MODE = "legacy_mobile"
    try:
        response = test_client.post(
            "/system/user/password/forget",
            json={"username": username, "mobile": mobile, "new_password": "replacement123"},
        )
    finally:
        settings.AUTH_LOGIN_FORGOT_PASSWORD_ENABLE = old_enabled
        settings.AUTH_PASSWORD_RESET_MODE = old_mode

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert "不唯一" in response.json()["msg"]


def test_tenant_mixin_has_no_implicit_platform_default() -> None:
    assert JobModel.__table__.c.tenant_id.default is None


async def _exercise_crud_tenant_immutability(operation: str) -> tuple[int, int]:
    tenant_id = await _create_tenant("CRUD当前租户")
    other_tenant_id = await _create_tenant("CRUD目标租户")
    async with async_db_session() as db:
        auth = AuthSchema(
            db=db,
            user=SimpleNamespace(id=1, is_superuser=False, roles=[], dept_id=None),
            tenant_id=tenant_id,
            check_data_scope=False,
        )
        crud = CRUDBase(model=JobModel, auth=auth)
        obj = await crud.create(
            {
                "tenant_id": other_tenant_id,
                "job_id": _unique(f"crud_{operation}"),
                "status": 0,
            }
        )
        assert obj.tenant_id == tenant_id

        if operation == "update":
            await crud.update(obj.id, {"tenant_id": other_tenant_id})
        elif operation == "set":
            await crud.set([obj.id], tenant_id=other_tenant_id)
        else:
            raise AssertionError(f"未知操作: {operation}")

        await db.refresh(obj)
        return tenant_id, obj.tenant_id


@pytest.mark.parametrize("operation", ["update", "set"])
def test_non_superuser_cannot_move_rows_between_tenants(operation: str, test_client: TestClient) -> None:
    expected_tenant_id, actual_tenant_id = asyncio.run(_exercise_crud_tenant_immutability(operation))

    assert actual_tenant_id == expected_tenant_id


def test_crud_create_injects_explicit_tenant_without_user(test_client: TestClient) -> None:
    async def exercise() -> tuple[int, int]:
        tenant_id = await _create_tenant("无用户显式上下文")
        async with async_db_session() as db:
            crud = CRUDBase(
                model=JobModel,
                auth=AuthSchema(db=db, tenant_id=tenant_id, check_data_scope=False),
            )
            obj = await crud.create(
                {
                    "tenant_id": 1,
                    "job_id": _unique("explicit"),
                    "status": 0,
                }
            )
            return tenant_id, obj.tenant_id

    expected, actual = asyncio.run(exercise())
    assert actual == expected


@pytest.mark.parametrize("operation", ["read", "write"])
def test_authenticated_crud_fails_closed_without_tenant_context(
    operation: str,
    test_client: TestClient,
) -> None:
    async def exercise() -> None:
        async with async_db_session() as db:
            crud = CRUDBase(
                model=JobModel,
                auth=AuthSchema(db=db, check_data_scope=False),
            )
            with pytest.raises(CustomException, match="租户上下文缺失"):
                if operation == "read":
                    await crud.get_list()
                else:
                    await crud.set([1], status=1)

    asyncio.run(exercise())


def test_platform_shared_rows_require_platform_global_context(test_client: TestClient) -> None:
    async def exercise() -> set[int]:
        tenant_id = await _create_tenant("共享字典覆盖")
        async with async_db_session() as db:
            db.add(
                DictTypeModel(
                    tenant_id=tenant_id,
                    dict_name="租户覆盖字典",
                    dict_type=_unique("shared_dict"),
                    status=0,
                )
            )
            await db.flush()
            auth = AuthSchema(
                db=db,
                user=SimpleNamespace(id=9, is_superuser=False, roles=[]),
                tenant_id=1,
                check_data_scope=False,
            )
            rows = await CRUDBase(model=DictTypeModel, auth=auth).get_list()
            return {row.tenant_id for row in rows}

    assert asyncio.run(exercise()) == {1}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/task/workflow/node-type/create"),
        ("PUT", "/task/workflow/node-type/update/{id}"),
        ("DELETE", "/task/workflow/node-type/delete"),
    ],
)
def test_workflow_python_management_is_platform_superuser_only(
    test_client: TestClient,
    method: str,
    path: str,
) -> None:
    assert _route_permissions(test_client.app, path, method) == ["*:*:*"]


def _job_info(tenant_id: int, node_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        id=node_id,
        tenant_id=tenant_id,
        name="隔离任务",
        func="def handler(*args, **kwargs):\n    return 'ok'",
        args=None,
        kwargs=None,
        jobstore="memory",
        executor="threadpool",
        coalesce=False,
    )


def test_scheduler_namespaces_job_id_with_explicit_tenant(monkeypatch) -> None:
    captured: dict = {}

    def fake_add_job(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["id"])

    monkeypatch.setattr(scheduler_module.scheduler, "add_job", fake_add_job)
    monkeypatch.setattr(SchedulerUtil, "_job_name_cache", {})

    SchedulerUtil._add_job_with_trigger(_job_info(tenant_id=1), trigger=object())

    assert captured["id"] == "tenant:1:node:7"
    assert captured["args"][:3] == ["tenant:1:node:7", 1, _job_info(1).func]


def test_scheduler_rejects_tenant_python_before_scheduling(monkeypatch) -> None:
    scheduled = False

    def fake_add_job(**kwargs):
        nonlocal scheduled
        scheduled = True
        return SimpleNamespace(id=kwargs["id"])

    monkeypatch.setattr(scheduler_module.scheduler, "add_job", fake_add_job)

    with pytest.raises(PermissionError, match="租户任务不允许执行自定义 Python"):
        SchedulerUtil._add_job_with_trigger(_job_info(tenant_id=2), trigger=object())

    assert scheduled is False


def test_scheduler_wrapper_rejects_tenant_python_without_executing() -> None:
    code = "def handler(*args, **kwargs):\n    raise AssertionError('不应执行')"

    with pytest.raises(PermissionError, match="租户任务不允许执行自定义 Python"):
        SchedulerUtil._task_wrapper(
            job_id="tenant:2:node:7",
            tenant_id=2,
            code_block=code,
        )


def test_workflow_engine_passes_explicit_platform_tenant_to_python(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_task_wrapper(job_id, tenant_id, code_block, *args, **kwargs):
        captured.update(job_id=job_id, tenant_id=tenant_id, code_block=code_block)
        return "ok"

    monkeypatch.setattr(SchedulerUtil, "_task_wrapper", fake_task_wrapper)
    result = run_workflow_sync(
        nodes=[{"id": "node-1", "type": "action"}],
        edges=[],
        node_templates={
            "action": {
                "func": "def handler(*args, **kwargs):\n    return 'ok'",
                "args": None,
                "kwargs": None,
            }
        },
        flow_variables={},
        tenant_id=1,
    )

    assert result["node_results"] == {"node-1": "ok"}
    assert captured["tenant_id"] == 1


def test_workflow_engine_rejects_tenant_before_starting_threads(monkeypatch) -> None:
    def fail_if_thread_started(*args, **kwargs):
        raise AssertionError("租户工作流不应创建执行线程")

    monkeypatch.setattr(
        "app.plugin.module_task.workflow.handlers.workflow_engine.ThreadPoolExecutor",
        fail_if_thread_started,
    )

    with pytest.raises(PermissionError, match="租户工作流不允许执行自定义 Python"):
        run_workflow_sync(
            nodes=[{"id": "node-1", "type": "action"}],
            edges=[],
            node_templates={"action": {"func": "def handler():\n    return 1"}},
            flow_variables={},
            tenant_id=2,
        )


def test_workflow_service_rejects_tenant_before_to_thread(monkeypatch) -> None:
    async def fake_get_workflow(self, id):
        return SimpleNamespace(
            id=id,
            tenant_id=2,
            name="租户工作流",
            workflow_status=1,
            nodes=[{"id": "node-1", "type": "action"}],
            edges=[],
        )

    async def fail_if_to_thread(*args, **kwargs):
        raise AssertionError("租户工作流不应进入线程执行")

    monkeypatch.setattr(WorkflowCRUD, "get_obj_by_id_crud", fake_get_workflow)
    monkeypatch.setattr(asyncio, "to_thread", fail_if_to_thread)
    async def exercise() -> None:
        async with async_db_session() as db:
            auth = AuthSchema(
                db=db,
                user=SimpleNamespace(id=2, is_superuser=False),
                tenant_id=2,
                check_data_scope=False,
            )
            with pytest.raises(CustomException, match="租户工作流不允许执行自定义 Python"):
                await WorkflowService(auth).execute_workflow(
                    WorkflowExecuteSchema(workflow_id=9),
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("method", ["create", "update", "delete"])
@pytest.mark.parametrize("is_superuser", [False, True])
def test_workflow_python_service_requires_platform_global_context(
    method: str,
    is_superuser: bool,
    test_client: TestClient,
) -> None:
    async def exercise() -> None:
        async with async_db_session() as db:
            auth = AuthSchema(
                db=db,
                user=SimpleNamespace(id=2, is_superuser=is_superuser),
                tenant_id=2,
                check_data_scope=False,
            )
            service = WorkflowNodeTypeService(auth)
            with pytest.raises(CustomException, match="仅平台管理员可操作"):
                if method == "create":
                    await service.create(
                        WorkflowNodeTypeCreateSchema(
                            name="禁止租户节点",
                            code="tenantBlockedNode",
                            func="def handler():\n    return 1",
                        )
                    )
                elif method == "update":
                    await service.update(
                        1,
                        WorkflowNodeTypeUpdateSchema(
                            name="禁止租户节点",
                            code="tenantBlockedNode",
                            func="def handler():\n    return 1",
                        ),
                    )
                else:
                    await service.delete([1])

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("tenant_id", "expected_ids", "package_called"),
    [(1, None, False), (2, [10, 11], True)],
)
def test_superuser_current_info_respects_impersonated_tenant_menus(
    tenant_id: int,
    expected_ids: list[int] | None,
    package_called: bool,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {"package_called": False}

    async def fake_get_user(self, **kwargs):
        return SimpleNamespace(id=1, dept=None)

    async def fake_available_menu_ids(self, requested_tenant_id):
        captured["package_called"] = True
        assert requested_tenant_id == tenant_id
        return [11, 10]

    async def fake_tree_list(self, search=None, **kwargs):
        captured["search"] = search
        return []

    monkeypatch.setattr(
        "app.api.v1.module_system.user.service.UserCRUD.get",
        fake_get_user,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.user.service.UserOutSchema.model_validate",
        lambda value: SimpleNamespace(menus=None),
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.user.service.PackageService.get_tenant_available_menu_ids",
        fake_available_menu_ids,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.user.service.MenuCRUD.tree_list",
        fake_tree_list,
    )

    async def exercise() -> None:
        async with async_db_session() as db:
            auth = AuthSchema(
                db=db,
                user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
                tenant_id=tenant_id,
                check_data_scope=False,
            )
            await UserService(auth).current_info()

    asyncio.run(exercise())

    assert captured["package_called"] is package_called
    search = captured["search"]
    if expected_ids is None:
        assert "id" not in search
    else:
        assert search["id"] == ("in", expected_ids)


async def _read_job_log_tenant(job_id: str) -> int:
    async with async_db_session() as db:
        row = (
            await db.execute(select(JobModel).where(JobModel.job_id == job_id))
        ).scalar_one()
        return row.tenant_id


async def _delete_job_log(job_id: str) -> None:
    async with async_db_session() as db:
        await db.execute(delete(JobModel).where(JobModel.job_id == job_id))
        await db.commit()


def test_scheduler_log_persists_tenant_from_namespaced_job_id(test_client: TestClient) -> None:
    tenant_id = asyncio.run(_create_tenant("日志租户"))
    job_id = f"tenant:{tenant_id}:node:{time.time_ns()}"

    try:
        log_id = SchedulerUtil._create_job_log(job_id=job_id, job_name="显式租户日志")

        assert log_id is not None
        assert asyncio.run(_read_job_log_tenant(job_id)) == tenant_id
    finally:
        asyncio.run(_delete_job_log(job_id))


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/system/auth/login/extra",
        "/api/v1/healthcheck",
        "/docsevil",
        "/metrics-private",
    ],
)
def test_tenant_whitelist_does_not_prefix_match_exact_paths(path: str) -> None:
    assert _tenant_is_whitelisted(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/system/auth/login",
        "/docs",
        "/docs/index.html",
        "/static/app.js",
    ],
)
def test_tenant_whitelist_keeps_exact_and_explicit_prefix_entries(path: str) -> None:
    assert _tenant_is_whitelisted(path) is True


def test_request_log_config_does_not_fallback_to_platform_tenant(monkeypatch) -> None:
    called_with: list[int] = []

    async def fake_load(redis, tenant_id: int):
        called_with.append(tenant_id)
        return {"demo_enable": True}

    monkeypatch.setattr(
        "app.core.middlewares.ParamsService.get_system_config_for_middleware",
        fake_load,
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/private",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
            "app": SimpleNamespace(state=SimpleNamespace(redis=object())),
        }
    )

    config = asyncio.run(RequestLogMiddleware._load_config(request))

    assert config["demo_enable"] is False
    assert called_with == []


@pytest.mark.parametrize("tenant_id", [None, 2])
def test_operation_log_requires_explicit_request_tenant(
    tenant_id: int | None,
    monkeypatch,
) -> None:
    captured: list[int | None] = []

    async def fake_create(self, data):
        captured.append(self.auth.tenant_id)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(
        "app.api.v1.module_system.log.service.OperationLogService.create",
        fake_create,
    )
    payload = {
        "request_path": "/private",
        "request_method": "POST",
        "response_code": 200,
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id

    asyncio.run(_write_operation_log_async(payload))

    assert captured == ([] if tenant_id is None else [tenant_id])


def test_payment_callback_binds_order_tenant_before_scoped_writes(
    test_client: TestClient,
    monkeypatch,
) -> None:
    class FakeGateway:
        async def verify_callback(self, callback_data):
            return SimpleNamespace(
                verified=True,
                order_id=None,
                amount=100,
                transaction_id=_unique("transaction"),
                raw={"verified": True},
            )

    async def fake_activate(auth, order):
        return None

    monkeypatch.setattr(
        "app.api.v1.module_platform.order.service.create_payment_gateway",
        lambda method: FakeGateway(),
    )
    monkeypatch.setattr(PaymentService, "_activate_tenant_package", fake_activate)

    async def exercise() -> tuple[int, int, int | None]:
        tenant_id = await _create_tenant("支付回调租户")
        order_no = _unique("payment")
        async with async_db_session() as db:
            order = OrderModel(
                tenant_id=tenant_id,
                order_no=order_no,
                order_type="new",
                amount=100,
                expire_time=datetime.now() + timedelta(minutes=15),
                status=0,
            )
            db.add(order)
            await db.commit()

            auth = AuthSchema(db=db, check_data_scope=False)
            await PaymentService.handle_callback(
                auth=auth,
                method="mock",
                callback_data={"order_no": order_no},
            )
            record = (
                await db.execute(
                    select(PaymentRecordModel).where(PaymentRecordModel.order_id == order.id)
                )
            ).scalar_one()
            return tenant_id, record.tenant_id, auth.tenant_id

    expected, record_tenant_id, callback_tenant_id = asyncio.run(exercise())
    assert record_tenant_id == expected
    assert callback_tenant_id == expected
