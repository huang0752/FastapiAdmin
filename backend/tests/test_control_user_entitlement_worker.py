"""Control user-entitlement command and worker contracts."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update

from app.api.v1.module_control.model import (
    ControlApplicationModel,
    ControlTenantApplicationModel,
    ControlUserApplicationGrantModel,
)
from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.plugin.module_task.business.task.model import BusinessTaskModel
from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher
from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
from app.utils.hash_bcrpy_util import PwdUtil


@dataclass
class _RecordingPublisher:
    messages: list[dict] = field(default_factory=list)

    async def publish(self, **message) -> None:
        self.messages.append(message)


async def _seed_case(
    *,
    desired_state: str = "inactive",
    sync_status: str = "succeeded",
    site_id: int | None = None,
) -> dict[str, int]:
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    async with async_db_session() as db:
        if site_id is None:
            site = SiteModel(code=f"worker_{suffix}", name=f"Worker {suffix}", status=0)
            db.add(site)
            await db.flush()
            site_id = site.id
        tenant = TenantModel(
            name=f"Worker tenant {suffix}",
            code=f"wrk{suffix}",
            site_id=site_id,
            package_id=1,
            status=0,
        )
        user = UserModel(
            tenant_id=1,
            username=f"worker_{suffix}",
            password=PwdUtil.hash_password("test-password"),
            name="Worker User",
            status=0,
        )
        application = ControlApplicationModel(
            site_id=site_id,
            code=f"wms{suffix}",
            name="Worker target",
            base_url="https://target.example.test",
            callback_url="https://target.example.test/callback",
            client_id=f"worker-client-{suffix}",
            client_secret_hash=PwdUtil.hash_password(f"secret-{suffix}"),
            entitlement_sync_url="https://target.example.test/api/v1/system/auth/control/access/sync",
            entitlement_sync_enabled=True,
            entitlement_sync_timeout_seconds=3,
            status=0,
        )
        db.add_all([tenant, user, application])
        await db.flush()
        db.add(TenantUserModel(user_id=user.id, tenant_id=tenant.id, role="member", is_default=1))
        opening = ControlTenantApplicationModel(
            site_id=site_id,
            tenant_id=tenant.id,
            application_id=application.id,
            target_tenant_code=f"target{suffix}",
            status=0,
        )
        db.add(opening)
        await db.flush()
        grant = ControlUserApplicationGrantModel(
            site_id=site_id,
            tenant_application_id=opening.id,
            tenant_id=tenant.id,
            user_id=user.id,
            desired_state=desired_state,
            status=0 if desired_state == "active" else 1,
            sync_status=sync_status,
            sync_version=1,
            last_event_id=f"seed-{uuid4().hex}",
        )
        db.add(grant)
        await db.commit()
        return {
            "application_id": application.id,
            "grant_id": grant.id,
            "site_id": site_id,
            "tenant_id": tenant.id,
            "user_id": user.id,
        }


async def _admin_auth(site_id: int):
    from app.core.database import async_db_session

    db = async_db_session()
    admin = (await db.execute(select(UserModel).where(UserModel.username == "admin"))).scalar_one()
    return db, AuthSchema(db=db, user=admin, tenant_id=1, site_id=site_id)


async def _tenant_actor_auth(case: dict[str, int], permissions: list[str]):
    from app.api.v1.module_platform.menu.model import MenuModel
    from app.api.v1.module_platform.package.model import PackageMenuModel
    from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
    from app.api.v1.module_system.user.model import UserRolesModel
    from app.core.database import async_db_session

    suffix = uuid4().hex[:10]
    async with async_db_session() as setup_db:
        menus = [
            MenuModel(
                name=f"Task8 permission {suffix}-{index}",
                type=3,
                order=999,
                permission=permission,
                scope="tenant",
                status=0,
            )
            for index, permission in enumerate(permissions)
        ]
        role = RoleModel(
            tenant_id=case["tenant_id"],
            name=f"Task8 admin {suffix}",
            code=f"TASK8_{suffix}",
            status=0,
        )
        setup_db.add_all([*menus, role])
        await setup_db.flush()
        setup_db.add(UserRolesModel(user_id=case["user_id"], role_id=role.id))
        for menu in menus:
            setup_db.add_all(
                [
                    RoleMenusModel(role_id=role.id, menu_id=menu.id),
                    PackageMenuModel(package_id=1, menu_id=menu.id),
                ]
            )
        await setup_db.commit()

    db = async_db_session()
    user = (await db.execute(select(UserModel).where(UserModel.id == case["user_id"]))).scalar_one()
    return db, AuthSchema(
        db=db,
        user=user,
        tenant_id=case["tenant_id"],
        site_id=case["site_id"],
    )


def _success_transport(*, status: str = "active", disposition: str = "applied", version: int = 2):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://target.example.test/api/v1/system/auth/control/access/sync"
        assert request.headers["accept-encoding"] == "identity"
        assert set(json.loads(request.content)) == {"code"}
        return httpx.Response(
            200,
            json={
                "code": 10000,
                "data": {
                    "disposition": disposition,
                    "status": status,
                    "applied_version": version,
                    "local_user_id": 99 if status == "active" else None,
                    "role_codes": ["USER"] if status == "active" else [],
                    "effective_menu_count": 3 if status == "active" else 0,
                    "session_cleanup_pending": False,
                },
                "msg": "ok",
            },
        )

    return httpx.MockTransport(handler)


async def _park_entitlement_task(
    case: dict[str, int],
    monkeypatch,
    *,
    grant_status: str = "pending",
    active_execution_token: str | None = None,
) -> int:
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.core.database import async_db_session

    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()
    async with async_db_session() as setup_db:
        await setup_db.execute(
            update(BusinessTaskModel)
            .where(BusinessTaskModel.id == task.id)
            .values(
                status="failed",
                error_code="DOMAIN_CLOSURE_PENDING",
                error="业务失败状态自动收口超过限额，需要显式重试",
                execution_token=None,
                created_id=case["user_id"],
            )
        )
        await setup_db.execute(
            update(ControlUserApplicationGrantModel)
            .where(ControlUserApplicationGrantModel.id == case["grant_id"])
            .values(
                sync_status=grant_status,
                active_execution_token=active_execution_token,
            )
        )
        await setup_db.commit()
    return task.id


@pytest.mark.asyncio
async def test_command_prepares_grant_and_outbox_without_publishing_before_commit(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        publish_entitlement_tasks,
    )
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    publisher = _RecordingPublisher()
    dispatcher = BusinessTaskDispatcher(publisher=publisher)
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth, dispatcher=dispatcher).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        assert publisher.messages == []
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert (grant.desired_state, grant.sync_status, grant.sync_version) == ("active", "pending", 2)
        assert task.payload == {
            "grant_id": grant.id,
            "event_id": grant.last_event_id,
            "sync_version": 2,
            "mode": "grant",
        }
        task_id = task.id
        await db.commit()
    finally:
        await db.close()

    await publish_entitlement_tasks([task_id], dispatcher=dispatcher)
    assert [item["business_task_id"] for item in publisher.messages] == [task_id]


@pytest.mark.asyncio
async def test_command_rollback_removes_grant_transition_and_outbox(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        task_id = task.id
        await db.rollback()
    finally:
        await db.close()

    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert (grant.desired_state, grant.sync_status, grant.sync_version) == ("inactive", "succeeded", 1)
        assert await check_db.get(BusinessTaskModel, task_id) is None


@pytest.mark.asyncio
async def test_retry_only_accepts_current_failed_generation(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case(desired_state="active", sync_status="failed")
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _admin_auth(case["site_id"])
    try:
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        old_event = grant.last_event_id
        task = await ControlUserEntitlementCommandService(auth).retry(case["grant_id"])
        assert task.payload["sync_version"] == 2
        assert task.payload["event_id"] != old_event
        assert grant.sync_version == 2
        assert grant.sync_status == "pending"
        with pytest.raises(CustomException, match="只有失败"):
            await ControlUserEntitlementCommandService(auth).retry(case["grant_id"])
    finally:
        await db.rollback()
        await db.close()


@pytest.mark.asyncio
async def test_revoke_supersedes_processing_grant_and_old_worker_cannot_write_back(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _admin_auth(case["site_id"])
    try:
        service = ControlUserEntitlementCommandService(auth)
        old_task = await service.set_desired_state(case["grant_id"], "active", mode="grant")
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        grant.sync_status = "processing"
        grant.active_execution_token = "old-token"
        current_task = await service.set_desired_state(case["grant_id"], "inactive", mode="revoke")
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(old_task.id)
    assert outcome.status == "success"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert (grant.desired_state, grant.sync_status, grant.sync_version) == ("inactive", "pending", 3)
        current = await check_db.get(BusinessTaskModel, current_task.id)
        current.status = "canceled"
        await check_db.commit()


@pytest.mark.asyncio
async def test_worker_applies_active_result_and_clears_execution_token(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    ControlUserEntitlementWorkerService.transport = _success_transport()
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    assert outcome.status == "success"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert grant.sync_status == "succeeded"
        assert grant.last_synced_at is not None
        assert grant.active_execution_token is None


@pytest.mark.asyncio
async def test_current_generation_cannot_remain_processing_when_target_is_ahead(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    ControlUserEntitlementWorkerService.transport = _success_transport(
        disposition="superseded",
        version=3,
    )
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    assert outcome.status == "failed"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert grant.sync_status == "failed"
        assert grant.active_execution_token is None
        assert grant.last_error_code == "TARGET_VERSION_AHEAD"


@pytest.mark.asyncio
async def test_worker_retries_timeout(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(timeout)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    assert outcome.status == "retrying"
    async with async_db_session() as cleanup_db:
        persisted = await cleanup_db.get(BusinessTaskModel, task.id)
        persisted.status = "failed"
        await cleanup_db.commit()


@pytest.mark.asyncio
async def test_old_worker_failure_after_new_generation_is_superseded(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def revoke_during_call(_request: httpx.Request) -> httpx.Response:
        async with async_db_session() as mutation_db:
            grant = await mutation_db.get(ControlUserApplicationGrantModel, case["grant_id"])
            grant.sync_version += 1
            grant.last_event_id = uuid4().hex
            grant.desired_state = "inactive"
            grant.status = 1
            grant.sync_status = "pending"
            grant.active_execution_token = None
            await mutation_db.commit()
        return httpx.Response(503, json={"detail": "temporarily unavailable"})

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(revoke_during_call)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    assert outcome.status == "success"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert (grant.desired_state, grant.sync_status, grant.sync_version) == ("inactive", "pending", 3)


@pytest.mark.asyncio
async def test_ticket_issue_failure_closes_current_generation_as_failed(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def fail_issue(*_args, **_kwargs):
        raise RuntimeError("ticket store unavailable")

    monkeypatch.setattr(
        "app.api.v1.module_control.user_entitlement.service.ControlUserEntitlementTicketService.issue",
        fail_issue,
    )
    ControlUserEntitlementWorkerService.transport = _success_transport()
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    assert outcome.status == "failed"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert grant.sync_status == "failed"
        assert grant.last_error_code == "UNEXPECTED_ERROR"
        assert grant.active_execution_token is None


@pytest.mark.asyncio
async def test_prestart_database_failure_does_not_report_superseded_or_leave_pending(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def fail_preflight(*_args, **_kwargs):
        raise RuntimeError("one-shot database failure")

    monkeypatch.setattr(ControlUserEntitlementWorkerService, "_load_target", fail_preflight)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    assert outcome.status == "failed"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant is not None
        assert grant.sync_status == "failed"
        assert grant.last_error_code == "UNEXPECTED_ERROR"


@pytest.mark.asyncio
async def test_tenant_grant_admin_can_command_and_run_only_its_grant(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    other = await _seed_case()
    same_site_other_tenant = await _seed_case(site_id=case["site_id"])
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    ControlUserEntitlementWorkerService.transport = _success_transport()
    db, auth = await _tenant_actor_auth(case, ["module_control:user_grant:update"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        assert task.tenant_id == case["tenant_id"]
        with pytest.raises(CustomException, match="其他租户或站点"):
            await ControlUserEntitlementCommandService(auth).set_desired_state(
                other["grant_id"], "active", mode="grant"
            )
        with pytest.raises(CustomException, match="其他租户或站点"):
            await ControlUserEntitlementCommandService(auth).set_desired_state(
                same_site_other_tenant["grant_id"], "active", mode="grant"
            )
        await db.commit()
    finally:
        await db.close()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    assert outcome.status == "success"


@pytest.mark.asyncio
async def test_tenant_actor_releases_permission_connection_before_blocking_http(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session, async_engine
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    entered = asyncio.Event()
    release = asyncio.Event()
    checked_out_at_http: int | None = None
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def blocking_target(_request: httpx.Request) -> httpx.Response:
        nonlocal checked_out_at_http
        checked_out_at_http = async_engine.pool.checkedout()
        entered.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "code": 10000,
                "data": {
                    "disposition": "applied",
                    "status": "active",
                    "applied_version": 2,
                    "local_user_id": 99,
                    "role_codes": ["USER"],
                    "effective_menu_count": 3,
                    "session_cleanup_pending": False,
                },
                "msg": "ok",
            },
        )

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(blocking_target)
    db, auth = await _tenant_actor_auth(case, ["module_control:user_grant:update"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    baseline = async_engine.pool.checkedout()
    execution = asyncio.create_task(BusinessTaskExecutor(session_factory=async_db_session).execute(task.id))
    await asyncio.wait_for(entered.wait(), timeout=2)
    try:
        assert checked_out_at_http == baseline
    finally:
        release.set()
    assert (await asyncio.wait_for(execution, timeout=2)).status == "success"


@pytest.mark.parametrize("revocation", ["permission", "membership", "package"])
@pytest.mark.asyncio
async def test_actor_revocation_closes_current_grant_generation(test_client, monkeypatch, revocation) -> None:
    from sqlalchemy import delete

    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.api.v1.module_platform.package.model import PackageModel
    from app.api.v1.module_system.role.model import RoleMenusModel
    from app.api.v1.module_system.user.model import UserRolesModel
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _tenant_actor_auth(case, ["module_control:user_grant:update"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    try:
        async with async_db_session() as revoke_db:
            if revocation == "permission":
                role_ids = select(UserRolesModel.role_id).where(UserRolesModel.user_id == case["user_id"])
                await revoke_db.execute(delete(RoleMenusModel).where(RoleMenusModel.role_id.in_(role_ids)))
            elif revocation == "membership":
                await revoke_db.execute(
                    delete(TenantUserModel).where(
                        TenantUserModel.user_id == case["user_id"],
                        TenantUserModel.tenant_id == case["tenant_id"],
                    )
                )
            else:
                package = await revoke_db.get(PackageModel, 1)
                package.status = 1
            await revoke_db.commit()

        outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
        assert outcome.status == "failed"
        async with async_db_session() as check_db:
            grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
            assert grant.sync_status == "failed"
            assert grant.last_error_code == "ACTOR_INVALID"
    finally:
        if revocation == "package":
            async with async_db_session() as restore_db:
                package = await restore_db.get(PackageModel, 1)
                package.status = 0
                await restore_db.commit()


@pytest.mark.asyncio
async def test_retry_mode_requires_retry_permission_not_update(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case(sync_status="failed")
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    ControlUserEntitlementWorkerService.transport = _success_transport(status="inactive")
    db, auth = await _tenant_actor_auth(case, ["module_control:user_grant:retry"])
    try:
        task = await ControlUserEntitlementCommandService(auth).retry(case["grant_id"])
        await db.commit()
    finally:
        await db.close()

    assert (await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)).status == "success"


@pytest.mark.parametrize("failure_point", ["build_auth", "permission", "release"])
@pytest.mark.asyncio
async def test_preflight_database_failure_retries_then_closes_grant(
    test_client,
    monkeypatch,
    failure_point,
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _tenant_actor_auth(case, ["module_control:user_grant:update"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    async def database_failure(*_args, **_kwargs):
        raise OperationalError("SELECT preflight", {}, RuntimeError("database unavailable"))

    if failure_point == "build_auth":
        monkeypatch.setattr("app.plugin.module_task.runtime.executor.build_background_auth", database_failure)
    elif failure_point == "permission":
        monkeypatch.setattr(
            "app.api.v1.module_control.user_entitlement.service.resolve_effective_permissions",
            database_failure,
        )
    else:
        monkeypatch.setattr(
            "app.api.v1.module_control.user_entitlement.service.ControlUserEntitlementWorkerService._release_context_db",
            database_failure,
        )

    executor = BusinessTaskExecutor(session_factory=async_db_session)
    assert [
        (await executor.execute(task.id)).status,
        (await executor.execute(task.id)).status,
        (await executor.execute(task.id)).status,
    ] == ["retrying", "retrying", "failed"]
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.sync_status == "failed"
        assert grant.active_execution_token is None


@pytest.mark.asyncio
async def test_superseded_task_with_revoked_actor_is_a_successful_noop(test_client, monkeypatch) -> None:
    from sqlalchemy import delete

    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _tenant_actor_auth(case, ["module_control:user_grant:update"])
    try:
        old_task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
        new_task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "inactive", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    async with async_db_session() as revoke_db:
        await revoke_db.execute(
            delete(TenantUserModel).where(
                TenantUserModel.user_id == case["user_id"],
                TenantUserModel.tenant_id == case["tenant_id"],
            )
        )
        await revoke_db.commit()

    assert (await BusinessTaskExecutor(session_factory=async_db_session).execute(old_task.id)).status == "success"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.sync_status == "pending"
        assert grant.sync_version == 3
        current_task = await check_db.get(BusinessTaskModel, new_task.id)
        current_task.status = "failed"
        await check_db.commit()


@pytest.mark.asyncio
async def test_release_context_db_closes_checked_out_connection_when_rollback_fails(test_client, monkeypatch) -> None:
    from types import SimpleNamespace

    from sqlalchemy.exc import OperationalError

    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementWorkerService
    from app.core.database import async_db_session, async_engine

    _ = test_client
    baseline = async_engine.pool.checkedout()
    db = async_db_session()
    await db.execute(select(UserModel.id).limit(1))
    assert async_engine.pool.checkedout() == baseline + 1

    async def rollback_failure():
        raise OperationalError("ROLLBACK", {}, RuntimeError("connection lost"))

    monkeypatch.setattr(db, "rollback", rollback_failure)
    service = ControlUserEntitlementWorkerService(
        SimpleNamespace(auth=AuthSchema(db=db, user=None, tenant_id=1))
    )
    with pytest.raises(OperationalError):
        await service._release_context_db()
    assert async_engine.pool.checkedout() == baseline


@pytest.mark.asyncio
async def test_domain_closure_failure_remains_recoverable_after_work_retry_budget(
    test_client,
    monkeypatch,
) -> None:
    from sqlalchemy import delete

    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    from app.api.v1.module_control.user_entitlement.task_schema import ControlUserEntitlementTaskPayload
    from app.core.database import async_db_session
    from app.plugin.module_control_provision.entitlement_handlers import sync_user_entitlement
    from app.plugin.module_task.runtime.registry import BusinessTaskRegistry

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _tenant_actor_auth(case, ["module_control:user_grant:update"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    async with async_db_session() as revoke_db:
        await revoke_db.execute(
            delete(TenantUserModel).where(
                TenantUserModel.user_id == case["user_id"],
                TenantUserModel.tenant_id == case["tenant_id"],
            )
        )
        await revoke_db.commit()

    async def closure_database_failure(*_args, **_kwargs):
        raise RuntimeError("closure database unavailable")

    registry = BusinessTaskRegistry()
    registry.register(
        handler_code="control.user_entitlement_sync",
        handler=sync_user_entitlement,
        module="control",
        max_retries=2,
        retry_backoff_seconds=10,
        payload_schema=ControlUserEntitlementTaskPayload,
        release_context_db_before_handler=True,
        pre_handler_failure=closure_database_failure,
    )
    executor = BusinessTaskExecutor(registry=registry, session_factory=async_db_session)
    outcomes = [await executor.execute(task.id) for _ in range(6)]
    assert [outcome.status for outcome in outcomes] == ["retrying"] * 5 + ["failed"]
    async with async_db_session() as check_db:
        persisted = await check_db.get(BusinessTaskModel, task.id)
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert persisted.status == "failed"
        assert persisted.error_code == "DOMAIN_CLOSURE_PENDING"
        assert grant.sync_status == "pending"

    from app.plugin.module_control_provision.entitlement_handlers import close_user_entitlement_before_handler

    recovery_registry = BusinessTaskRegistry()
    recovery_registry.register(
        handler_code="control.user_entitlement_sync",
        handler=sync_user_entitlement,
        module="control",
        max_retries=2,
        retry_backoff_seconds=10,
        payload_schema=ControlUserEntitlementTaskPayload,
        release_context_db_before_handler=True,
        pre_handler_failure=close_user_entitlement_before_handler,
    )
    recovered = await BusinessTaskExecutor(
        registry=recovery_registry,
        session_factory=async_db_session,
    ).reconcile_domain_closure(task.id)
    assert recovered.status == "closed"
    repeated = await BusinessTaskExecutor(
        registry=recovery_registry,
        session_factory=async_db_session,
    ).reconcile_domain_closure(task.id)
    assert repeated.status == "noop"
    async with async_db_session() as check_db:
        persisted = await check_db.get(BusinessTaskModel, task.id)
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert persisted.error_code == "DOMAIN_CLOSURE_FAILED"
        assert grant.sync_status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("grant_status", "active_token", "expected_status"),
    [
        ("pending", None, "closed"),
        ("processing", "abandoned-worker-token", "closed"),
        ("failed", None, "already_closed"),
    ],
)
async def test_parked_reconciliation_atomically_closes_same_generation(
    test_client,
    monkeypatch,
    grant_status,
    active_token,
    expected_status,
) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    task_id = await _park_entitlement_task(
        case,
        monkeypatch,
        grant_status=grant_status,
        active_execution_token=active_token,
    )
    outcome = await BusinessTaskExecutor(session_factory=async_db_session).reconcile_domain_closure(
        task_id,
        tenant_id=case["tenant_id"],
        site_id=case["site_id"],
    )
    assert outcome.status == expected_status
    async with async_db_session() as check_db:
        task = await check_db.get(BusinessTaskModel, task_id)
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert task.status == "failed"
        assert task.error_code == "DOMAIN_CLOSURE_FAILED"
        assert grant.sync_status == "failed"
        assert grant.active_execution_token is None


@pytest.mark.asyncio
async def test_concurrent_parked_reconciliation_is_serialized_and_idempotent(
    test_client,
    monkeypatch,
) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    task_id = await _park_entitlement_task(case, monkeypatch)
    outcomes = await asyncio.gather(
        *(
            BusinessTaskExecutor(session_factory=async_db_session).reconcile_domain_closure(
                task_id,
                tenant_id=case["tenant_id"],
                site_id=case["site_id"],
            )
            for _ in range(2)
        )
    )
    assert sorted(outcome.status for outcome in outcomes) == ["closed", "noop"]


@pytest.mark.asyncio
async def test_parked_reconciliation_rejects_wrong_tenant_without_mutation(
    test_client,
    monkeypatch,
) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    other_case = await _seed_case()
    task_id = await _park_entitlement_task(case, monkeypatch)
    outcome = await BusinessTaskExecutor(session_factory=async_db_session).reconcile_domain_closure(
        task_id,
        tenant_id=other_case["tenant_id"],
        site_id=case["site_id"],
    )
    assert outcome.status == "noop"
    async with async_db_session() as check_db:
        task = await check_db.get(BusinessTaskModel, task_id)
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert task.error_code == "DOMAIN_CLOSURE_PENDING"
        assert grant.sync_status == "pending"


@pytest.mark.asyncio
async def test_parked_reconciliation_marks_newer_generation_superseded_without_success(
    test_client,
    monkeypatch,
) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    task_id = await _park_entitlement_task(case, monkeypatch)
    async with async_db_session() as db:
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        grant.sync_version += 1
        grant.last_event_id = uuid4().hex
        grant.sync_status = "pending"
        await db.commit()

    outcome = await BusinessTaskExecutor(session_factory=async_db_session).reconcile_domain_closure(
        task_id,
        tenant_id=case["tenant_id"],
        site_id=case["site_id"],
    )
    assert outcome.status == "superseded"
    async with async_db_session() as check_db:
        task = await check_db.get(BusinessTaskModel, task_id)
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert task.status == "failed"
        assert task.error_code == "DOMAIN_CLOSURE_SUPERSEDED"
        assert grant.sync_status == "pending"


@pytest.mark.asyncio
async def test_business_task_retry_operator_reconciles_parked_grant_without_product_http(
    test_client,
    monkeypatch,
) -> None:
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementWorkerService
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401
    from app.plugin.module_task.business.task.service import BusinessTaskService

    _ = test_client
    case = await _seed_case()
    task_id = await _park_entitlement_task(
        case,
        monkeypatch,
        grant_status="processing",
        active_execution_token="old",
    )
    called = False

    async def forbidden_http(_request):
        nonlocal called
        called = True
        raise AssertionError("parked reconciliation must not call product HTTP")

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(forbidden_http)
    generic_db, generic_auth = await _tenant_actor_auth(
        case,
        ["module_task:business_task:retry"],
    )
    try:
        with pytest.raises(CustomException) as exc_info:
            await BusinessTaskService(generic_auth).retry(task_id)
        assert exc_info.value.status_code == 403
    finally:
        await generic_db.close()
    async with async_db_session() as check_db:
        task = await check_db.get(BusinessTaskModel, task_id)
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert task.error_code == "DOMAIN_CLOSURE_PENDING"
        assert grant.sync_status == "processing"
        assert grant.active_execution_token == "old"

    other_site = await _seed_case()
    wrong_site_db, wrong_site_auth = await _tenant_actor_auth(
        {**case, "site_id": other_site["site_id"]},
        ["module_task:business_task:retry", "module_control:user_grant:retry"],
    )
    try:
        with pytest.raises(CustomException) as exc_info:
            await BusinessTaskService(wrong_site_auth).retry(task_id)
        assert exc_info.value.status_code == 403
    finally:
        await wrong_site_db.close()

    db, auth = await _tenant_actor_auth(
        case,
        ["module_task:business_task:retry", "module_control:user_grant:retry"],
    )
    try:
        action = await BusinessTaskService(auth).retry(task_id)
    finally:
        await db.close()
    assert action.status == "failed"
    assert called is False
    async with async_db_session() as check_db:
        task = await check_db.get(BusinessTaskModel, task_id)
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert task.error_code == "DOMAIN_CLOSURE_FAILED"
        assert grant.sync_status == "failed"
        assert grant.active_execution_token is None


@pytest.mark.asyncio
async def test_platform_superuser_keeps_parked_entitlement_retry_bypass(
    test_client,
    monkeypatch,
) -> None:
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401
    from app.plugin.module_task.business.task.service import BusinessTaskService

    _ = test_client
    case = await _seed_case()
    task_id = await _park_entitlement_task(case, monkeypatch)
    db, auth = await _admin_auth(case["site_id"])
    try:
        action = await BusinessTaskService(auth).retry(task_id)
    finally:
        await db.close()
    assert action.status == "failed"


@pytest.mark.asyncio
async def test_non_entitlement_retry_does_not_require_user_grant_permission(
    test_client,
    monkeypatch,
) -> None:
    from app.core.database import async_db_session
    from app.plugin.module_task.business.task.service import BusinessTaskService
    from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher

    _ = test_client
    case = await _seed_case()
    task_id = await _park_entitlement_task(case, monkeypatch)
    async with async_db_session() as setup_db:
        await setup_db.execute(
            update(BusinessTaskModel)
            .where(BusinessTaskModel.id == task_id)
            .values(
                handler_code="sample.non_entitlement",
                error_code="BUSINESS_TASK_FAILED",
                attempt=0,
                max_retries=1,
            )
        )
        await setup_db.commit()

    called = False

    async def retry_existing(_self, *, task_id, tenant_id):
        nonlocal called
        called = True
        async with async_db_session() as db:
            return await db.get(BusinessTaskModel, task_id)

    monkeypatch.setattr(BusinessTaskDispatcher, "retry_existing", retry_existing)
    db, auth = await _tenant_actor_auth(case, ["module_task:business_task:retry"])
    try:
        action = await BusinessTaskService(auth).retry(task_id)
    finally:
        await db.close()
    assert called is True
    assert action.status == "failed"


@pytest.mark.asyncio
async def test_start_failure_and_closure_database_errors_remain_retryable(test_client, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    async def original_failure(*_args, **_kwargs):
        raise OperationalError("SELECT target", {}, RuntimeError("database unavailable"))

    original_record = ControlUserEntitlementWorkerService._record_start_failure
    calls = 0

    async def closure_failure_once(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("UPDATE grant", {}, RuntimeError("database unavailable"))
        return await original_record(self, *args, **kwargs)

    monkeypatch.setattr(ControlUserEntitlementWorkerService, "_load_target", original_failure)
    monkeypatch.setattr(ControlUserEntitlementWorkerService, "_record_start_failure", closure_failure_once)
    executor = BusinessTaskExecutor(session_factory=async_db_session)
    assert (await executor.execute(task.id)).status == "retrying"
    assert (await executor.execute(task.id)).status == "failed"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.sync_status == "failed"


@pytest.mark.parametrize("write_method", ["_record_failure", "_record_success"])
@pytest.mark.asyncio
async def test_guarded_writeback_database_error_is_retryable(test_client, monkeypatch, write_method) -> None:
    from sqlalchemy.exc import OperationalError

    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    if write_method == "_record_failure":
        async def target(_request):
            return httpx.Response(400, json={"detail": "bad request"})
        ControlUserEntitlementWorkerService.transport = httpx.MockTransport(target)
    else:
        ControlUserEntitlementWorkerService.transport = _success_transport()

    original = getattr(ControlUserEntitlementWorkerService, write_method)
    calls = 0

    async def fail_once(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("UPDATE grant", {}, RuntimeError("database unavailable"))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ControlUserEntitlementWorkerService, write_method, fail_once)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    executor = BusinessTaskExecutor(session_factory=async_db_session)
    assert (await executor.execute(task.id)).status == "retrying"
    expected = "failed" if write_method == "_record_failure" else "success"
    assert (await executor.execute(task.id)).status == expected


@pytest.mark.asyncio
async def test_chunked_oversized_target_response_stops_before_full_buffer(test_client, monkeypatch) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    yielded = 0

    class ChunkedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal yielded
            for _ in range(8192):
                yielded += 1
                yield b"x" * 1024

    async def oversized(_request):
        return httpx.Response(200, stream=ChunkedBody())

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(oversized)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    assert (await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)).status == "failed"
    assert yielded < 100
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.last_error_code == "TARGET_RESPONSE_TOO_LARGE"


@pytest.mark.parametrize("declared_length", [8 * 1024 * 1024, -1])
@pytest.mark.asyncio
async def test_invalid_declared_target_response_length_is_rejected_before_body_read(
    test_client,
    monkeypatch,
    declared_length,
) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    body_read = False

    class UnreadBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal body_read
            body_read = True
            yield b"should-not-be-read"

    async def oversized(_request):
        return httpx.Response(
            200,
            headers={"content-length": str(declared_length)},
            stream=UnreadBody(),
        )

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(oversized)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    assert (await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)).status == "failed"
    assert body_read is False


@pytest.mark.asyncio
@pytest.mark.parametrize("content_encoding", ["gzip", "GZip", "identity, gzip", "br, gzip"])
async def test_encoded_target_response_is_rejected_before_body_read(
    test_client,
    monkeypatch,
    content_encoding,
) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)
    body_read = False

    class CompressedBomb(httpx.AsyncByteStream):
        async def __aiter__(self):
            import gzip

            nonlocal body_read
            body_read = True
            yield gzip.compress(b"x" * (16 * 1024 * 1024))

    async def encoded(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-encoding": content_encoding, "content-length": "32"},
            stream=CompressedBomb(),
        )

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(encoded)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    assert (await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)).status == "failed"
    assert body_read is False
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.last_error_code == "TARGET_RESPONSE_ENCODING_UNSUPPORTED"


@pytest.mark.asyncio
async def test_invalid_target_response_does_not_leak_body_or_ticket_to_exception_logs(test_client, monkeypatch) -> None:
    from loguru import logger

    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    marker = "LIVE-TICKET-RESPONSE-MARKER-" + "z" * 40
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def issue_marker(*_args, **_kwargs):
        return marker

    async def invalid_response(_request):
        return httpx.Response(
            200,
            json={
                "data": {
                    "disposition": marker,
                    "status": marker,
                    "applied_version": marker,
                    "role_codes": [marker],
                    "effective_menu_count": marker,
                }
            },
        )

    monkeypatch.setattr(
        "app.api.v1.module_control.user_entitlement.service.ControlUserEntitlementTicketService.issue",
        issue_marker,
    )
    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(invalid_response)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    default_log = io.StringIO()
    diagnostic_log = io.StringIO()
    default_sink = logger.add(default_log, format="{message}\n{exception}", diagnose=False, backtrace=True)
    diagnostic_sink = logger.add(diagnostic_log, format="{message}\n{exception}", diagnose=True, backtrace=True)
    try:
        assert (await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)).status == "failed"
    finally:
        logger.remove(default_sink)
        logger.remove(diagnostic_sink)
    assert marker not in default_log.getvalue()
    assert marker not in diagnostic_log.getvalue()


@pytest.mark.asyncio
async def test_second_temporary_failure_uses_twenty_second_backoff(test_client, monkeypatch) -> None:
    from datetime import UTC, datetime

    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "temporarily unavailable"})

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(unavailable)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    executor = BusinessTaskExecutor(session_factory=async_db_session)
    assert (await executor.execute(task.id)).status == "retrying"
    before_second = datetime.now(UTC).replace(tzinfo=None)
    assert (await executor.execute(task.id)).status == "retrying"
    async with async_db_session() as check_db:
        grant = await check_db.get(ControlUserApplicationGrantModel, case["grant_id"])
        delay = (grant.next_retry_at - before_second).total_seconds()
        assert 18 <= delay <= 22
        persisted = await check_db.get(BusinessTaskModel, task.id)
        persisted.status = "failed"
        await check_db.commit()


@pytest.mark.parametrize(
    ("error_type", "expected_outcome"),
    [
        (httpx.ConnectError, "retrying"),
        (httpx.ReadError, "retrying"),
        (httpx.LocalProtocolError, "failed"),
        (httpx.RemoteProtocolError, "retrying"),
        (httpx.UnsupportedProtocol, "failed"),
    ],
)
def test_worker_classifies_transport_errors(test_client, monkeypatch, error_type, expected_outcome) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = asyncio.run(_seed_case())
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def network_failure(request: httpx.Request) -> httpx.Response:
        raise error_type("network failure", request=request)

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(network_failure)

    async def execute():
        db, auth = await _admin_auth(case["site_id"])
        try:
            task = await ControlUserEntitlementCommandService(auth).set_desired_state(
                case["grant_id"], "active", mode="grant"
            )
            await db.commit()
        finally:
            await db.close()
        outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
        async with async_db_session() as cleanup_db:
            persisted = await cleanup_db.get(BusinessTaskModel, task.id)
            persisted.status = "failed"
            await cleanup_db.commit()
        return outcome

    assert asyncio.run(execute()).status == expected_outcome


@pytest.mark.asyncio
async def test_live_ticket_code_is_absent_from_diagnostic_exception_logs(test_client, monkeypatch) -> None:
    from loguru import logger

    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    marker = "LIVE-TICKET-MARKER-" + "x" * 48
    case = await _seed_case()
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def issue_marker(*_args, **_kwargs):
        return marker

    async def fail_target(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("read failed", request=request)

    monkeypatch.setattr(
        "app.api.v1.module_control.user_entitlement.service.ControlUserEntitlementTicketService.issue",
        issue_marker,
    )
    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(fail_target)
    db, auth = await _admin_auth(case["site_id"])
    try:
        task = await ControlUserEntitlementCommandService(auth).set_desired_state(
            case["grant_id"], "active", mode="grant"
        )
        await db.commit()
    finally:
        await db.close()

    captured = io.StringIO()
    sink_id = logger.add(captured, format="{message}\n{exception}", diagnose=True, backtrace=True)
    try:
        outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
    finally:
        logger.remove(sink_id)
    assert outcome.status == "retrying"
    assert marker not in captured.getvalue()
    async with async_db_session() as cleanup_db:
        persisted = await cleanup_db.get(BusinessTaskModel, task.id)
        persisted.status = "failed"
        await cleanup_db.commit()


@pytest.mark.parametrize(
    ("http_status", "expected_outcome"),
    [(429, "retrying"), (502, "retrying"), (503, "retrying"), (504, "retrying"), (400, "failed"), (401, "failed"), (403, "failed"), (404, "failed"), (409, "failed")],
)
def test_worker_retries_only_temporary_target_statuses(
    test_client, monkeypatch, http_status: int, expected_outcome: str
) -> None:
    from app.api.v1.module_control.user_entitlement.service import (
        ControlUserEntitlementCommandService,
        ControlUserEntitlementWorkerService,
    )
    from app.core.database import async_db_session
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401

    _ = test_client
    case = asyncio.run(_seed_case())
    monkeypatch.setattr("app.plugin.module_task.runtime.dispatcher.settings.CELERY_ENABLED", True)

    async def target(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(http_status, json={"detail": "safe"})

    ControlUserEntitlementWorkerService.transport = httpx.MockTransport(target)

    async def prepare_and_run():
        db, auth = await _admin_auth(case["site_id"])
        try:
            task = await ControlUserEntitlementCommandService(auth).set_desired_state(
                case["grant_id"], "active", mode="grant"
            )
            await db.commit()
        finally:
            await db.close()
        outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task.id)
        if outcome.status == "retrying":
            async with async_db_session() as cleanup_db:
                persisted = await cleanup_db.get(BusinessTaskModel, task.id)
                persisted.status = "failed"
                await cleanup_db.commit()
        return outcome

    assert asyncio.run(prepare_and_run()).status == expected_outcome


def test_handler_and_plugin_register_both_control_workers() -> None:
    from app.plugin.module_control_provision import entitlement_handlers  # noqa: F401
    from app.plugin.module_task.runtime.registry import business_task_registry

    definition = business_task_registry.get("control.user_entitlement_sync")
    assert definition.max_retries == 2
    plugin_toml = (
        __import__("pathlib").Path(__file__).parents[1]
        / "app/plugin/module_control_provision/plugin.toml"
    ).read_text(encoding="utf-8")
    assert "app.plugin.module_control_provision.handlers" in plugin_toml
    assert "app.plugin.module_control_provision.entitlement_handlers" in plugin_toml


def test_manual_product_may_acknowledge_qualification_before_local_role_assignment():
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementWorkerService
    from app.api.v1.module_control.user_entitlement.task_schema import ControlTargetUserEntitlementResult, ControlUserEntitlementTaskPayload
    payload = ControlUserEntitlementTaskPayload(grant_id=1, event_id="manual-event", sync_version=3, mode="grant")
    result = ControlTargetUserEntitlementResult(disposition="applied", status="active", applied_version=3, local_user_id=1, role_codes=[], effective_menu_count=0, session_cleanup_pending=False)
    ControlUserEntitlementWorkerService._validate_target_result(payload, "active", result)


def test_pending_session_cleanup_is_retryable_not_success():
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementWorkerService, _TargetEntitlementError
    from app.api.v1.module_control.user_entitlement.task_schema import ControlTargetUserEntitlementResult, ControlUserEntitlementTaskPayload
    payload = ControlUserEntitlementTaskPayload(grant_id=1, event_id="cleanup-event", sync_version=3, mode="revoke")
    result = ControlTargetUserEntitlementResult(disposition="applied", status="inactive", applied_version=3, local_user_id=1, role_codes=[], effective_menu_count=0, session_cleanup_pending=True)
    with pytest.raises(_TargetEntitlementError) as error:
        ControlUserEntitlementWorkerService._validate_target_result(payload, "inactive", result)
    assert error.value.retryable is True
    assert error.value.code == "TARGET_SESSION_CLEANUP_PENDING"
