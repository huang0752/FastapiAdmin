import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from app.api.v1.module_system.role.model import RoleModel
from app.core.permission import Permission


def query_for(role):
    auth = SimpleNamespace(user=SimpleNamespace(is_superuser=False, roles=[role]), tenant_id=3, check_data_scope=True)
    query = asyncio.run(Permission(RoleModel, auth).filter_query(select(RoleModel.id)))
    return str(query.compile(compile_kwargs={"literal_binds": True}))


def role(**changes):
    return SimpleNamespace(**({"id": 6, "tenant_id": 3, "code": "owner", "is_system": True, "status": 0, "is_deleted": False} | changes))


def test_owner_can_manage_unbound_roles_only_in_current_tenant():
    for code in ("owner", "admin"):
        sql = query_for(role(code=code))
        assert "sys_role.tenant_id = 3" in sql
        assert "sys_role.id IN (6)" not in sql


def test_inactive_foreign_or_custom_owner_named_roles_do_not_bypass_binding():
    for changes in ({"status": 1}, {"is_deleted": True}, {"tenant_id": 4}, {"is_system": False}, {"code": "member"}):
        assert "sys_role.id IN (6)" in query_for(role(**changes))
