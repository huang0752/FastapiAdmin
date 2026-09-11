"""Server-originated revocations caused by identity/tenant lifecycle changes.

The source mutation and each inactive generation/outbox row share a transaction.
Publication is opportunistic after commit; the pending-task scanner is authoritative.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import and_, event, or_, select

from app.api.v1.module_control.model import ControlTenantApplicationModel, ControlUserApplicationGrantModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.control_features import is_control_provider
from app.core.exceptions import CustomException
from app.plugin.module_task.runtime.exceptions import InvalidBackgroundActorError

from .service import ControlUserEntitlementCommandService, publish_entitlement_tasks

PENDING_KEY = "control_lifecycle_publish_ids"
TASK_PREFIX = "control-lifecycle:"
_publishers: set[asyncio.Task] = set()


def _after_commit(session):
    if session.in_nested_transaction():
        return
    ids = session.info.pop(PENDING_KEY, [])
    if ids:
        task = asyncio.get_running_loop().create_task(publish_entitlement_tasks(ids))
        _publishers.add(task)
        task.add_done_callback(_publishers.discard)


def _after_rollback(session):
    if not session.in_nested_transaction():
        session.info.pop(PENDING_KEY, None)


def _queue_after_commit(db, task_id: int):
    session = db.sync_session
    if not session.info.get("control_lifecycle_listeners"):
        event.listen(session, "after_commit", _after_commit)
        event.listen(session, "after_rollback", _after_rollback)
        session.info["control_lifecycle_listeners"] = True
    session.info.setdefault(PENDING_KEY, []).append(task_id)


async def revoke_control_access(
    auth: AuthSchema,
    *,
    user_ids: list[int] | None = None,
    tenant_ids: list[int] | None = None,
    opening_ids: list[int] | None = None,
    application_ids: list[int] | None = None,
) -> list[int]:
    """Invalidate exact scopes; platform-global identity disable spans its Sites.

    Only shared, already-authorized mutation services call this helper. Public
    task schemas expose neither handler_code nor idempotency_key.
    """
    if not is_control_provider():
        return []
    if not any((user_ids, tenant_ids, opening_ids, application_ids)):
        raise ValueError("生命周期撤权必须明确用户、租户或产品范围")
    if auth.site_id is None or auth.user is None:
        raise CustomException(msg="生命周期撤权缺少可信站点或操作人", status_code=403)
    conditions = [
        ControlUserApplicationGrantModel.is_deleted.is_(False),
        or_(
            ControlUserApplicationGrantModel.desired_state == "active",
            and_(ControlUserApplicationGrantModel.desired_state == "inactive", ControlUserApplicationGrantModel.sync_status != "succeeded"),
        ),
    ]
    global_identity_change = auth.is_platform_global and user_ids is not None and tenant_ids is None and opening_ids is None and application_ids is None
    if not global_identity_change:
        conditions.append(ControlUserApplicationGrantModel.site_id == auth.site_id)
    if not auth.is_platform_global:
        conditions.append(ControlUserApplicationGrantModel.tenant_id == auth.tenant_id)
    for values, field in ((user_ids, ControlUserApplicationGrantModel.user_id), (tenant_ids, ControlUserApplicationGrantModel.tenant_id), (opening_ids, ControlUserApplicationGrantModel.tenant_application_id)):
        if values is not None:
            conditions.append(field.in_(values))
    if application_ids is not None:
        conditions.append(ControlUserApplicationGrantModel.tenant_application_id.in_(select(ControlTenantApplicationModel.id).where(ControlTenantApplicationModel.application_id.in_(application_ids))))
    task_ids = []
    cursor = 0
    while True:
        rows = list((await auth.db.execute(select(ControlUserApplicationGrantModel.id, ControlUserApplicationGrantModel.site_id).where(*conditions, ControlUserApplicationGrantModel.id > cursor).order_by(ControlUserApplicationGrantModel.id).limit(200))).all())
        if not rows:
            break
        for grant_id, site_id in rows:
            command = ControlUserEntitlementCommandService(auth.model_copy(update={"site_id": site_id}))
            task = await command.set_desired_state(grant_id, "inactive", mode="lifecycle")
            task.idempotency_key = f"{TASK_PREFIX}{grant_id}:{task.payload['sync_version']}:{task.payload['event_id']}"
            await auth.db.flush()
            task_ids.append(task.id)
            _queue_after_commit(auth.db, task.id)
        cursor = rows[-1].id
    return task_ids


def is_lifecycle_task(task) -> bool:
    payload = task.payload or {}
    return (
        task.handler_code == "control.user_entitlement_sync"
        and payload.get("mode") == "lifecycle"
        and task.biz_type == "user_entitlement"
        and task.biz_id == str(payload.get("grant_id"))
        and task.idempotency_key == f"{TASK_PREFIX}{payload.get('grant_id')}:{payload.get('sync_version')}:{payload.get('event_id')}"
    )


async def build_lifecycle_revocation_auth(db, task) -> AuthSchema:
    """Reconstruct only an already-recorded inactive decision, not general authority."""
    if not is_lifecycle_task(task) or task.created_id is None or not is_control_provider():
        raise InvalidBackgroundActorError("无效生命周期撤权来源")
    grant = await db.get(ControlUserApplicationGrantModel, task.payload["grant_id"])
    tenant = await db.get(TenantModel, task.tenant_id)
    if grant is None or tenant is None or grant.tenant_id != task.tenant_id or tenant.site_id != grant.site_id:
        raise InvalidBackgroundActorError("生命周期撤权租户站点不匹配")
    # An older generation is permitted to reach the worker's superseded check;
    # it can never overwrite a later active generation.
    if grant.sync_version < task.payload["sync_version"] or (grant.sync_version == task.payload["sync_version"] and (grant.desired_state != "inactive" or grant.last_event_id != task.payload["event_id"])):
        raise InvalidBackgroundActorError("生命周期撤权版本或状态不匹配")
    actor = await db.get(UserModel, task.created_id)
    if actor is None:
        raise InvalidBackgroundActorError("生命周期撤权操作记录缺少原始用户")
    # No superuser or active-package bypass escapes this handler-specific context.
    identity = SimpleNamespace(id=actor.id, is_superuser=False, roles=[], status=actor.status)
    auth = AuthSchema.for_background_task(db=db, user=identity, tenant_id=task.tenant_id)
    auth.site_id = grant.site_id
    return auth
