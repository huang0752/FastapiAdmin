"""Control tenant-provision worker retry and accounting contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.module_control.application_package.model import ControlApplicationPackageModel
from app.api.v1.module_control.model import ControlApplicationModel, ControlTenantApplicationModel, ControlUserApplicationGrantModel
from app.api.v1.module_control.tenant_provision.model import ControlTenantProvisionModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.plugin.module_task.business.task.model import BusinessTaskModel
from app.plugin.module_task.runtime.context import BusinessTaskContext
from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
from app.plugin.module_task.runtime.registry import business_task_registry
from app.utils.hash_bcrpy_util import PwdUtil


async def _seed_worker_case(
    *,
    max_retries: int = 1,
    task_attempt: int = 0,
    desired_target_tenant_code: str | None = None,
) -> tuple[int, int]:
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    async with async_db_session() as db:
        tenant = TenantModel(name=f"Worker租户{suffix}", code=f"worker{suffix}", site_id=1, status=0)
        owner = UserModel(
            tenant_id=1,
            username=f"worker{suffix}_admin",
            password=PwdUtil.hash_password("test-password"),
            name="Worker管理员",
            status=0,
        )
        application = ControlApplicationModel(
            site_id=1,
            code=f"worker{suffix}",
            name="Worker目标",
            base_url="http://target.example.test",
            callback_url="http://target.example.test/web#/auth/control-sso-callback",
            client_id=f"worker-{suffix}",
            client_secret_hash=PwdUtil.hash_password(f"secret-{suffix}"),
            provisioning_url="http://target.example.test/system/auth/control/tenant/provision",
            provisioning_enabled=True,
            provisioning_timeout_seconds=10,
            status=0,
        )
        db.add_all([tenant, owner, application])
        await db.flush()
        db.add(TenantUserModel(tenant_id=tenant.id, user_id=owner.id, role="owner", is_default=1))
        package = ControlApplicationPackageModel(
            site_id=1,
            application_id=application.id,
            code="pro",
            name="专业版",
            target_package_code="pro",
            status=0,
        )
        db.add(package)
        await db.flush()
        provision = ControlTenantProvisionModel(
            site_id=1,
            tenant_id=tenant.id,
            application_id=application.id,
            application_package_id=package.id,
            owner_user_id=owner.id,
            provision_request_uuid=str(uuid4()),
            desired_target_tenant_code=desired_target_tenant_code or tenant.code,
            status="pending",
        )
        db.add(provision)
        await db.flush()
        task = BusinessTaskModel(
            tenant_id=1,
            created_id=1,
            updated_id=1,
            handler_code="control.tenant_provision",
            module="control",
            biz_type="tenant_provision",
            biz_id=str(provision.id),
            title="目标租户开通",
            payload={"provision_id": provision.id, "mode": "initial"},
            queue="default",
            idempotency_key=f"tenant-provision:{provision.provision_request_uuid}:initial",
            status="pending",
            progress=0,
            attempt=task_attempt,
            max_retries=max_retries,
            trace_id=uuid4().hex,
        )
        db.add(task)
        await db.commit()
        return provision.id, task.id


def test_handler_registers_single_retry_with_fixed_ten_second_backoff() -> None:
    import app.plugin.module_control_provision.handlers  # noqa: F401

    definition = business_task_registry.get("control.tenant_provision")
    assert definition.max_retries == 1
    assert definition.retry_backoff_seconds == 10


def test_target_result_rejects_tenant_code_drift() -> None:
    from app.api.v1.module_control.tenant_provision.service import (
        ControlTenantProvisionWorkerService,
        _TargetProvisionError,
    )
    from app.api.v1.module_control.tenant_provision.task_schema import ControlTargetProvisionResult

    result = ControlTargetProvisionResult(
        result="created",
        target_tenant_id=88,
        target_tenant_uuid=str(uuid4()),
        target_tenant_code="wrong-code",
    )

    with pytest.raises(_TargetProvisionError, match="租户编码") as exc_info:
        ControlTenantProvisionWorkerService._validate_target_result("expected-code", result)

    assert exc_info.value.code == "TARGET_TENANT_CODE_MISMATCH"
    assert exc_info.value.retryable is False


def test_worker_retries_timeout_once_then_succeeds_and_creates_idempotent_opening(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del test_client
    import app.plugin.module_control_provision.handlers  # noqa: F401
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case(desired_target_tenant_code="remote01"))
    calls = 0

    async def target(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "result": "created",
                    "target_tenant_id": 88,
                    "target_tenant_uuid": str(uuid4()),
                    "target_tenant_code": "remote01",
                }
            },
        )

    monkeypatch.setattr(ControlTenantProvisionWorkerService, "transport", httpx.MockTransport(target))
    executor = BusinessTaskExecutor(session_factory=async_db_session)
    first = asyncio.run(executor.execute(task_id))
    if first.status != "retrying":
        async def failure_snapshot():
            async with async_db_session() as db:
                task = await db.get(BusinessTaskModel, task_id)
                provision = await db.get(ControlTenantProvisionModel, provision_id)
                return task.error_code, task.error, provision.last_error_code, provision.last_error_message

        pytest.fail(f"first attempt did not retry: {asyncio.run(failure_snapshot())}")
    second = asyncio.run(executor.execute(task_id))
    assert first.retry_countdown == 10
    assert second.status == "success"
    assert calls == 2

    async def assert_success() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            assert provision.attempt_count == 2
            assert provision.status == "succeeded"
            opening = (
                await db.execute(
                    select(ControlTenantApplicationModel).where(
                        ControlTenantApplicationModel.tenant_id == provision.tenant_id,
                        ControlTenantApplicationModel.application_id == provision.application_id,
                    )
                )
            ).scalar_one()
            grants = (
                await db.execute(
                    select(ControlUserApplicationGrantModel).where(
                        ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                        ControlUserApplicationGrantModel.user_id == provision.owner_user_id,
                    )
                )
            ).scalars().all()
            assert len(grants) == 1
            owner_grant = grants[0]
            assert owner_grant.desired_state == "active"
            assert owner_grant.sync_status == "succeeded"
            assert owner_grant.sync_version >= 1
            assert owner_grant.last_event_id == provision.provision_request_uuid
            assert owner_grant.last_synced_at is not None
            assert owner_grant.status == 0
            assert owner_grant.is_deleted is False

    asyncio.run(assert_success())


def test_worker_success_restores_soft_deleted_opening_and_owner_grant_in_portal(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del test_client
    import app.plugin.module_control_provision.handlers  # noqa: F401
    from app.api.v1.module_control.service import ControlPortalService
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case(desired_target_tenant_code="restored-target"))
    deleted_at = datetime.now(UTC)

    async def seed_deleted_access() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            opening = ControlTenantApplicationModel(
                site_id=provision.site_id,
                tenant_id=provision.tenant_id,
                application_id=provision.application_id,
                target_tenant_code="old-target",
                status=1,
                is_deleted=True,
                deleted_time=deleted_at,
                deleted_id=1,
            )
            db.add(opening)
            await db.flush()
            db.add(
                ControlUserApplicationGrantModel(
                    site_id=provision.site_id,
                    tenant_application_id=opening.id,
                    tenant_id=provision.tenant_id,
                    user_id=provision.owner_user_id,
                    status=1,
                    is_deleted=True,
                    deleted_time=deleted_at,
                    deleted_id=1,
                )
            )
            await db.commit()

    asyncio.run(seed_deleted_access())

    def target(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "result": "already_exists",
                    "target_tenant_id": 88,
                    "target_tenant_uuid": str(uuid4()),
                    "target_tenant_code": "restored-target",
                }
            },
        )

    monkeypatch.setattr(ControlTenantProvisionWorkerService, "transport", httpx.MockTransport(target))
    outcome = asyncio.run(BusinessTaskExecutor(session_factory=async_db_session).execute(task_id))
    assert outcome.status == "success"

    async def assert_restored() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            opening = (
                await db.execute(
                    select(ControlTenantApplicationModel).where(
                        ControlTenantApplicationModel.tenant_id == provision.tenant_id,
                        ControlTenantApplicationModel.application_id == provision.application_id,
                    )
                )
            ).scalar_one()
            grant = (
                await db.execute(
                    select(ControlUserApplicationGrantModel).where(
                        ControlUserApplicationGrantModel.tenant_application_id == opening.id,
                        ControlUserApplicationGrantModel.user_id == provision.owner_user_id,
                    )
                )
            ).scalar_one()
            assert opening.status == 0
            assert opening.is_deleted is False
            assert opening.deleted_time is None
            assert opening.deleted_id is None
            assert grant.status == 0
            assert grant.desired_state == "active"
            assert grant.sync_status == "succeeded"
            assert grant.sync_version >= 1
            assert grant.last_event_id == provision.provision_request_uuid
            assert grant.last_synced_at is not None
            assert grant.is_deleted is False
            assert grant.deleted_time is None
            assert grant.deleted_id is None

            owner = await db.get(UserModel, provision.owner_user_id)
            application = await db.get(ControlApplicationModel, provision.application_id)
            assert owner is not None and application is not None
            applications = await ControlPortalService(
                AuthSchema(db=db, user=owner, tenant_id=provision.tenant_id, site_id=provision.site_id)
            ).my_applications()
            assert [item.code for item in applications] == [application.code]

    asyncio.run(assert_restored())


@pytest.mark.parametrize(
    "generation",
    ["user_revoke", "same_bootstrap", "legacy_with_user_task", "non_initial_no_task"],
)
def test_owner_bootstrap_never_overwrites_a_user_command_generation(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    generation: str,
) -> None:
    del test_client
    import app.plugin.module_control_provision.handlers  # noqa: F401
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case(desired_target_tenant_code="generation-target"))

    async def seed_generation() -> tuple[int, str | None, int, str, str]:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            opening = ControlTenantApplicationModel(
                site_id=provision.site_id,
                tenant_id=provision.tenant_id,
                application_id=provision.application_id,
                target_tenant_code="old-target",
                status=0,
            )
            db.add(opening)
            await db.flush()
            event_id = provision.provision_request_uuid if generation == "same_bootstrap" else None
            if generation == "user_revoke":
                event_id = f"revoke-{uuid4().hex}"
            is_active = generation in {"same_bootstrap", "non_initial_no_task"}
            grant = ControlUserApplicationGrantModel(
                site_id=provision.site_id,
                tenant_application_id=opening.id,
                tenant_id=provision.tenant_id,
                user_id=provision.owner_user_id,
                status=0 if is_active else 1,
                desired_state="active" if is_active else "inactive",
                sync_status="succeeded" if is_active else "pending",
                sync_version=1 if generation in {"legacy_with_user_task", "non_initial_no_task"} else 7,
                last_event_id=event_id,
            )
            db.add(grant)
            await db.flush()
            if generation == "legacy_with_user_task":
                db.add(
                    BusinessTaskModel(
                        tenant_id=provision.tenant_id,
                        created_id=1,
                        updated_id=1,
                        handler_code="control.user_entitlement_sync",
                        module="control",
                        biz_type="user_entitlement",
                        biz_id=str(grant.id),
                        title="已存在的人工撤权",
                        payload={"grant_id": grant.id, "event_id": "queued", "sync_version": 2, "mode": "revoke"},
                        queue="default",
                        idempotency_key=f"existing-user-command-{uuid4().hex}",
                        status="pending",
                        progress=0,
                        attempt=0,
                        max_retries=2,
                        trace_id=uuid4().hex,
                    )
                )
            await db.commit()
            return grant.id, event_id, grant.sync_version, grant.desired_state, grant.sync_status

    grant_id, event_id, version, desired_state, sync_status = asyncio.run(seed_generation())

    def target(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "result": "already_exists",
                    "target_tenant_id": 88,
                    "target_tenant_uuid": str(uuid4()),
                    "target_tenant_code": "generation-target",
                }
            },
        )

    monkeypatch.setattr(ControlTenantProvisionWorkerService, "transport", httpx.MockTransport(target))
    outcome = asyncio.run(BusinessTaskExecutor(session_factory=async_db_session).execute(task_id))
    assert outcome.status == "success"

    async def assert_generation() -> None:
        async with async_db_session() as db:
            grant = await db.get(ControlUserApplicationGrantModel, grant_id)
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            if generation == "same_bootstrap":
                assert grant.desired_state == "active"
                assert grant.sync_status == "succeeded"
                assert grant.sync_version == version
                assert grant.last_event_id == provision.provision_request_uuid
            else:
                assert (grant.desired_state, grant.sync_status) == (desired_state, sync_status)
                assert grant.sync_version == version
                assert grant.last_event_id == event_id

    asyncio.run(assert_generation())


@pytest.mark.parametrize("failure", ["twice_503", "business_409"])
def test_worker_stops_after_retry_budget_or_non_retryable_business_error(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    del test_client
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case())
    calls = 0

    def target(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status_code = 503 if failure == "twice_503" else 409
        return httpx.Response(status_code, json={"msg": "目标业务错误"})

    monkeypatch.setattr(ControlTenantProvisionWorkerService, "transport", httpx.MockTransport(target))
    executor = BusinessTaskExecutor(session_factory=async_db_session)
    first = asyncio.run(executor.execute(task_id))
    if failure == "twice_503":
        assert first.status == "retrying"
        final = asyncio.run(executor.execute(task_id))
        assert final.status == "failed"
        assert calls == 2
    else:
        assert first.status == "failed"
        assert calls == 1

    async def assert_failed() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            assert provision.status == "failed"
            assert provision.attempt_count == (2 if failure == "twice_503" else 1)
            assert provision.last_error_code == ("TARGET_HTTP_503" if failure == "twice_503" else "TARGET_HTTP_409")

    asyncio.run(assert_failed())


def test_recovered_task_does_not_leave_domain_pending_when_runtime_retry_budget_is_spent(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del test_client
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case(task_attempt=1))

    def target(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"msg": "temporary"})

    monkeypatch.setattr(ControlTenantProvisionWorkerService, "transport", httpx.MockTransport(target))
    outcome = asyncio.run(BusinessTaskExecutor(session_factory=async_db_session).execute(task_id))
    assert outcome.status == "failed"

    async def assert_domain_failed() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            assert provision.status == "failed"
            assert provision.next_retry_at is None

    asyncio.run(assert_domain_failed())


def test_unexpected_worker_error_is_recorded_as_safe_terminal_failure(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del test_client
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case())

    async def fail_unexpectedly(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("password=unexpected-secret")

    monkeypatch.setattr(ControlTenantProvisionWorkerService, "_call_target", fail_unexpectedly)
    outcome = asyncio.run(BusinessTaskExecutor(session_factory=async_db_session).execute(task_id))
    assert outcome.status == "failed"

    async def assert_safe_failure() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            assert provision.status == "failed"
            assert provision.last_error_code == "UNEXPECTED_ERROR"
            assert provision.last_error_message == "租户自动开户执行异常"
            assert "unexpected-secret" not in (provision.last_error_message or "")

    asyncio.run(assert_safe_failure())


def test_stale_execution_token_cannot_overwrite_newer_success(test_client: TestClient) -> None:
    del test_client
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionWorkerService
    from app.api.v1.module_control.tenant_provision.task_schema import ControlTargetProvisionResult
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case())

    async def exercise() -> None:
        async with async_db_session() as old_db, async_db_session() as new_db:
            admin = await old_db.get(UserModel, 1)
            assert admin is not None
            old_context = BusinessTaskContext(
                task_id=task_id,
                tenant_id=1,
                actor_user_id=1,
                trace_id="old-trace",
                execution_token="old-token",
                db=old_db,
                auth=AuthSchema(db=old_db, user=admin, tenant_id=1, site_id=1),
                session_factory=async_db_session,
            )
            newer_admin = await new_db.get(UserModel, 1)
            assert newer_admin is not None
            new_context = BusinessTaskContext(
                task_id=task_id,
                tenant_id=1,
                actor_user_id=1,
                trace_id="new-trace",
                execution_token="new-token",
                db=new_db,
                auth=AuthSchema(db=new_db, user=newer_admin, tenant_id=1, site_id=1),
                session_factory=async_db_session,
            )
            old_worker = ControlTenantProvisionWorkerService(old_context)
            new_worker = ControlTenantProvisionWorkerService(new_context)
            assert await old_worker._start_attempt(provision_id) is not None
            assert await new_worker._start_attempt(provision_id) is not None
            await new_worker._record_success(
                provision_id,
                ControlTargetProvisionResult(
                    result="created",
                    target_tenant_id=88,
                    target_tenant_uuid=str(uuid4()),
                    target_tenant_code="remote-new",
                ),
            )
            await new_db.commit()
            await old_worker._record_failure(
                provision_id,
                code="TARGET_TIMEOUT",
                message="旧执行超时",
                will_retry=False,
            )

        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            assert provision.status == "succeeded"
            assert provision.target_tenant_code == "remote-new"
            task = await db.get(BusinessTaskModel, task_id)
            assert task is not None
            task.status = "canceled"
            await db.commit()

    asyncio.run(exercise())


def test_exhausted_processing_attempt_is_closed_as_failed(test_client: TestClient) -> None:
    del test_client
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case())

    async def exhaust_attempts() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            provision.status = "processing"
            provision.attempt_count = provision.max_attempts
            await db.commit()

    asyncio.run(exhaust_attempts())
    outcome = asyncio.run(BusinessTaskExecutor(session_factory=async_db_session).execute(task_id))
    assert outcome.status == "failed"

    async def assert_closed() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            assert provision.status == "failed"
            assert provision.last_error_code == "ATTEMPTS_EXHAUSTED"

    asyncio.run(assert_closed())


def test_manual_retry_recovers_processing_record_without_active_business_task_lease(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del test_client
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionService
    from app.core.database import async_db_session

    provision_id, task_id = asyncio.run(_seed_worker_case())
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    monkeypatch.setattr("app.api.v1.module_control.tenant_provision.service.settings.CELERY_ENABLED", True)

    async def exercise() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            task = await db.get(BusinessTaskModel, task_id)
            assert provision is not None and task is not None
            provision.status = "processing"
            provision.attempt_count = 1
            provision.active_execution_token = "active-token"
            task.status = "running"
            task.execution_token = "active-token"
            task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
            await db.commit()
        async with async_db_session() as db:
            admin = await db.get(UserModel, 1)
            assert admin is not None
            service = ControlTenantProvisionService(AuthSchema(db=db, user=admin, tenant_id=1, site_id=1))
            with pytest.raises(CustomException, match="仍在执行"):
                await service.dispatch_action(provision_id, "retry")
            await db.rollback()
        async with async_db_session() as db:
            task = await db.get(BusinessTaskModel, task_id)
            assert task is not None
            task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        async with async_db_session() as db:
            admin = await db.get(UserModel, 1)
            assert admin is not None
            service = ControlTenantProvisionService(AuthSchema(db=db, user=admin, tenant_id=1, site_id=1))
            retry_task = await service.dispatch_action(provision_id, "retry")
            assert retry_task.max_retries == 0
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            assert provision.status == "pending"
            assert provision.attempt_count == 0
            tasks = (
                await db.execute(select(BusinessTaskModel).where(BusinessTaskModel.biz_id == str(provision_id)))
            ).scalars().all()
            for task in tasks:
                task.status = "canceled"
            await db.commit()

    asyncio.run(exercise())


def test_manual_retry_and_reconcile_dispatch_single_attempt_unique_tasks(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del test_client
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionService
    from app.core.database import async_db_session

    provision_id, _task_id = asyncio.run(_seed_worker_case())
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def exercise() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            provision.status = "failed"
            provision.attempt_count = 2
            await db.commit()
        async with async_db_session() as db:
            admin = await db.get(UserModel, 1)
            auth = AuthSchema(db=db, user=admin, tenant_id=1, site_id=1)
            service = ControlTenantProvisionService(auth)
            retry_task = await service.dispatch_action(provision_id, "retry")
            await db.commit()
            assert retry_task.max_retries == 0
            assert retry_task.idempotency_key is not None and ":retry:" in retry_task.idempotency_key
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            provision.status = "failed"
            provision.attempt_count = 1
            await db.commit()
        async with async_db_session() as db:
            admin = await db.get(UserModel, 1)
            auth = AuthSchema(db=db, user=admin, tenant_id=1, site_id=1)
            service = ControlTenantProvisionService(auth)
            reconcile_task = await service.dispatch_action(provision_id, "reconcile")
            await db.commit()
            assert reconcile_task.max_retries == 0
            assert reconcile_task.idempotency_key is not None and ":reconcile:" in reconcile_task.idempotency_key
            assert retry_task.idempotency_key != reconcile_task.idempotency_key
            tasks = (
                await db.execute(select(BusinessTaskModel).where(BusinessTaskModel.biz_id == str(provision_id)))
            ).scalars().all()
            for task in tasks:
                task.status = "canceled"
            await db.commit()

    asyncio.run(exercise())


def test_succeeded_provision_rejects_update_and_retry_and_site_boundary_is_enforced(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del test_client
    from app.api.v1.module_control.tenant_provision.schema import ControlTenantProvisionUpdateSchema
    from app.api.v1.module_control.tenant_provision.service import ControlTenantProvisionService
    from app.core.database import async_db_session

    provision_id, _task_id = asyncio.run(_seed_worker_case())
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def exercise() -> None:
        async with async_db_session() as db:
            provision = await db.get(ControlTenantProvisionModel, provision_id)
            assert provision is not None
            provision.status = "succeeded"
            await db.commit()
        async with async_db_session() as db:
            admin = await db.get(UserModel, 1)
            service = ControlTenantProvisionService(AuthSchema(db=db, user=admin, tenant_id=1, site_id=1))
            with pytest.raises(CustomException, match="只有失败"):
                await service.update(provision_id, ControlTenantProvisionUpdateSchema(desired_target_tenant_code="newcode"))
            with pytest.raises(CustomException, match="不能重试"):
                await service.dispatch_action(provision_id, "retry")
        async with async_db_session() as db:
            admin = await db.get(UserModel, 1)
            cross_site = ControlTenantProvisionService(AuthSchema(db=db, user=admin, tenant_id=1, site_id=999))
            with pytest.raises(CustomException, match="其他站点"):
                await cross_site.update(
                    provision_id,
                    ControlTenantProvisionUpdateSchema(desired_target_tenant_code="newcode"),
                )
        async with async_db_session() as db:
            tasks = (
                await db.execute(select(BusinessTaskModel).where(BusinessTaskModel.biz_id == str(provision_id)))
            ).scalars().all()
            for task in tasks:
                task.status = "canceled"
            await db.commit()

    asyncio.run(exercise())
