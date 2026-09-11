"""Shared lifecycle operations must persist revocation before publishing."""
import asyncio

import pytest
from sqlalchemy import select
from test_control_user_entitlement_worker import _seed_case

from app.api.v1.module_control.model import ControlUserApplicationGrantModel
from app.api.v1.module_platform.tenant.schema import TenantBatchStatusSchema
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.user.model import UserModel
from app.api.v1.module_system.user.service import UserService
from app.config.setting import settings
from app.core.base_schema import AuthSchema, BatchSetAvailable
from app.core.database import async_db_session
from app.plugin.module_task.business.task.model import BusinessTaskModel

pytestmark = pytest.mark.usefixtures("control_provider_context")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["user", "tenant", "membership"])
async def test_shared_lifecycle_persists_inactive_outbox_atomically(test_client, monkeypatch, operation):
    from app.api.v1.module_control.user_entitlement import lifecycle
    monkeypatch.setattr(settings, "CELERY_ENABLED", True)
    published = []
    async def publish(ids):
        published.extend(ids)
    monkeypatch.setattr(lifecycle, "publish_entitlement_tasks", publish)
    case = await _seed_case(desired_state="active", site_id=1)
    async with async_db_session() as db:
        admin = await db.get(UserModel, 1)
        auth = AuthSchema(db=db, user=admin, site_id=1, tenant_id=1, check_data_scope=False)
        if operation == "user":
            await UserService(auth).set_available(BatchSetAvailable(ids=[case["user_id"]], status=1))
        elif operation == "tenant":
            await TenantService(auth).set_available(TenantBatchStatusSchema(ids=[case["tenant_id"]], status=2))
        else:
            await TenantService(auth).remove_tenant_user(case["tenant_id"], case["user_id"])
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.desired_state == "inactive"
        assert grant.sync_version == 2
        tasks = list((await db.scalars(select(BusinessTaskModel).where(BusinessTaskModel.biz_id == str(grant.id), BusinessTaskModel.payload["mode"].as_string() == "lifecycle"))).all())
        assert len(tasks) == 1
        assert published == []
        await db.commit()
        await asyncio.sleep(0)
        assert published == [tasks[0].id]


@pytest.mark.asyncio
async def test_lifecycle_rollback_does_not_publish_or_change_authorization(test_client, monkeypatch):
    from app.api.v1.module_control.user_entitlement import lifecycle
    monkeypatch.setattr(settings, "CELERY_ENABLED", True)
    published = []
    async def publish(ids):
        published.extend(ids)
    monkeypatch.setattr(lifecycle, "publish_entitlement_tasks", publish)
    case = await _seed_case(desired_state="active", site_id=1)
    async with async_db_session() as db:
        admin = await db.get(UserModel, 1)
        auth = AuthSchema(db=db, user=admin, site_id=1, tenant_id=1, check_data_scope=False)
        await UserService(auth).set_available(BatchSetAvailable(ids=[case["user_id"]], status=1))
        await db.rollback()
        await asyncio.sleep(0)
    async with async_db_session() as db:
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.desired_state == "active"
        assert grant.sync_version == 1
    assert published == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["tenant", "membership"])
