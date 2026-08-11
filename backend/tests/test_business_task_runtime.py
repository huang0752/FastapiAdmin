"""module_task Celery 业务任务运行时聚焦测试。"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from app.config.setting import Settings
from app.core.assembly import AssemblyConfig


def test_celery_defaults_are_safe_and_generic() -> None:
    config = Settings(_env_file=None)

    assert config.CELERY_ENABLED is False
    assert config.CELERY_DEFAULT_QUEUE == "business_tasks"
    assert config.CELERY_BROKER_KEY_PREFIX == "fastapiadmin:celery:"
    assert config.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert config.CELERY_TASK_ACKS_LATE is True
    assert config.CELERY_TASK_REJECT_ON_WORKER_LOST is True
    assert config.CELERY_RESULT_BACKEND is None


def test_celery_app_uses_json_and_no_result_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("app.plugin.module_task.runtime.celery_app")
    monkeypatch.setattr(module.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(module, "is_plugin_enabled", lambda code: code == "module_task")

    app = module.create_celery_app()

    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.result_backend is None
    assert app.conf.task_ignore_result is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True


def test_disabled_module_task_rejects_celery_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("app.plugin.module_task.runtime.celery_app")
    monkeypatch.setattr(module.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(module, "is_plugin_enabled", lambda code: False)

    with pytest.raises(module.CeleryRuntimeDisabledError, match="module_task"):
        module.create_celery_app()


def test_celery_disabled_rejects_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("app.plugin.module_task.runtime.celery_app")
    monkeypatch.setattr(module.settings, "CELERY_ENABLED", False)
    monkeypatch.setattr(module, "is_plugin_enabled", lambda code: True)

    with pytest.raises(module.CeleryRuntimeDisabledError, match="CELERY_ENABLED"):
        module.create_celery_app()


def test_assembly_exposes_only_enabled_plugin_task_modules(tmp_path) -> None:
    plugin_a = tmp_path / "module_a"
    plugin_a.mkdir()
    (plugin_a / "plugin.toml").write_text(
        '[plugin]\nname = "a"\n\n[runtime]\nbusiness_task_modules = ["app.plugin.module_a.tasks"]\n',
        encoding="utf-8",
    )
    plugin_b = tmp_path / "module_b"
    plugin_b.mkdir()
    (plugin_b / "plugin.toml").write_text(
        '[plugin]\nname = "b"\n\n[runtime]\nbusiness_task_modules = ["app.plugin.module_b.tasks"]\n',
        encoding="utf-8",
    )
    assembly = AssemblyConfig(enabled_plugins=["module_a"])

    assert assembly.business_task_modules(plugin_dir=tmp_path) == ["app.plugin.module_a.tasks"]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "queued"),
        ("pending", "enqueue_failed"),
        ("queued", "running"),
        ("running", "retrying"),
        ("running", "success"),
        ("retrying", "queued"),
        ("pending", "canceled"),
        ("queued", "canceled"),
    ],
)
def test_business_task_state_machine_allows_explicit_transitions(current: str, target: str) -> None:
    from app.plugin.module_task.runtime.state import ensure_transition

    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending", "success"),
        ("running", "pending"),
        ("success", "running"),
        ("failed", "queued"),
        ("canceled", "retrying"),
    ],
)
def test_business_task_state_machine_rejects_illegal_transitions(current: str, target: str) -> None:
    from app.plugin.module_task.runtime.state import InvalidBusinessTaskTransition, ensure_transition

    with pytest.raises(InvalidBusinessTaskTransition):
        ensure_transition(current, target)


def test_progress_is_monotonic_and_bounded() -> None:
    from app.plugin.module_task.runtime.state import InvalidBusinessTaskProgress, ensure_progress

    assert ensure_progress(20, 20) == 20
    assert ensure_progress(20, 80) == 80
    with pytest.raises(InvalidBusinessTaskProgress):
        ensure_progress(20, 19)
    with pytest.raises(InvalidBusinessTaskProgress):
        ensure_progress(20, 101)


def test_business_task_model_has_tenant_scoped_idempotency() -> None:
    from app.plugin.module_task.business.task.model import BusinessTaskModel

    constraints = {constraint.name for constraint in BusinessTaskModel.__table__.constraints}
    assert "uq_business_task_tenant_idempotency" in constraints
    assert "uq_business_task_tenant_external_task" in constraints


class _SamplePayload(BaseModel):
    value: int


async def _sample_handler(context, payload: _SamplePayload) -> dict:
    return {"value": payload.value}


def test_business_task_registry_registers_and_validates_payload() -> None:
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    registry = BusinessTaskRegistry()
    definition = registry.register(
        handler_code="sample.echo",
        handler=_sample_handler,
        module="sample",
        payload_schema=_SamplePayload,
        source="tests.sample",
    )

    assert registry.get("sample.echo") is definition
    assert definition.validate_payload({"value": 3}).value == 3


def test_business_task_registry_uses_handler_retry_backoff_or_global_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime import registry as registry_module
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    monkeypatch.setattr(registry_module.settings, "CELERY_RETRY_BACKOFF", 37)
    registry = BusinessTaskRegistry()

    custom = registry.register(
        handler_code="sample.custom_backoff",
        handler=_sample_handler,
        module="sample",
        retry_backoff_seconds=10,
    )
    default = registry.register(
        handler_code="sample.default_backoff",
        handler=_sample_handler,
        module="sample",
    )

    assert custom.retry_backoff_seconds == 10
    assert default.retry_backoff_seconds == 37


def test_business_task_registry_rejects_non_positive_retry_backoff() -> None:
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    with pytest.raises(ValueError, match="retry_backoff_seconds"):
        BusinessTaskRegistry().register(
            handler_code="sample.invalid_backoff",
            handler=_sample_handler,
            module="sample",
            retry_backoff_seconds=0,
        )


def test_business_task_registry_rejects_duplicate_handler() -> None:
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry, DuplicateBusinessTaskHandlerError

    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.echo", handler=_sample_handler, module="sample", source="plugin.a")

    with pytest.raises(DuplicateBusinessTaskHandlerError, match="plugin.a.*plugin.b"):
        registry.register(handler_code="sample.echo", handler=_sample_handler, module="sample", source="plugin.b")


def test_business_task_registry_rejects_unknown_handler() -> None:
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry, UnknownBusinessTaskHandlerError

    with pytest.raises(UnknownBusinessTaskHandlerError, match="missing.handler"):
        BusinessTaskRegistry().get("missing.handler")


def test_plugin_task_module_must_stay_inside_plugin_package(tmp_path) -> None:
    plugin = tmp_path / "module_sample"
    plugin.mkdir()
    (plugin / "plugin.toml").write_text(
        '[plugin]\nname = "sample"\n\n[runtime]\nbusiness_task_modules = ["outside.product.tasks"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="module_sample"):
        AssemblyConfig().business_task_modules(plugin_dir=tmp_path)


@dataclass
class _RecordingPublisher:
    messages: list[dict] = field(default_factory=list)
    fail: bool = False

    async def publish(self, **message) -> None:
        from sqlalchemy import select

        from app.core.database import async_db_session
        from app.plugin.module_task.business.task.model import BusinessTaskModel

        async with async_db_session() as db:
            assert (await db.execute(select(BusinessTaskModel.id).where(BusinessTaskModel.id == message["business_task_id"]))).scalar_one()
        if self.fail:
            raise ConnectionError("broker unavailable password=do-not-leak")
        self.messages.append(message)


async def _runtime_auth(username: str = "user"):
    from sqlalchemy import select

    from app.api.v1.module_system.user.model import UserModel
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session

    db = async_db_session()
    user = (await db.execute(select(UserModel).where(UserModel.username == username))).scalar_one()
    return db, AuthSchema(db=db, user=user, tenant_id=user.tenant_id)


@pytest.mark.asyncio
async def test_dispatcher_commits_before_publishing_and_sends_only_task_id(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.echo", handler=_sample_handler, module="sample", payload_schema=_SamplePayload)
    publisher = _RecordingPublisher()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=publisher).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.echo", biz_type="echo", payload={"value": 7}, idempotency_key="dispatch-commit-first"),
        )
    finally:
        await db.close()

    assert task.status == "queued"
    assert len(publisher.messages) == 1
    assert publisher.messages[0]["business_task_id"] == task.id
    assert set(publisher.messages[0]) == {"business_task_id", "celery_task_id", "queue", "soft_time_limit", "hard_time_limit"}


@pytest.mark.asyncio
async def test_dispatcher_idempotency_is_scoped_to_tenant(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.echo", handler=_sample_handler, module="sample", payload_schema=_SamplePayload)
    publisher = _RecordingPublisher()
    dispatcher = BusinessTaskDispatcher(registry=registry, publisher=publisher)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    request = DispatchRequest(handler_code="sample.echo", biz_type="echo", payload={"value": 1}, idempotency_key="same-request")
    try:
        first = await dispatcher.dispatch(auth=auth, request=request)
        second = await dispatcher.dispatch(auth=auth, request=request)
    finally:
        await db.close()

    assert first.id == second.id
    assert len(publisher.messages) == 1


@pytest.mark.asyncio
async def test_prepare_rolls_back_with_callers_business_transaction(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.prepare_rollback", handler=_sample_handler, module="sample", payload_schema=_SamplePayload)
    publisher = _RecordingPublisher()
    dispatcher = BusinessTaskDispatcher(registry=registry, publisher=publisher)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    original_name = auth.user.name
    try:
        auth.user.name = "prepare rollback marker"
        task = await dispatcher.prepare(
            auth=auth,
            request=DispatchRequest(
                handler_code="sample.prepare_rollback",
                biz_type="prepare",
                payload={"value": 1},
                idempotency_key="prepare-rollback",
            ),
        )
        assert task.status == "pending"
        assert publisher.messages == []
        await db.rollback()
    finally:
        await db.close()

    async with async_db_session() as check_db:
        persisted_task = (
            await check_db.execute(select(BusinessTaskModel).where(BusinessTaskModel.idempotency_key == "prepare-rollback"))
        ).scalar_one_or_none()
        persisted_user_name = (
            await check_db.execute(select(UserModel.name).where(UserModel.username == "user"))
        ).scalar_one()

    assert persisted_task is None
    assert persisted_user_name == original_name


@pytest.mark.asyncio
async def test_prepare_commit_can_publish_and_recovery_can_publish_pending(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.prepare_commit", handler=_sample_handler, module="sample", payload_schema=_SamplePayload)
    publisher = _RecordingPublisher()
    dispatcher = BusinessTaskDispatcher(registry=registry, publisher=publisher)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        first = await dispatcher.prepare(
            auth=auth,
            request=DispatchRequest(
                handler_code="sample.prepare_commit",
                biz_type="prepare",
                payload={"value": 2},
                idempotency_key="prepare-commit",
            ),
        )
        recovery = await dispatcher.prepare(
            auth=auth,
            request=DispatchRequest(
                handler_code="sample.prepare_commit",
                biz_type="prepare",
                payload={"value": 3},
                idempotency_key="prepare-recovery",
            ),
        )
        await db.commit()
    finally:
        await db.close()

    published = await dispatcher.publish_existing(first.id)
    assert published.status == "queued"
    assert [message["business_task_id"] for message in publisher.messages] == [first.id]

    recovered = await dispatcher.recover_pending()
    assert recovered == 1
    assert [message["business_task_id"] for message in publisher.messages] == [first.id, recovery.id]
    async with async_db_session() as check_db:
        statuses = dict(
            (
                await check_db.execute(
                    select(BusinessTaskModel.id, BusinessTaskModel.status).where(BusinessTaskModel.id.in_((first.id, recovery.id)))
                )
            ).all()
        )
    assert statuses == {first.id: "queued", recovery.id: "queued"}


@pytest.mark.asyncio
async def test_default_recovery_loads_handlers_before_scanning_after_cold_start(
    test_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime import dispatcher as dispatcher_module
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    publisher = _RecordingPublisher()
    isolated_registry = BusinessTaskRegistry()
    monkeypatch.setattr(dispatcher_module, "business_task_registry", isolated_registry)
    dispatcher = BusinessTaskDispatcher(registry=isolated_registry, publisher=publisher)
    monkeypatch.setattr(dispatcher_module.settings, "CELERY_ENABLED", True)
    isolated_registry.register(
        handler_code="sample.cold_start_recovery",
        handler=_sample_handler,
        module="sample",
        payload_schema=_SamplePayload,
    )
    db, auth = await _runtime_auth()
    try:
        pending = await dispatcher.prepare(
            auth=auth,
            request=DispatchRequest(
                handler_code="sample.cold_start_recovery",
                biz_type="prepare",
                payload={"value": 4},
                idempotency_key="cold-start-recovery",
            ),
        )
        await db.commit()
    finally:
        await db.close()

    isolated_registry.clear()
    load_calls = 0

    def load_cold_start_handlers() -> tuple[str, ...]:
        nonlocal load_calls
        load_calls += 1
        isolated_registry.register(
            handler_code="sample.cold_start_recovery",
            handler=_sample_handler,
            module="sample",
            payload_schema=_SamplePayload,
        )
        return ("tests.cold_start_tasks",)

    monkeypatch.setattr(dispatcher_module, "load_business_task_modules", load_cold_start_handlers)
    try:
        recovered = await dispatcher.recover_pending()
        async with async_db_session() as check_db:
            status = (
                await check_db.execute(
                    select(BusinessTaskModel.status).where(BusinessTaskModel.id == pending.id)
                )
            ).scalar_one()
    finally:
        isolated_registry.clear()

    assert load_calls == 1
    assert recovered == 1
    assert status == "queued"
    assert [message["business_task_id"] for message in publisher.messages] == [pending.id]


@pytest.mark.asyncio
async def test_broker_failure_is_recorded_as_recoverable(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.echo", handler=_sample_handler, module="sample", payload_schema=_SamplePayload)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        with pytest.raises(ConnectionError):
            await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher(fail=True)).dispatch(
                auth=auth,
                request=DispatchRequest(handler_code="sample.echo", biz_type="echo", payload={"value": 1}, idempotency_key="publish-failure"),
            )
    finally:
        await db.close()
    async with async_db_session() as check_db:
        task = (await check_db.execute(select(BusinessTaskModel).where(BusinessTaskModel.idempotency_key == "publish-failure"))).scalar_one()

    assert task.status == "enqueue_failed"
    assert task.error_code == "BROKER_PUBLISH_FAILED"
    assert "password" not in (task.error or "").lower()


@pytest.mark.asyncio
async def test_executor_claims_once_and_terminal_duplicate_is_noop(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    calls: list[int] = []

    async def handler(context, payload: _SamplePayload) -> dict:
        calls.append(payload.value)
        return {"echo": payload.value, "tenant_id": context.tenant_id}

    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.once", handler=handler, module="sample", payload_schema=_SamplePayload)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.once", biz_type="echo", payload={"value": 9}, idempotency_key="execute-once"),
        )
    finally:
        await db.close()

    executor = BusinessTaskExecutor(registry=registry)
    first = await executor.execute(task.id)
    second = await executor.execute(task.id)

    assert first.status == "success"
    assert second.status == "noop"
    assert calls == [9]


@pytest.mark.asyncio
async def test_retryable_error_retries_with_backoff_then_succeeds(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.exceptions import RetryableBusinessTaskError
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    calls = 0

    async def handler(context, payload: _SamplePayload) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableBusinessTaskError("temporary timeout")
        return {"value": payload.value}

    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.retry", handler=handler, module="sample", payload_schema=_SamplePayload, max_retries=1)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.retry", biz_type="retry", payload={"value": 2}, idempotency_key="retry-success"),
        )
    finally:
        await db.close()
    executor = BusinessTaskExecutor(registry=registry)

    first = await executor.execute(task.id)
    second = await executor.execute(task.id)

    assert first.status == "retrying"
    assert first.retry_countdown == 30
    assert second.status == "success"
    assert calls == 2


@pytest.mark.asyncio
async def test_retryable_error_uses_handler_retry_backoff_and_global_default(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime import registry as registry_module
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.exceptions import RetryableBusinessTaskError
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client

    async def handler(context, payload: _SamplePayload) -> dict:
        raise RetryableBusinessTaskError("temporary timeout")

    monkeypatch.setattr(registry_module.settings, "CELERY_RETRY_BACKOFF", 41)
    registry = BusinessTaskRegistry()
    registry.register(
        handler_code="sample.retry_ten_seconds",
        handler=handler,
        module="sample",
        payload_schema=_SamplePayload,
        max_retries=1,
        retry_backoff_seconds=10,
    )
    registry.register(
        handler_code="sample.retry_global_default",
        handler=handler,
        module="sample",
        payload_schema=_SamplePayload,
        max_retries=1,
    )
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        custom = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.retry_ten_seconds", biz_type="retry", payload={"value": 1}),
        )
        default = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.retry_global_default", biz_type="retry", payload={"value": 2}),
        )
    finally:
        await db.close()

    executor = BusinessTaskExecutor(registry=registry)
    custom_outcome = await executor.execute(custom.id)
    default_outcome = await executor.execute(default.id)

    assert custom_outcome.retry_countdown == 10
    assert default_outcome.retry_countdown == 41


@pytest.mark.asyncio
async def test_retry_outcome_carries_persisted_limit_above_celery_default(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.exceptions import RetryableBusinessTaskError
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client

    async def handler(context, payload: _SamplePayload) -> dict:
        raise RetryableBusinessTaskError("temporary timeout")

    registry = BusinessTaskRegistry()
    registry.register(
        handler_code="sample.retry_five",
        handler=handler,
        module="sample",
        payload_schema=_SamplePayload,
        max_retries=5,
    )
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(
                handler_code="sample.retry_five",
                biz_type="retry",
                payload={"value": 5},
                idempotency_key="retry-limit-above-celery-default",
            ),
        )
    finally:
        await db.close()

    executor = BusinessTaskExecutor(registry=registry)
    outcome = await executor.execute(task.id)

    assert outcome.status == "retrying"
    assert outcome.retry_countdown == 30
    assert outcome.retry_budget == 5
    assert outcome.retry_attempt == 1
    remaining = [await executor.execute(task.id) for _ in range(5)]
    assert [item.status for item in remaining] == ["retrying", "retrying", "retrying", "retrying", "failed"]


def test_worker_adds_database_retry_budget_to_current_celery_count(monkeypatch: pytest.MonkeyPatch) -> None:
    celery_app_module = importlib.import_module("app.plugin.module_task.runtime.celery_app")
    monkeypatch.setattr(celery_app_module.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(celery_app_module, "is_plugin_enabled", lambda code: code == "module_task")
    worker = importlib.import_module("app.plugin.module_task.runtime.worker")
    from app.plugin.module_task.runtime.executor import ExecutionOutcome

    class FakeExecutor:
        async def execute(self, business_task_id: int) -> ExecutionOutcome:
            assert business_task_id == 42
            return ExecutionOutcome(status="retrying", retry_countdown=30, retry_budget=3, retry_attempt=3)

    class RetrySignal(Exception):
        pass

    captured: dict[str, int | None] = {}

    def fake_retry(*, countdown: int, max_retries: int | None):
        captured.update(countdown=countdown, max_retries=max_retries)
        return RetrySignal()

    monkeypatch.setattr(worker, "BusinessTaskExecutor", FakeExecutor)
    monkeypatch.setattr(worker.execute_business_task, "retry", fake_retry)
    worker.execute_business_task.push_request(retries=4)

    try:
        with pytest.raises(RetrySignal):
            worker.execute_business_task.run(42)
    finally:
        worker.execute_business_task.pop_request()

    assert captured == {"countdown": 30, "max_retries": 7}


def test_worker_lease_deferral_does_not_consume_business_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    celery_app_module = importlib.import_module("app.plugin.module_task.runtime.celery_app")
    monkeypatch.setattr(celery_app_module.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(celery_app_module, "is_plugin_enabled", lambda code: code == "module_task")
    worker = importlib.import_module("app.plugin.module_task.runtime.worker")
    from app.plugin.module_task.runtime.executor import ExecutionOutcome

    class FakeExecutor:
        async def execute(self, business_task_id: int) -> ExecutionOutcome:
            return ExecutionOutcome(status="deferred", retry_countdown=30, retry_budget=1)

    class RetrySignal(Exception):
        pass

    captured: dict[str, int | None] = {}

    def fake_retry(*, countdown: int, max_retries: int | None):
        captured.update(countdown=countdown, max_retries=max_retries)
        return RetrySignal()

    monkeypatch.setattr(worker, "BusinessTaskExecutor", FakeExecutor)
    monkeypatch.setattr(worker.execute_business_task, "retry", fake_retry)
    worker.execute_business_task.push_request(retries=8)
    try:
        with pytest.raises(RetrySignal):
            worker.execute_business_task.run(42)
    finally:
        worker.execute_business_task.pop_request()

    assert captured == {"countdown": 30, "max_retries": 9}


def test_worker_closes_retrying_task_if_celery_rejects_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.exceptions import MaxRetriesExceededError

    celery_app_module = importlib.import_module("app.plugin.module_task.runtime.celery_app")
    monkeypatch.setattr(celery_app_module.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(celery_app_module, "is_plugin_enabled", lambda code: code == "module_task")
    worker = importlib.import_module("app.plugin.module_task.runtime.worker")
    from app.plugin.module_task.runtime.executor import ExecutionOutcome

    exhausted: list[int] = []

    class FakeExecutor:
        async def execute(self, business_task_id: int) -> ExecutionOutcome:
            return ExecutionOutcome(status="retrying", retry_countdown=30, retry_budget=1, retry_attempt=5)

        async def fail_retry_exhausted(self, business_task_id: int, *, expected_attempt: int) -> None:
            exhausted.extend((business_task_id, expected_attempt))

    def reject_retry(*, countdown: int, max_retries: int | None):
        raise MaxRetriesExceededError()

    monkeypatch.setattr(worker, "BusinessTaskExecutor", FakeExecutor)
    monkeypatch.setattr(worker.execute_business_task, "retry", reject_retry)

    worker.execute_business_task.run(42)

    assert exhausted == [42, 5]


def test_worker_marks_retry_recoverable_if_retry_publish_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.exceptions import Reject

    celery_app_module = importlib.import_module("app.plugin.module_task.runtime.celery_app")
    monkeypatch.setattr(celery_app_module.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(celery_app_module, "is_plugin_enabled", lambda code: code == "module_task")
    worker = importlib.import_module("app.plugin.module_task.runtime.worker")
    from app.plugin.module_task.runtime.executor import ExecutionOutcome

    recoverable: list[int] = []

    class FakeExecutor:
        async def execute(self, business_task_id: int) -> ExecutionOutcome:
            return ExecutionOutcome(status="retrying", retry_countdown=30, retry_budget=2, retry_attempt=4)

        async def mark_retry_enqueue_failed(self, business_task_id: int, *, expected_attempt: int) -> None:
            recoverable.extend((business_task_id, expected_attempt))

    def reject_publish(*, countdown: int, max_retries: int | None):
        raise Reject(ConnectionError("broker unavailable secret=do-not-leak"), requeue=False)

    monkeypatch.setattr(worker, "BusinessTaskExecutor", FakeExecutor)
    monkeypatch.setattr(worker.execute_business_task, "retry", reject_publish)

    worker.execute_business_task.run(42)

    assert recoverable == [42, 4]


@pytest.mark.asyncio
async def test_executor_closes_retrying_database_state_after_celery_exhaustion(test_client) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor

    _ = test_client
    db, auth = await _runtime_auth()
    try:
        task = BusinessTaskModel(
            tenant_id=auth.tenant_id,
            created_id=auth.user.id,
            updated_id=auth.user.id,
            module="sample",
            biz_type="retry-exhausted",
            handler_code="sample.retry_exhausted",
            queue="business_tasks",
            external_task_id="retry-exhausted-state",
            status="retrying",
            progress=0,
            attempt=4,
            max_retries=5,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
    finally:
        await db.close()

    await BusinessTaskExecutor().fail_retry_exhausted(task.id, expected_attempt=4)

    async with async_db_session() as check_db:
        failed = await check_db.get(BusinessTaskModel, task.id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error_code == "RETRIES_EXHAUSTED"
        assert failed.finished_at is not None


@pytest.mark.asyncio
async def test_executor_does_not_close_newer_retry_generation(test_client) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor

    _ = test_client
    db, auth = await _runtime_auth()
    try:
        task = BusinessTaskModel(
            tenant_id=auth.tenant_id,
            created_id=auth.user.id,
            updated_id=auth.user.id,
            module="sample",
            biz_type="retry-generation",
            handler_code="sample.retry_generation",
            queue="business_tasks",
            external_task_id="retry-generation-state",
            status="retrying",
            progress=0,
            attempt=5,
            max_retries=5,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
    finally:
        await db.close()

    await BusinessTaskExecutor().fail_retry_exhausted(task.id, expected_attempt=4)

    async with async_db_session() as check_db:
        current = await check_db.get(BusinessTaskModel, task.id)
        assert current is not None
        assert current.status == "retrying"
        assert current.attempt == 5


@pytest.mark.asyncio
async def test_executor_marks_same_retry_generation_recoverable_after_publish_failure(test_client) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor

    _ = test_client
    db, auth = await _runtime_auth()
    try:
        task = BusinessTaskModel(
            tenant_id=auth.tenant_id,
            created_id=auth.user.id,
            updated_id=auth.user.id,
            module="sample",
            biz_type="retry-publish-failure",
            handler_code="sample.retry_publish_failure",
            queue="business_tasks",
            external_task_id="retry-publish-failure-state",
            status="retrying",
            progress=0,
            attempt=4,
            max_retries=5,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
    finally:
        await db.close()

    executor = BusinessTaskExecutor()
    await executor.mark_retry_enqueue_failed(task.id, expected_attempt=3)
    async with async_db_session() as check_db:
        unchanged = await check_db.get(BusinessTaskModel, task.id)
        assert unchanged is not None and unchanged.status == "retrying"

    await executor.mark_retry_enqueue_failed(task.id, expected_attempt=4)
    async with async_db_session() as check_db:
        recoverable = await check_db.get(BusinessTaskModel, task.id)
        assert recoverable is not None
        assert recoverable.status == "enqueue_failed"
        assert recoverable.error_code == "BROKER_PUBLISH_FAILED"
        assert recoverable.enqueue_failed_at is not None


@pytest.mark.asyncio
async def test_non_retryable_error_fails_without_retry(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client

    async def handler(context, payload: _SamplePayload) -> dict:
        raise ValueError("business validation failed")

    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.fail", handler=handler, module="sample", payload_schema=_SamplePayload, max_retries=3)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.fail", biz_type="fail", payload={"value": 2}, idempotency_key="no-blind-retry"),
        )
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(registry=registry).execute(task.id)
    async with async_db_session() as check_db:
        failed = (await check_db.execute(select(BusinessTaskModel).where(BusinessTaskModel.id == task.id))).scalar_one()

    assert outcome.status == "failed"
    assert failed.status == "failed"
    assert failed.attempt == 1
    assert failed.error_code == "BUSINESS_TASK_FAILED"


@pytest.mark.asyncio
async def test_pending_or_queued_task_can_be_canceled_and_will_not_execute(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.business.task.service import BusinessTaskService
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    calls = 0

    async def handler(context, payload: _SamplePayload) -> dict:
        nonlocal calls
        calls += 1
        return {}

    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.cancel", handler=handler, module="sample", payload_schema=_SamplePayload)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.cancel", biz_type="cancel", payload={"value": 1}, idempotency_key="cancel-before-run"),
        )
        canceled = await BusinessTaskService(auth).cancel(task.id)
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(registry=registry).execute(task.id)
    assert canceled.status == "canceled"
    assert outcome.status == "noop"
    assert calls == 0


@pytest.mark.asyncio
async def test_tenant_context_comes_from_database_not_payload(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    observed: list[tuple[int, int]] = []

    async def handler(context, payload: dict) -> dict:
        observed.append((context.tenant_id, payload["tenant_id"]))
        return {"tenant_id": context.tenant_id}

    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.tenant", handler=handler, module="sample")
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.tenant", biz_type="tenant", payload={"tenant_id": 999999}, idempotency_key="trusted-tenant"),
        )
        trusted_tenant_id = auth.tenant_id
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(registry=registry).execute(task.id)
    assert outcome.status == "success"
    assert observed == [(trusted_tenant_id, 999999)]


@pytest.mark.asyncio
async def test_same_idempotency_key_is_allowed_for_different_tenants(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    from app.api.v1.module_system.user.model import UserModel
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.multitenant", handler=_sample_handler, module="sample", payload_schema=_SamplePayload)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    publisher = _RecordingPublisher()
    dispatcher = BusinessTaskDispatcher(registry=registry, publisher=publisher)
    async with async_db_session() as db:
        users = (await db.execute(select(UserModel).where(UserModel.username.in_(("user", "test_user"))))).scalars().all()
        assert len({user.tenant_id for user in users}) == 2
        tasks = []
        for user in users:
            tasks.append(
                await dispatcher.dispatch(
                    auth=AuthSchema(db=db, user=user, tenant_id=user.tenant_id),
                    request=DispatchRequest(handler_code="sample.multitenant", biz_type="tenant", payload={"value": 1}, idempotency_key="tenant-local-key"),
                )
            )

    assert tasks[0].id != tasks[1].id
    assert tasks[0].tenant_id != tasks[1].tenant_id


@pytest.mark.asyncio
async def test_missing_actor_fails_without_running_handler(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    calls = 0

    async def handler(context, payload: _SamplePayload) -> dict:
        nonlocal calls
        calls += 1
        return {}

    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.actor", handler=handler, module="sample", payload_schema=_SamplePayload)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.actor", biz_type="actor", payload={"value": 1}, idempotency_key="missing-actor"),
        )
    finally:
        await db.close()
    async with async_db_session() as mutate_db:
        persisted = await mutate_db.get(BusinessTaskModel, task.id)
        persisted.created_id = None
        await mutate_db.commit()

    outcome = await BusinessTaskExecutor(registry=registry).execute(task.id)
    async with async_db_session() as check_db:
        failed = (await check_db.execute(select(BusinessTaskModel).where(BusinessTaskModel.id == task.id))).scalar_one()

    assert outcome.status == "failed"
    assert failed.error_code == "ACTOR_INVALID"
    assert calls == 0


@pytest.mark.parametrize("revocation", ["menu_disabled", "package_disabled", "package_permission_removed"])
@pytest.mark.asyncio
async def test_executor_rechecks_effective_package_menu_permissions(
    test_client,
    monkeypatch: pytest.MonkeyPatch,
    revocation: str,
) -> None:
    from sqlalchemy import delete, select
    from sqlalchemy.orm import selectinload

    from app.api.v1.module_platform.menu.model import MenuModel
    from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
    from app.api.v1.module_platform.tenant.model import TenantModel
    from app.api.v1.module_system.role.model import RoleMenusModel
    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    permission = f"tests:background:{revocation}"
    calls = 0

    async def handler(context, payload: _SamplePayload) -> dict:
        nonlocal calls
        calls += 1
        return {"value": payload.value}

    registry = BusinessTaskRegistry()
    registry.register(
        handler_code=f"sample.{revocation}",
        handler=handler,
        module="sample",
        payload_schema=_SamplePayload,
        required_permissions=(permission,),
    )
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    menu_id: int | None = None
    package_id: int | None = None
    try:
        async with async_db_session() as setup_db:
            user = (
                await setup_db.execute(
                    select(UserModel)
                    .options(selectinload(UserModel.roles))
                    .where(UserModel.username == "test_user")
                )
            ).scalar_one()
            tenant = await setup_db.get(TenantModel, user.tenant_id)
            assert tenant is not None and tenant.package_id is not None
            assert user.roles
            package_id = tenant.package_id
            menu = MenuModel(
                name=f"后台权限复核-{revocation}",
                type=3,
                order=999,
                permission=permission,
                scope="tenant",
                status=0,
            )
            setup_db.add(menu)
            await setup_db.flush()
            menu_id = menu.id
            setup_db.add(RoleMenusModel(role_id=user.roles[0].id, menu_id=menu.id))
            setup_db.add(PackageMenuModel(package_id=package_id, menu_id=menu.id))
            await setup_db.commit()

        db, auth = await _runtime_auth("test_user")
        try:
            task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
                auth=auth,
                request=DispatchRequest(
                    handler_code=f"sample.{revocation}",
                    biz_type="permission-recheck",
                    payload={"value": 1},
                    idempotency_key=f"permission-recheck-{revocation}",
                ),
            )
        finally:
            await db.close()

        async with async_db_session() as revoke_db:
            if revocation == "menu_disabled":
                menu = await revoke_db.get(MenuModel, menu_id)
                assert menu is not None
                menu.status = 1
            elif revocation == "package_disabled":
                package = await revoke_db.get(PackageModel, package_id)
                assert package is not None
                package.status = 1
            else:
                await revoke_db.execute(
                    delete(PackageMenuModel).where(
                        PackageMenuModel.package_id == package_id,
                        PackageMenuModel.menu_id == menu_id,
                    )
                )
            await revoke_db.commit()

        outcome = await BusinessTaskExecutor(registry=registry).execute(task.id)
        async with async_db_session() as check_db:
            failed = await check_db.get(BusinessTaskModel, task.id)
            assert failed is not None
            assert failed.status == "failed"
            assert failed.error_code == "ACTOR_INVALID"
        assert outcome.status == "failed"
        assert calls == 0
    finally:
        if menu_id is not None:
            async with async_db_session() as cleanup_db:
                if package_id is not None:
                    package = await cleanup_db.get(PackageModel, package_id)
                    if package is not None:
                        package.status = 0
                    await cleanup_db.execute(
                        delete(PackageMenuModel).where(
                            PackageMenuModel.package_id == package_id,
                            PackageMenuModel.menu_id == menu_id,
                        )
                    )
                await cleanup_db.execute(delete(RoleMenusModel).where(RoleMenusModel.menu_id == menu_id))
                await cleanup_db.execute(delete(MenuModel).where(MenuModel.id == menu_id))
                await cleanup_db.commit()


@pytest.mark.asyncio
async def test_background_superuser_bypasses_role_grant_but_not_menu_entitlement(test_client) -> None:
    from sqlalchemy import delete, select

    from app.api.v1.module_platform.menu.model import MenuModel
    from app.api.v1.module_platform.package.model import PackageMenuModel
    from app.api.v1.module_platform.tenant.model import TenantModel
    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session
    from app.plugin.module_task.runtime.context import build_background_auth
    from app.plugin.module_task.runtime.exceptions import InvalidBackgroundActorError

    _ = test_client
    permission = "tests:background:superuser-entitlement"
    menu_id: int | None = None
    actor_id: int | None = None
    async with async_db_session() as setup_db:
        actor = (await setup_db.execute(select(UserModel).where(UserModel.username == "super"))).scalar_one()
        tenant = await setup_db.get(TenantModel, 2)
        assert actor.is_superuser and tenant is not None and tenant.package_id is not None
        actor_id = actor.id
        menu = MenuModel(name="后台超管菜单复核", type=3, order=999, permission=permission, scope="tenant", status=0)
        setup_db.add(menu)
        await setup_db.flush()
        menu_id = menu.id
        setup_db.add(PackageMenuModel(package_id=tenant.package_id, menu_id=menu.id))
        await setup_db.commit()

    try:
        async with async_db_session() as allowed_db:
            auth = await build_background_auth(
                allowed_db,
                tenant_id=2,
                actor_user_id=actor_id,
                required_permissions=(permission,),
            )
            assert auth.tenant_id == 2

        async with async_db_session() as revoke_db:
            menu = await revoke_db.get(MenuModel, menu_id)
            assert menu is not None
            menu.status = 1
            await revoke_db.commit()

        async with async_db_session() as denied_db:
            with pytest.raises(InvalidBackgroundActorError, match="所需权限"):
                await build_background_auth(
                    denied_db,
                    tenant_id=2,
                    actor_user_id=actor_id,
                    required_permissions=(permission,),
                )
    finally:
        if menu_id is not None:
            async with async_db_session() as cleanup_db:
                await cleanup_db.execute(delete(PackageMenuModel).where(PackageMenuModel.menu_id == menu_id))
                await cleanup_db.execute(delete(MenuModel).where(MenuModel.id == menu_id))
                await cleanup_db.commit()


@pytest.mark.asyncio
async def test_disabled_package_revokes_owner_minimum_permission_for_background_task(test_client) -> None:
    from sqlalchemy import delete, select
    from sqlalchemy.orm import selectinload

    from app.api.v1.module_platform.menu.model import MenuModel
    from app.api.v1.module_platform.package.model import PackageModel
    from app.api.v1.module_platform.tenant.model import TenantModel
    from app.api.v1.module_system.role.model import RoleMenusModel
    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session
    from app.plugin.module_task.runtime.context import build_background_auth
    from app.plugin.module_task.runtime.exceptions import InvalidBackgroundActorError

    _ = test_client
    permission = "module_system:user:update"
    added_role_menu = False
    role_id: int | None = None
    menu_id: int | None = None
    package_id: int | None = None
    actor_id: int | None = None
    async with async_db_session() as setup_db:
        actor = (
            await setup_db.execute(
                select(UserModel).options(selectinload(UserModel.roles)).where(UserModel.username == "test_admin")
            )
        ).scalar_one()
        tenant = await setup_db.get(TenantModel, actor.tenant_id)
        menu = (await setup_db.execute(select(MenuModel).where(MenuModel.permission == permission))).scalar_one()
        assert tenant is not None and tenant.package_id is not None and actor.roles
        actor_id = actor.id
        package_id = tenant.package_id
        role_id = actor.roles[0].id
        menu_id = menu.id
        existing = (
            await setup_db.execute(
                select(RoleMenusModel).where(RoleMenusModel.role_id == role_id, RoleMenusModel.menu_id == menu_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            setup_db.add(RoleMenusModel(role_id=role_id, menu_id=menu_id))
            added_role_menu = True
        package = await setup_db.get(PackageModel, package_id)
        assert package is not None
        package.status = 1
        await setup_db.commit()

    try:
        async with async_db_session() as denied_db:
            with pytest.raises(InvalidBackgroundActorError, match="套餐"):
                await build_background_auth(
                    denied_db,
                    tenant_id=2,
                    actor_user_id=actor_id,
                    required_permissions=(permission,),
                )
        async with async_db_session() as no_permission_db:
            with pytest.raises(InvalidBackgroundActorError, match="套餐"):
                await build_background_auth(
                    no_permission_db,
                    tenant_id=2,
                    actor_user_id=actor_id,
                )
    finally:
        async with async_db_session() as cleanup_db:
            package = await cleanup_db.get(PackageModel, package_id)
            assert package is not None
            package.status = 0
            if added_role_menu:
                await cleanup_db.execute(
                    delete(RoleMenusModel).where(RoleMenusModel.role_id == role_id, RoleMenusModel.menu_id == menu_id)
                )
            await cleanup_db.commit()


@pytest.mark.asyncio
async def test_unknown_handler_message_fails_without_dynamic_import(test_client) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.model import BusinessTaskModel
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    db, auth = await _runtime_auth()
    try:
        task = BusinessTaskModel(
            tenant_id=auth.tenant_id,
            created_id=auth.user.id,
            updated_id=auth.user.id,
            module="sample",
            biz_type="unknown",
            handler_code="sample.missing",
            queue="business_tasks",
            external_task_id="unknown-handler-message",
            status="queued",
            progress=0,
            attempt=0,
            max_retries=3,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(registry=BusinessTaskRegistry()).execute(task.id)
    async with async_db_session() as check_db:
        failed = await check_db.get(BusinessTaskModel, task.id)
        assert failed.status == "failed"
        assert failed.error_code == "INVALID_TASK"
        assert failed.attempt == 1
    assert outcome.status == "failed"


@pytest.mark.asyncio
async def test_heartbeat_extends_owned_lease(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_task.runtime.context import BusinessTaskContext, build_background_auth
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    registry = BusinessTaskRegistry()
    registry.register(handler_code="sample.heartbeat", handler=_sample_handler, module="sample", payload_schema=_SamplePayload)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _runtime_auth()
    try:
        task = await BusinessTaskDispatcher(registry=registry, publisher=_RecordingPublisher()).dispatch(
            auth=auth,
            request=DispatchRequest(handler_code="sample.heartbeat", biz_type="heartbeat", payload={"value": 1}, idempotency_key="heartbeat-lease"),
        )
    finally:
        await db.close()
    executor = BusinessTaskExecutor(registry=registry)
    claimed = await executor._claim(task.id)
    assert isinstance(claimed, tuple)
    running, token = claimed
    original_lease = running.lease_expires_at
    monkeypatch.setattr("app.plugin.module_task.runtime.context.settings.CELERY_LEASE_SECONDS", 120)
    async with async_db_session() as context_db:
        context = BusinessTaskContext(
            task_id=running.id,
            tenant_id=running.tenant_id,
            actor_user_id=running.created_id,
            trace_id=running.trace_id,
            execution_token=token,
            db=context_db,
            auth=await build_background_auth(context_db, tenant_id=running.tenant_id, actor_user_id=running.created_id),
            session_factory=async_db_session,
        )
        assert await context.heartbeat() is True
    async with async_db_session() as check_db:
        refreshed = await check_db.get(type(running), running.id)
        assert refreshed.heartbeat_at is not None
        assert refreshed.lease_expires_at.replace(tzinfo=None) > original_lease.replace(tzinfo=None)
