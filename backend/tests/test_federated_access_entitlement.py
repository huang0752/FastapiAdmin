import asyncio
from uuid import uuid4

from sqlalchemy import CheckConstraint, UniqueConstraint, select

from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.federated_access.model import (
    FederatedAccessEntitlementModel,
    FederatedAccessEventModel,
)
from app.api.v1.module_system.role.model import RoleModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


def test_federated_access_entitlement_has_tenant_subject_unique_key() -> None:
    constraints = {
        tuple(column.name for column in item.columns)
        for item in FederatedAccessEntitlementModel.__table__.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert ("issuer", "central_user_uuid", "tenant_id") in constraints


def test_federated_access_entitlement_status_and_version_contract() -> None:
    table = FederatedAccessEntitlementModel.__table__
    assert table.c.status.default.arg == "inactive"
    assert table.c.applied_version.default.arg == 0
    assert table.c.session_cleanup_pending.default.arg is False


def test_federated_access_event_has_unique_event_receipt() -> None:
    table = FederatedAccessEventModel.__table__
    assert table.c.event_id.unique is True
    assert table.c.request_fingerprint.nullable is False
    assert table.c.result_json.nullable is False


def test_system_role_flag_defaults_to_false() -> None:
    assert RoleModel.__table__.c.is_system.default.arg is False


def test_federated_access_event_requires_positive_sync_version() -> None:
    checks = {
        str(item.sqltext)
        for item in FederatedAccessEventModel.__table__.constraints
        if isinstance(item, CheckConstraint)
    }
    assert "sync_version > 0" in checks


def test_federated_access_event_receipt_is_not_cascade_deleted() -> None:
    foreign_key = next(iter(FederatedAccessEventModel.__table__.c.entitlement_id.foreign_keys))
    assert foreign_key.ondelete in {None, "RESTRICT"}


def test_federated_access_defaults_are_owned_by_the_database() -> None:
    entitlement = FederatedAccessEntitlementModel.__table__
    event = FederatedAccessEventModel.__table__
    role = RoleModel.__table__

    assert entitlement.c.status.server_default.arg == "inactive"
    assert entitlement.c.applied_version.server_default.arg == "0"
    assert str(entitlement.c.session_cleanup_pending.server_default.arg) == "false"
    assert str(role.c.is_system.server_default.arg) == "false"
    assert str(event.c.created_at.server_default.arg).lower() == "now()"
    assert event.c.created_at.default is None


async def _assert_programmatic_governance_roles_are_system_managed() -> None:
    suffix = uuid4().hex[:12]
    async with async_db_session() as db:
        user = UserModel(
            username=f"role_contract_{suffix}",
            password="not-used",
            name="角色契约测试",
            tenant_id=2,
        )
        custom_role = RoleModel(
            name="自定义角色",
            code=f"CUSTOM_{suffix}",
            tenant_id=2,
            status=0,
            data_scope=1,
        )
        db.add_all([user, custom_role])
        await db.flush()

        service = TenantService(AuthSchema(db=db, tenant_id=2, check_data_scope=False))
        for code in ("owner", "admin", "member"):
            await service._replace_tenant_member_rbac(2, user.id, code)

        roles = (
            await db.execute(
                select(RoleModel).where(
                    RoleModel.tenant_id == 2,
                    RoleModel.code.in_({"owner", "admin", "member", custom_role.code}),
                )
            )
        ).scalars().all()
        flags = {role.code: role.is_system for role in roles}
        assert flags["owner"] is True
        assert flags["admin"] is True
        assert flags["member"] is True
        assert flags[custom_role.code] is False
        await db.rollback()


def test_programmatic_governance_roles_are_system_managed(test_client) -> None:
    asyncio.run(_assert_programmatic_governance_roles_are_system_managed())