async def test_lifecycle_worker_finishes_after_source_context_is_gone(test_client, monkeypatch, operation):
    from test_control_user_entitlement_worker import _success_transport

    from app.api.v1.module_control.user_entitlement import lifecycle
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementWorkerService
    from app.plugin.module_task.runtime.executor import BusinessTaskExecutor
    monkeypatch.setattr(settings, "CELERY_ENABLED", True)
    async def no_publish(ids):
        pass
    monkeypatch.setattr(lifecycle, "publish_entitlement_tasks", no_publish)
    monkeypatch.setattr(ControlUserEntitlementWorkerService, "transport", _success_transport(status="inactive"))
    case = await _seed_case(desired_state="active", site_id=1)
    async with async_db_session() as db:
        admin = await db.get(UserModel, 1)
        auth = AuthSchema(db=db, user=admin, site_id=1, tenant_id=1, check_data_scope=False)
        if operation == "tenant":
            await TenantService(auth).set_available(TenantBatchStatusSchema(ids=[case["tenant_id"]], status=2))
        else:
            await TenantService(auth).remove_tenant_user(case["tenant_id"], case["user_id"])
        task = (await db.scalars(select(BusinessTaskModel).where(BusinessTaskModel.biz_id == str(case["grant_id"]), BusinessTaskModel.handler_code == "control.user_entitlement_sync"))).one()
        task_id = task.id
        await db.commit()
    outcome = await BusinessTaskExecutor(session_factory=async_db_session).execute(task_id)
    assert outcome.status == "success"
    async with async_db_session() as db:
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert (grant.desired_state, grant.sync_status) == ("inactive", "succeeded")


@pytest.mark.asyncio
async def test_public_task_creation_cannot_forge_internal_lifecycle_task(test_client):
    from app.api.v1.module_control.user_entitlement.lifecycle import is_lifecycle_task
    from app.plugin.module_task.business.task.schema import BusinessTaskCreateSchema
    from app.plugin.module_task.business.task.service import BusinessTaskService
    async with async_db_session() as db:
        auth = AuthSchema(db=db, user=await db.get(UserModel, 1), tenant_id=1, site_id=1)
        data = BusinessTaskCreateSchema.model_validate({
            "module": "control", "biz_type": "user_entitlement", "biz_id": "1",
            "handler_code": "control.user_entitlement_sync",
            "idempotency_key": "control-lifecycle:1:1:forged",
            "payload": {"mode": "lifecycle", "grant_id": 1, "sync_version": 1, "event_id": "forged"},
        })
        result = await BusinessTaskService(auth).create(data)
        task = await db.get(BusinessTaskModel, result.id)
        assert task.handler_code is None
        assert not is_lifecycle_task(task)
        await db.rollback()


@pytest.mark.asyncio
async def test_lifecycle_retry_keeps_scoped_internal_origin(test_client, monkeypatch):
    from app.api.v1.module_control.user_entitlement import lifecycle
    from app.api.v1.module_control.user_entitlement.service import ControlUserEntitlementCommandService
    monkeypatch.setattr(settings, "CELERY_ENABLED", True)
    async def no_publish(ids):
        pass
    monkeypatch.setattr(lifecycle, "publish_entitlement_tasks", no_publish)
    case = await _seed_case(desired_state="active", site_id=1)
    async with async_db_session() as db:
        auth = AuthSchema(db=db, user=await db.get(UserModel, 1), tenant_id=1, site_id=1)
        await TenantService(auth).set_available(TenantBatchStatusSchema(ids=[case["tenant_id"]], status=2))
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        grant.sync_status = "failed"
        await db.flush()
        retry = await ControlUserEntitlementCommandService(auth).retry(grant.id)
        assert retry.payload["sync_version"] == 3
        assert lifecycle.is_lifecycle_task(retry)
        restored = await lifecycle.build_lifecycle_revocation_auth(db, retry)
        assert restored.tenant_id == case["tenant_id"]
        assert restored.user.is_superuser is False
        await db.rollback()


@pytest.mark.asyncio
async def test_tenant_admin_cannot_disable_shared_global_identity(test_client, monkeypatch):
    from app.api.v1.module_platform.tenant.model import TenantUserModel
    from app.core.exceptions import CustomException
    case = await _seed_case(desired_state="active", site_id=1)
    async with async_db_session() as db:
        user = await db.get(UserModel, case["user_id"])
        # User is primarily stored in tenant 1 and belongs to another tenant.
        db.add(TenantUserModel(user_id=user.id, tenant_id=1, role="member", is_default=0))
        await db.flush()
        auth = AuthSchema(db=db, user=user, site_id=1, tenant_id=1, check_data_scope=False)
        with pytest.raises(CustomException, match="多个租户"):
            await UserService(auth).set_available(BatchSetAvailable(ids=[user.id], status=1))
        assert user.status == 0
        await db.rollback()


