from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.tenant.model import TenantModel

_PROCESS_TENANT_LOCKS: dict[tuple[asyncio.AbstractEventLoop, int], asyncio.Lock] = {}
_PROCESS_USER_LOCKS: dict[tuple[asyncio.AbstractEventLoop, int], asyncio.Lock] = {}


def _release_session_locks(sync_session, transaction) -> None:
    if transaction.parent is not None:
        return
    lock_groups = (
        ("tenant_role_assignment_locks", _PROCESS_TENANT_LOCKS),
        ("tenant_user_membership_locks", _PROCESS_USER_LOCKS),
    )
    for info_key, process_locks in lock_groups:
        held_locks = sync_session.info.pop(info_key, {})
        for lock_key, lock in held_locks.values():
            lock.release()
            if not lock._waiters:  # noqa: SLF001 - no public waiter count
                process_locks.pop(lock_key, None)


def _ensure_session_lock_listener(db: AsyncSession) -> None:
    sync_session = db.sync_session
    if not sync_session.info.get("tenant_role_assignment_listener"):
        event.listen(sync_session, "after_transaction_end", _release_session_locks)
        sync_session.info["tenant_role_assignment_listener"] = True


@asynccontextmanager
async def lock_tenant_role_assignment(db: AsyncSession, tenant_id: int):
    """Serialize role replacement/binding, with a DB row lock across processes."""
    _ensure_session_lock_listener(db)
    sync_session = db.sync_session
    held_locks = sync_session.info.setdefault("tenant_role_assignment_locks", {})
    if tenant_id not in held_locks:
        lock_key = (asyncio.get_running_loop(), tenant_id)
        process_lock = _PROCESS_TENANT_LOCKS.setdefault(lock_key, asyncio.Lock())
        await process_lock.acquire()
        held_locks[tenant_id] = (lock_key, process_lock)
        await db.execute(
            select(TenantModel.id)
            .where(TenantModel.id == tenant_id)
            .with_for_update()
        )
    yield


@asynccontextmanager
async def lock_tenant_membership_users(
    db: AsyncSession,
    user_ids: list[int],
):
    """Lock membership target users in ascending ID order until transaction end."""
    from app.api.v1.module_system.user.model import UserModel

    ordered_ids = sorted(set(user_ids))
    if not ordered_ids:
        yield []
        return
    _ensure_session_lock_listener(db)
    sync_session = db.sync_session
    held_locks = sync_session.info.setdefault("tenant_user_membership_locks", {})
    for user_id in ordered_ids:
        if user_id in held_locks:
            continue
        lock_key = (asyncio.get_running_loop(), user_id)
        process_lock = _PROCESS_USER_LOCKS.setdefault(lock_key, asyncio.Lock())
        await process_lock.acquire()
        held_locks[user_id] = (lock_key, process_lock)
    users = list(
        (
            await db.execute(
                select(UserModel)
                .where(UserModel.id.in_(ordered_ids))
                .order_by(UserModel.id)
                .with_for_update()
            )
        ).scalars().all()
    )
    yield users
