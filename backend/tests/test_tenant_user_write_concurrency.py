from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_platform.tenant.schema import TenantUserAddSchema
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.role.model import RoleModel
from app.api.v1.module_system.user.model import (
    UserLoginIdentifierModel,
    UserModel,
    UserRolesModel,
)
from app.api.v1.module_system.user.service import UserService
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException


def _is_user_for_update(statement) -> bool:
    if getattr(statement, "_for_update_arg", None) is None:
        return False
    return any(
        description.get("entity") is UserModel
        for description in getattr(statement, "column_descriptions", ())
    )


def _pause_after_user_lock(
    monkeypatch,
    target_session: AsyncSession,
) -> tuple[asyncio.Event, asyncio.Event]:
    user_locked = asyncio.Event()
    release = asyncio.Event()
    original_execute = AsyncSession.execute

    async def coordinated_execute(session, statement, *args, **kwargs):
        result = await original_execute(session, statement, *args, **kwargs)
        if session is target_session and _is_user_for_update(statement):
            user_locked.set()
            await release.wait()
        return result

    monkeypatch.setattr(AsyncSession, "execute", coordinated_execute)
    return user_locked, release


async def _cleanup_users_and_tenants(
    *,
    user_ids: list[int],
    tenant_ids: list[int],
) -> None:
    async with async_db_session() as cleanup_db, cleanup_db.begin():
        role_ids = set(
            (
                await cleanup_db.execute(
                    select(RoleModel.id).where(
                        RoleModel.tenant_id.in_(tenant_ids)
                    )
                )
            ).scalars().all()
        )
        await cleanup_db.execute(
            delete(UserRolesModel).where(UserRolesModel.user_id.in_(user_ids))
        )
        await cleanup_db.execute(
            delete(TenantUserModel).where(TenantUserModel.user_id.in_(user_ids))
        )
        await cleanup_db.execute(
            delete(UserLoginIdentifierModel).where(
                UserLoginIdentifierModel.user_id.in_(user_ids)
            )
        )
        await cleanup_db.execute(
            delete(UserModel).where(UserModel.id.in_(user_ids))
        )
        if role_ids:
            await cleanup_db.execute(
                delete(RoleModel).where(RoleModel.id.in_(role_ids))
            )
        await cleanup_db.execute(
            delete(TenantModel).where(TenantModel.id.in_(tenant_ids))
        )