@pytest.mark.asyncio
async def test_platform_identity_disable_revokes_exact_grants_across_sites(test_client, monkeypatch):
    from app.api.v1.module_control.user_entitlement import lifecycle
    from app.api.v1.module_platform.tenant.model import TenantUserModel
    monkeypatch.setattr(settings, "CELERY_ENABLED", True)
    async def no_publish(ids):
        pass
    monkeypatch.setattr(lifecycle, "publish_entitlement_tasks", no_publish)
    first = await _seed_case(desired_state="active", site_id=1)
    second = await _seed_case(desired_state="active")
    async with async_db_session() as db:
        shared_grant = await db.get(ControlUserApplicationGrantModel, second["grant_id"])
        shared_grant.user_id = first["user_id"]
        db.add(TenantUserModel(user_id=first["user_id"], tenant_id=second["tenant_id"], role="member", is_default=0))
        await db.flush()
        auth = AuthSchema(db=db, user=await db.get(UserModel, 1), site_id=1, tenant_id=1, check_data_scope=False)
        await UserService(auth).set_available(BatchSetAvailable(ids=[first["user_id"]], status=1))
        for case in [first, second]:
            grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
            assert grant.desired_state == "inactive"
            task = (await db.scalars(select(BusinessTaskModel).where(BusinessTaskModel.idempotency_key == f"control-lifecycle:{grant.id}:{grant.sync_version}:{grant.last_event_id}"))).one()
            restored = await lifecycle.build_lifecycle_revocation_auth(db, task)
            assert restored.site_id == case["site_id"]
            assert restored.tenant_id == case["tenant_id"]
        await db.rollback()


@pytest.mark.asyncio
async def test_shared_identity_delete_is_rejected_before_revocation(test_client):
    from app.api.v1.module_platform.tenant.model import TenantUserModel
    from app.core.exceptions import CustomException
    case = await _seed_case(desired_state="active", site_id=1)
    async with async_db_session() as db:
        user = await db.get(UserModel, case["user_id"])
        user.status = 1
        db.add(TenantUserModel(user_id=user.id, tenant_id=1, role="member", is_default=0))
        await db.flush()
        auth = AuthSchema(db=db, user=await db.get(UserModel, 1), site_id=1, tenant_id=1, check_data_scope=False)
        with pytest.raises(CustomException, match="其他租户成员关系"):
            await UserService(auth).delete([user.id])
        assert not user.is_deleted
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        assert grant.desired_state == "active"
        await db.rollback()


@pytest.mark.asyncio
async def test_lifecycle_upgrades_unfinished_ordinary_revocation(test_client, monkeypatch):
    from app.api.v1.module_control.user_entitlement import lifecycle
    monkeypatch.setattr(settings, "CELERY_ENABLED", True)
    async def no_publish(ids):
        pass
    monkeypatch.setattr(lifecycle, "publish_entitlement_tasks", no_publish)
    case = await _seed_case(desired_state="inactive", site_id=1)
    async with async_db_session() as db:
        grant = await db.get(ControlUserApplicationGrantModel, case["grant_id"])
        grant.sync_status = "failed"
        await db.flush()
        auth = AuthSchema(db=db, user=await db.get(UserModel, 1), site_id=1, tenant_id=1, check_data_scope=False)
        await TenantService(auth).set_available(TenantBatchStatusSchema(ids=[case["tenant_id"]], status=2))
        task = (await db.scalars(select(BusinessTaskModel).where(BusinessTaskModel.idempotency_key == f"control-lifecycle:{grant.id}:{grant.sync_version}:{grant.last_event_id}"))).one()
        assert grant.sync_version == 2
        assert lifecycle.is_lifecycle_task(task)
        await lifecycle.build_lifecycle_revocation_auth(db, task)
        await db.rollback()