@pytest.mark.asyncio
async def test_delete_owner_and_remove_other_owner_are_serialized(
    _api_client,
    monkeypatch,
) -> None:
    suffix = uuid4().hex[:8]
    async with async_db_session() as setup_db, setup_db.begin():
        tenant = TenantModel(
            name=f"并发owner租户{suffix}",
            code=f"ownerconcurrency{suffix}",
            site_id=1,
            status=0,
        )
        setup_db.add(tenant)
        await setup_db.flush()
        owners = [
            UserModel(
                username=f"owner_{index}_{suffix}",
                password="unused",
                name=f"并发Owner {index}",
                tenant_id=tenant.id,
                status=1,
                is_superuser=False,
            )
            for index in (1, 2)
        ]
        setup_db.add_all(owners)
        await setup_db.flush()
        setup_db.add_all(
            TenantUserModel(
                user_id=user.id,
                tenant_id=tenant.id,
                role="owner",
                is_default=1,
            )
            for user in owners
        )
        await setup_db.flush()
        tenant_id = tenant.id
        owner_ids = [user.id for user in owners]

    try:
        async with async_db_session() as delete_db, async_db_session() as remove_db:
            user_locked, release = _pause_after_user_lock(
                monkeypatch,
                delete_db,
            )

            async def delete_first_owner():
                try:
                    async with delete_db.begin():
                        auth = AuthSchema(
                            db=delete_db,
                            tenant_id=tenant_id,
                            user=SimpleNamespace(
                                id=max(owner_ids) + 1000,
                                is_superuser=False,
                                roles=[],
                            ),
                            check_data_scope=False,
                        )
                        await UserService(auth).delete([owner_ids[0]])
                except Exception as exc:
                    return exc
                return None

            async def remove_second_owner():
                try:
                    async with remove_db.begin():
                        auth = AuthSchema(
                            db=remove_db,
                            tenant_id=1,
                            user=SimpleNamespace(
                                id=1,
                                is_superuser=True,
                                roles=[],
                            ),
                            check_data_scope=False,
                        )
                        await TenantService(auth).remove_tenant_user(
                            tenant_id,
                            owner_ids[1],
                        )
                except Exception as exc:
                    return exc
                return None

            delete_task = asyncio.create_task(delete_first_owner())
            try:
                await asyncio.wait_for(user_locked.wait(), timeout=1)
                protocol_observed = True
            except TimeoutError:
                protocol_observed = False
            remove_task = asyncio.create_task(remove_second_owner())
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(delete_task, remove_task)

        async with async_db_session() as check_db:
            remaining_owners = (
                await check_db.execute(
                    select(TenantUserModel.user_id).where(
                        TenantUserModel.tenant_id == tenant_id,
                        TenantUserModel.role == "owner",
                    )
                )
            ).scalars().all()

        assert protocol_observed, "delete must SELECT the user FOR UPDATE"
        assert len(remaining_owners) >= 1
        failures = [result for result in results if result is not None]
        assert len(failures) == 1
        assert isinstance(failures[0], CustomException)
        assert "至少需要保留一个拥有者" in str(failures[0])
    finally:
        await _cleanup_users_and_tenants(
            user_ids=owner_ids,
            tenant_ids=[tenant_id],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("first_operation", ("delete", "add"))
async def test_delete_and_cross_tenant_add_never_leave_deleted_membership(
    _api_client,
    monkeypatch,
    first_operation: str,
) -> None:
    suffix = uuid4().hex[:8]
    async with async_db_session() as setup_db, setup_db.begin():
        current_tenant = TenantModel(
            name=f"删除竞态当前租户{suffix}",
            code=f"deletecurrent{suffix}",
            site_id=1,
            status=0,
        )
        other_tenant = TenantModel(
            name=f"删除竞态其他租户{suffix}",
            code=f"deleteother{suffix}",
            site_id=1,
            status=0,
        )
        setup_db.add_all([current_tenant, other_tenant])
        await setup_db.flush()
        user = UserModel(
            username=f"delete_add_race_{suffix}",
            password="unused",
            name="删除与添加竞态用户",
            tenant_id=current_tenant.id,
            status=1,
            is_superuser=False,
        )
        setup_db.add(user)
        await setup_db.flush()
        setup_db.add(
            TenantUserModel(
                user_id=user.id,
                tenant_id=current_tenant.id,
                role="member",
                is_default=1,
            )
        )
        await setup_db.flush()
        current_tenant_id = current_tenant.id
        other_tenant_id = other_tenant.id
        user_id = user.id

    try:
        async with async_db_session() as delete_db, async_db_session() as add_db:
            first_db = delete_db if first_operation == "delete" else add_db
            user_locked, release = _pause_after_user_lock(
                monkeypatch,
                first_db,
            )

            async def delete_user():
                try:
                    async with delete_db.begin():
                        auth = AuthSchema(
                            db=delete_db,
                            tenant_id=current_tenant_id,
                            user=SimpleNamespace(
                                id=user_id + 1000,
                                is_superuser=False,
                                roles=[],
                            ),
                            check_data_scope=False,
                        )
                        await UserService(auth).delete([user_id])
                except Exception as exc:
                    return exc
                return None

            async def add_other_membership():
                try:
                    async with add_db.begin():
                        auth = AuthSchema(
                            db=add_db,
                            tenant_id=1,
                            user=SimpleNamespace(
                                id=1,
                                is_superuser=True,
                                roles=[],
                            ),
                            check_data_scope=False,
                        )
                        await TenantService(auth).add_tenant_user(
                            other_tenant_id,
                            TenantUserAddSchema(
                                user_id=user_id,
                                role="member",
                                is_default=0,
                            ),
                        )
                except Exception as exc:
                    return exc
                return None

            operations = {
                "delete": delete_user,
                "add": add_other_membership,
            }
            second_operation = "add" if first_operation == "delete" else "delete"
            first_task = asyncio.create_task(operations[first_operation]())
            try:
                await asyncio.wait_for(user_locked.wait(), timeout=1)
                protocol_observed = True
            except TimeoutError:
                protocol_observed = False
            second_task = asyncio.create_task(operations[second_operation]())
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(first_task, second_task)

        async with async_db_session() as check_db:
            persisted_user = await check_db.get(UserModel, user_id)
            memberships = (
                await check_db.execute(
                    select(TenantUserModel.tenant_id).where(
                        TenantUserModel.user_id == user_id
                    )
                )
            ).scalars().all()

        assert protocol_observed, (
            f"{first_operation} must SELECT the user FOR UPDATE"
        )
        assert not (persisted_user.is_deleted and memberships)
        failures = [result for result in results if result is not None]
        assert len(failures) == 1
        assert isinstance(failures[0], CustomException)
        if first_operation == "delete":
            assert persisted_user.is_deleted is True
            assert memberships == []
            assert "已删除" in str(failures[0])
        else:
            assert persisted_user.is_deleted is False
            assert set(memberships) == {
                current_tenant_id,
                other_tenant_id,
            }
            assert "其他租户" in str(failures[0])
    finally:
        await _cleanup_users_and_tenants(
            user_ids=[user_id],
            tenant_ids=[current_tenant_id, other_tenant_id],
        )
