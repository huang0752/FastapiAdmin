from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
from app.api.v1.module_platform.package.schema import PackageMenuSetSchema
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_platform.tenant.schema import TenantCreateSchema
from app.api.v1.module_platform.tenant.service import TenantService
from app.api.v1.module_system.federated_access.default_role import DefaultUserRoleService
from app.api.v1.module_system.position.model import PositionModel
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.role.schema import (
    RoleCreateSchema,
    RolePermissionSettingSchema,
    RoleUpdateSchema,
)
from app.api.v1.module_system.role.service import RoleService
from app.api.v1.module_system.user.crud import UserCRUD
from app.api.v1.module_system.user.model import (
    UserModel,
    UserPositionsModel,
    UserRolesModel,
)
from app.api.v1.module_system.user.schema import UserUpdateSchema
from app.api.v1.module_system.user.service import UserService
from app.config.setting import settings
from app.core.assembly import reset_assembly_cache
from app.core.base_schema import AuthSchema, BatchSetAvailable
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.scripts.reconcile_default_user_roles import (
    ReconciliationError,
    build_parser,
    build_reconciliation_plan,
    parse_args,
    reconcile_default_user_roles,
    run,
)

PRODUCT_CASES = (
    ("alpha", "module_alpha", "alpha"),
    ("beta", "module_beta", "beta"),
    ("gamma", "module_gamma", "gamma"),
)


@dataclass(frozen=True)
class DefaultRoleFixture:
    tenant_id: int
    package_id: int
    parent_menu_id: int
    business_menu_id: int
    business_button_id: int


@dataclass(frozen=True)
class PartitionedUserRolesFixture:
    current_tenant_id: int
    other_tenant_id: int
    user_id: int
    current_user_role_id: int
    current_manual_role_id: int
    replacement_manual_role_id: int
    current_governance_role_ids: tuple[int, ...]
    current_system_role_id: int
    other_role_ids: tuple[int, ...]


@pytest_asyncio.fixture
async def db_session(_api_client):
    async with async_db_session() as db:
        yield db
        await db.rollback()


@contextmanager
def use_assembly(assembly: str):
    original_assembly = settings.APP_ASSEMBLY
    original_file = settings.APP_ASSEMBLY_FILE
    settings.APP_ASSEMBLY = assembly
    settings.APP_ASSEMBLY_FILE = f"tests/fixtures/assemblies/{assembly}.toml"
    reset_assembly_cache()
    try:
        yield
    finally:
        settings.APP_ASSEMBLY = original_assembly
        settings.APP_ASSEMBLY_FILE = original_file
        reset_assembly_cache()


async def seed_product_tenant_with_standard_package(
    db,
    *,
    assembly: str,
    permission_prefix: str,
    route_group: str,
) -> DefaultRoleFixture:
    suffix = uuid4().hex[:10]
    site = SiteModel(code=f"dr{suffix}", name=f"默认角色站点{suffix}", status=0)
    db.add(site)
    await db.flush()

    package = PackageModel(
        site_id=site.id,
        name=f"标准套餐{suffix}",
        code=f"standard{suffix}",
        status=0,
    )
    db.add(package)
    await db.flush()

    tenant = TenantModel(
        site_id=site.id,
        package_id=package.id,
        name=f"默认角色租户{suffix}",
        code=f"tenant{suffix}",
        status=0,
    )
    db.add(tenant)
    await db.flush()

    parent = MenuModel(
        name=f"业务目录{suffix}",
        title=f"业务目录{suffix}",
        type=1,
        order=1,
        route_name=f"BusinessCatalog{suffix}",
        route_path=f"/{route_group}/default-role-{suffix}",
        component_path=None,
        redirect=f"/{route_group}/default-role-{suffix}/page",
        client="pc",
        scope="tenant",
        status=0,
    )
    business_menu = MenuModel(
        name=f"业务页{suffix}",
        title=f"业务页{suffix}",
        type=2,
        order=1,
        permission=f"{permission_prefix}:default-role:query",
        route_name=f"BusinessPage{suffix}",
        route_path="page",
        component_path=f"{route_group}/default-role/index",
        parent=parent,
        client="pc",
        scope="tenant",
        status=0,
    )
    business_button = MenuModel(
        name=f"业务操作{suffix}",
        title=f"业务操作{suffix}",
        type=3,
        order=1,
        permission=f"{permission_prefix}:default-role:update",
        parent=business_menu,
        client="pc",
        scope="tenant",
        status=0,
    )
    excluded_menus = [
        MenuModel(
            name=f"系统管理{suffix}",
            title=f"系统管理{suffix}",
            type=3,
            order=2,
            permission="module_system:user:update",
            client="pc",
            scope="tenant",
            status=0,
        ),
        MenuModel(
            name=f"平台管理{suffix}",
            title=f"平台管理{suffix}",
            type=3,
            order=3,
            permission="module_platform:tenant:update",
            client="pc",
            scope="tenant",
            status=0,
        ),
        MenuModel(
            name=f"中控管理{suffix}",
            title=f"中控管理{suffix}",
            type=3,
            order=4,
            permission="module_control:application:update",
            client="pc",
            scope="tenant",
            status=0,
        ),
        MenuModel(
            name=f"AI管理{suffix}",
            title=f"AI管理{suffix}",
            type=3,
            order=5,
            permission="module_ai:model:update",
            client="pc",
            scope="tenant",
            status=0,
        ),
        MenuModel(
            name=f"其他产品{suffix}",
            title=f"其他产品{suffix}",
            type=3,
            order=6,
            permission="module_other_product:record:update",
            client="pc",
            scope="tenant",
            status=0,
        ),
    ]
    db.add_all([parent, business_menu, business_button, *excluded_menus])
    await db.flush()
    db.add_all(
        PackageMenuModel(package_id=package.id, menu_id=menu.id)
        for menu in [parent, business_menu, business_button, *excluded_menus]
    )
    await db.flush()
    return DefaultRoleFixture(
        tenant_id=tenant.id,
        package_id=package.id,
        parent_menu_id=parent.id,
        business_menu_id=business_menu.id,
        business_button_id=business_button.id,
    )


async def seed_partitioned_user_roles(db) -> PartitionedUserRolesFixture:
    suffix = uuid4().hex[:8]
    current_tenant = TenantModel(
        name=f"当前角色租户{suffix}",
        code=f"current{suffix}",
        site_id=1,
        status=0,
    )
    other_tenant = TenantModel(
        name=f"其他角色租户{suffix}",
        code=f"other{suffix}",
        site_id=1,
        status=0,
    )
    db.add_all([current_tenant, other_tenant])
    await db.flush()
    user = UserModel(
        username=f"partitioned_{suffix}",
        password="unused",
        name="多租户角色用户",
        tenant_id=current_tenant.id,
        status=0,
        is_superuser=False,
    )
    current_user_role = RoleModel(
        name="当前普通用户",
        code="USER",
        tenant_id=current_tenant.id,
        status=0,
        is_system=True,
        data_scope=1,
    )
    current_manual = RoleModel(
        name="当前旧人工角色",
        code=f"old_manual_{suffix}",
        tenant_id=current_tenant.id,
        status=0,
        is_system=False,
        data_scope=1,
    )
    replacement_manual = RoleModel(
        name="当前新人工角色",
        code=f"new_manual_{suffix}",
        tenant_id=current_tenant.id,
        status=0,
        is_system=False,
        data_scope=1,
    )
    governance_roles = [
        RoleModel(
            name=f"治理角色{code}",
            code=code,
            tenant_id=current_tenant.id,
            status=0,
            is_system=False,
            data_scope=1,
        )
        for code in ("owner", "admin", "member", "SUPER_ADMIN", "ADMIN")
    ]
    current_system = RoleModel(
        name="当前自定义系统角色",
        code=f"SYSTEM_CUSTOM_{suffix}",
        tenant_id=current_tenant.id,
        status=0,
        is_system=True,
        data_scope=1,
    )
    other_manual = RoleModel(
        name="其他租户人工角色",
        code=f"other_manual_{suffix}",
        tenant_id=other_tenant.id,
        status=0,
        is_system=False,
        data_scope=1,
    )
    other_system = RoleModel(
        name="其他租户系统角色",
        code="USER",
        tenant_id=other_tenant.id,
        status=0,
        is_system=True,
        data_scope=1,
    )
    db.add_all(
        [
            user,
            current_user_role,
            current_manual,
            replacement_manual,
            *governance_roles,
            current_system,
            other_manual,
            other_system,
        ]
    )
    await db.flush()
    initially_assigned = [
        current_user_role,
        current_manual,
        *governance_roles,
        current_system,
        other_manual,
        other_system,
    ]
    db.add_all(
        UserRolesModel(user_id=user.id, role_id=role.id)
        for role in initially_assigned
    )
    await db.flush()
    return PartitionedUserRolesFixture(
        current_tenant_id=current_tenant.id,
        other_tenant_id=other_tenant.id,
        user_id=user.id,
        current_user_role_id=current_user_role.id,
        current_manual_role_id=current_manual.id,
        replacement_manual_role_id=replacement_manual.id,
        current_governance_role_ids=tuple(role.id for role in governance_roles),
        current_system_role_id=current_system.id,
        other_role_ids=(other_manual.id, other_system.id),
    )


async def assigned_role_ids(db, user_id: int) -> set[int]:
    return set(
        (
            await db.execute(
                select(UserRolesModel.role_id).where(
                    UserRolesModel.user_id == user_id
                )
            )
        ).scalars().all()
    )


async def seed_user_delete_candidate(
    db,
    *,
    tenant_id: int,
    auth_source: str = "local",
    membership_role: str = "member",
) -> tuple[UserModel, RoleModel, PositionModel, TenantUserModel]:
    suffix = uuid4().hex[:8]
    user = UserModel(
        username=f"delete_{suffix}",
        password="unused",
        name="待删除用户",
        tenant_id=tenant_id,
        status=1,
        is_superuser=False,
        auth_source=auth_source,
        password_login_enabled=auth_source != "federated",
    )
    role = RoleModel(
        name="待删除人工角色",
        code=f"delete_role_{suffix}",
        tenant_id=tenant_id,
        status=0,
        is_system=False,
        data_scope=1,
    )
    position = PositionModel(
        name="待删除岗位",
        code=f"delete_position_{suffix}",
        tenant_id=tenant_id,
        status=0,
    )
    db.add_all([user, role, position])
    await db.flush()
    membership = TenantUserModel(
        user_id=user.id,
        tenant_id=tenant_id,
        role=membership_role,
        is_default=1,
    )
    db.add_all(
        [
            membership,
            UserRolesModel(user_id=user.id, role_id=role.id),
            UserPositionsModel(user_id=user.id, position_id=position.id),
        ]
    )
    await db.flush()
    return user, role, position, membership


@pytest.mark.parametrize(("assembly", "permission_prefix", "route_group"), PRODUCT_CASES)
@pytest.mark.asyncio
async def test_default_user_role_contains_only_current_product_business_permissions_and_ancestors(
    db_session,
    assembly: str,
    permission_prefix: str,
    route_group: str,
) -> None:
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        role = await DefaultUserRoleService(db_session).ensure(fixture.tenant_id)

    permissions = {menu.permission for menu in role.menus if menu.permission}
    assert permissions
    assert permissions == {
        f"{permission_prefix}:default-role:query",
        f"{permission_prefix}:default-role:update",
    }
    assert {menu.id for menu in role.menus} == {
        fixture.parent_menu_id,
        fixture.business_menu_id,
        fixture.business_button_id,
    }
    assert role.code == "USER"
    assert role.name == "普通用户"
    assert role.status == 0
    assert role.is_system is True
    assert role.data_scope == 1


@pytest.mark.asyncio
async def test_default_user_role_prunes_business_children_under_forbidden_permission_ancestor(
    db_session,
) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        package = await db_session.get(PackageModel, fixture.package_id)
        suffix = uuid4().hex[:8]
        safe_directory = MenuModel(
            name=f"无权限目录{suffix}",
            title=f"无权限目录{suffix}",
            type=1,
            order=20,
            route_name=f"SafeDirectory{suffix}",
            route_path=f"/{route_group}/safe-{suffix}",
            redirect=f"/{route_group}/safe-{suffix}/page",
            client="pc",
            scope="tenant",
            status=0,
        )
        safe_business = MenuModel(
            name=f"安全业务{suffix}",
            title=f"安全业务{suffix}",
            type=2,
            order=1,
            permission=f"{permission_prefix}:safe:query",
            route_name=f"SafeBusiness{suffix}",
            route_path="page",
            component_path=f"{route_group}/safe/index",
            parent=safe_directory,
            client="pc",
            scope="tenant",
            status=0,
        )
        forbidden_directory = MenuModel(
            name=f"系统权限目录{suffix}",
            title=f"系统权限目录{suffix}",
            type=1,
            order=21,
            permission="module_system:user:query",
            route_name=f"ForbiddenDirectory{suffix}",
            route_path=f"/{route_group}/forbidden-{suffix}",
            redirect=f"/{route_group}/forbidden-{suffix}/page",
            client="pc",
            scope="tenant",
            status=0,
        )
        forbidden_business = MenuModel(
            name=f"非法分支业务{suffix}",
            title=f"非法分支业务{suffix}",
            type=2,
            order=1,
            permission=f"{permission_prefix}:forbidden-child:query",
            route_name=f"ForbiddenBusiness{suffix}",
            route_path="page",
            component_path=f"{route_group}/forbidden/index",
            parent=forbidden_directory,
            client="pc",
            scope="tenant",
            status=0,
        )
        db_session.add_all(
            [safe_directory, safe_business, forbidden_directory, forbidden_business]
        )
        await db_session.flush()
        db_session.add_all(
            PackageMenuModel(package_id=package.id, menu_id=menu.id)
            for menu in (
                safe_directory,
                safe_business,
                forbidden_directory,
                forbidden_business,
            )
        )
        await db_session.flush()

        role = await DefaultUserRoleService(db_session).ensure(fixture.tenant_id)

    role_menu_ids = {menu.id for menu in role.menus}
    assert safe_directory.id in role_menu_ids
    assert safe_business.id in role_menu_ids
    assert forbidden_directory.id not in role_menu_ids
    assert forbidden_business.id not in role_menu_ids


@pytest.mark.asyncio
async def test_default_user_role_restores_disabled_role_and_replaces_package_menus(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        service = DefaultUserRoleService(db_session)
        role = await service.ensure(fixture.tenant_id)
        role.status = 1
        role.is_system = False
        role.data_scope = 4

        replacement = MenuModel(
            name="替换业务",
            title="替换业务",
            type=2,
            order=10,
            permission=f"{permission_prefix}:replacement:query",
            route_name="TraceReplacement",
            route_path=f"/{route_group}/replacement",
            component_path=f"{route_group}/replacement/index",
            client="pc",
            scope="tenant",
            status=0,
        )
        db_session.add(replacement)
        await db_session.flush()
        await db_session.execute(
            delete(PackageMenuModel).where(PackageMenuModel.package_id == fixture.package_id)
        )
        db_session.add(
            PackageMenuModel(package_id=fixture.package_id, menu_id=replacement.id)
        )
        await db_session.flush()

        restored = await service.ensure(fixture.tenant_id)

    assert restored.id == role.id
    assert restored.status == 0
    assert restored.is_system is True
    assert restored.data_scope == 1
    assert {menu.id for menu in restored.menus} == {replacement.id}


@pytest.mark.asyncio
async def test_default_user_role_fails_stably_when_package_has_no_business_menu(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        await db_session.execute(
            delete(PackageMenuModel).where(PackageMenuModel.package_id == fixture.package_id)
        )
        await db_session.flush()

        with pytest.raises(CustomException, match="没有有效业务菜单") as exc_info:
            await DefaultUserRoleService(db_session).ensure(fixture.tenant_id)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_bind_only_when_user_has_no_active_local_role(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        no_role_user = UserModel(
            username=f"no_role_{uuid4().hex[:8]}",
            password="unused",
            name="无角色用户",
            tenant_id=fixture.tenant_id,
            status=0,
            is_superuser=False,
        )
        manual_role_user = UserModel(
            username=f"manual_role_{uuid4().hex[:8]}",
            password="unused",
            name="人工角色用户",
            tenant_id=fixture.tenant_id,
            status=0,
            is_superuser=False,
        )
        manual_role = RoleModel(
            name="人工业务角色",
            code=f"manual_{uuid4().hex[:8]}",
            tenant_id=fixture.tenant_id,
            status=0,
            is_system=False,
            data_scope=1,
        )
        db_session.add_all([no_role_user, manual_role_user, manual_role])
        await db_session.flush()
        db_session.add(
            UserRolesModel(user_id=manual_role_user.id, role_id=manual_role.id)
        )
        await db_session.flush()

        service = DefaultUserRoleService(db_session)
        bound = await service.bind_if_user_has_no_active_role(
            fixture.tenant_id,
            no_role_user.id,
        )
        skipped = await service.bind_if_user_has_no_active_role(
            fixture.tenant_id,
            manual_role_user.id,
        )

    assert bound is not None
    assert bound.code == "USER"
    assert skipped is None
    no_role_codes = set(
        (
            await db_session.execute(
                select(RoleModel.code)
                .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                .where(UserRolesModel.user_id == no_role_user.id)
            )
        ).scalars().all()
    )
    manual_role_codes = set(
        (
            await db_session.execute(
                select(RoleModel.code)
                .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                .where(UserRolesModel.user_id == manual_role_user.id)
            )
        ).scalars().all()
    )
    assert no_role_codes == {"USER"}
    assert manual_role_codes == {manual_role.code}


@pytest.mark.asyncio
async def test_bind_refreshes_user_role_before_skipping_existing_binding(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        service = DefaultUserRoleService(db_session)
        role = await service.ensure(fixture.tenant_id)
        user = UserModel(
            username=f"existing_user_{uuid4().hex[:8]}",
            password="unused",
            name="已绑定普通用户",
            tenant_id=fixture.tenant_id,
            status=0,
            is_superuser=False,
        )
        replacement = MenuModel(
            name="绑定前刷新业务",
            title="绑定前刷新业务",
            type=2,
            order=10,
            permission=f"{permission_prefix}:bind-refresh:query",
            route_name="TraceBindRefresh",
            route_path=f"/{route_group}/bind-refresh",
            component_path=f"{route_group}/bind-refresh/index",
            client="pc",
            scope="tenant",
            status=0,
        )
        db_session.add_all([user, replacement])
        await db_session.flush()
        db_session.add(UserRolesModel(user_id=user.id, role_id=role.id))
        await db_session.execute(
            delete(PackageMenuModel).where(
                PackageMenuModel.package_id == fixture.package_id
            )
        )
        db_session.add(
            PackageMenuModel(
                package_id=fixture.package_id,
                menu_id=replacement.id,
            )
        )
        await db_session.flush()

        skipped = await service.bind_if_user_has_no_active_role(
            fixture.tenant_id,
            user.id,
        )
        await db_session.refresh(role, attribute_names=["menus"])

    assert skipped is None
    assert {menu.id for menu in role.menus} == {replacement.id}


@pytest.mark.asyncio
async def test_concurrent_default_role_binding_is_idempotent(_api_client) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    fixture: DefaultRoleFixture | None = None
    user_id: int | None = None
    menu_ids: set[int] = set()
    site_id: int | None = None
    with use_assembly(assembly):
        async with async_db_session() as setup_db:
            fixture = await seed_product_tenant_with_standard_package(
                setup_db,
                assembly=assembly,
                permission_prefix=permission_prefix,
                route_group=route_group,
            )
            tenant = await setup_db.get(TenantModel, fixture.tenant_id)
            site_id = tenant.site_id
            menu_ids = set(
                (
                    await setup_db.execute(
                        select(PackageMenuModel.menu_id).where(
                            PackageMenuModel.package_id == fixture.package_id
                        )
                    )
                ).scalars().all()
            )
            user = UserModel(
                username=f"concurrent_user_{uuid4().hex[:8]}",
                password="unused",
                name="并发普通用户",
                tenant_id=fixture.tenant_id,
                status=0,
                is_superuser=False,
            )
            setup_db.add(user)
            await setup_db.flush()
            user_id = user.id
            await setup_db.commit()

        async def bind_once() -> int | None:
            async with async_db_session() as bind_db, bind_db.begin():
                role = await DefaultUserRoleService(
                    bind_db
                ).bind_if_user_has_no_active_role(fixture.tenant_id, user_id)
                return role.id if role is not None else None

        try:
            await asyncio.gather(bind_once(), bind_once())
            async with async_db_session() as check_db:
                roles = list(
                    (
                        await check_db.execute(
                            select(RoleModel).where(
                                RoleModel.tenant_id == fixture.tenant_id,
                                RoleModel.code == "USER",
                            )
                        )
                    ).scalars().all()
                )
                role_ids = {role.id for role in roles}
                bindings = list(
                    (
                        await check_db.execute(
                            select(UserRolesModel).where(
                                UserRolesModel.user_id == user_id,
                                UserRolesModel.role_id.in_(role_ids),
                            )
                        )
                    ).scalars().all()
                )
                role_count = len(roles)
                binding_count = len(bindings)
        finally:
            async with async_db_session() as cleanup_db, cleanup_db.begin():
                role_ids = set(
                    (
                        await cleanup_db.execute(
                            select(RoleModel.id).where(
                                RoleModel.tenant_id == fixture.tenant_id
                            )
                        )
                    ).scalars().all()
                )
                if role_ids:
                    await cleanup_db.execute(
                        delete(UserRolesModel).where(
                            UserRolesModel.role_id.in_(role_ids)
                        )
                    )
                    await cleanup_db.execute(
                        delete(RoleMenusModel).where(
                            RoleMenusModel.role_id.in_(role_ids)
                        )
                    )
                    await cleanup_db.execute(
                        delete(RoleModel).where(RoleModel.id.in_(role_ids))
                    )
                await cleanup_db.execute(
                    delete(UserRolesModel).where(UserRolesModel.user_id == user_id)
                )
                await cleanup_db.execute(
                    delete(UserModel).where(UserModel.id == user_id)
                )
                await cleanup_db.execute(
                    delete(PackageMenuModel).where(
                        PackageMenuModel.package_id == fixture.package_id
                    )
                )
                await cleanup_db.execute(
                    delete(TenantModel).where(TenantModel.id == fixture.tenant_id)
                )
                await cleanup_db.execute(
                    delete(PackageModel).where(PackageModel.id == fixture.package_id)
                )
                await cleanup_db.execute(
                    delete(MenuModel).where(MenuModel.id.in_(menu_ids))
                )
                await cleanup_db.execute(
                    delete(SiteModel).where(SiteModel.id == site_id)
                )

    assert role_count == 1
    assert binding_count == 1


@pytest.mark.asyncio
async def test_manual_role_assignment_racing_default_binding_never_coexists(
    _api_client,
) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    fixture: DefaultRoleFixture | None = None
    user_id: int | None = None
    manual_role_id: int | None = None
    start = asyncio.Event()
    with use_assembly(assembly):
        async with async_db_session() as setup_db:
            fixture = await seed_product_tenant_with_standard_package(
                setup_db,
                assembly=assembly,
                permission_prefix=permission_prefix,
                route_group=route_group,
            )
            user = UserModel(
                username=f"race_user_{uuid4().hex[:8]}",
                password="unused",
                name="竞态用户",
                tenant_id=fixture.tenant_id,
                status=0,
                is_superuser=False,
            )
            manual_role = RoleModel(
                name="竞态人工角色",
                code=f"race_manual_{uuid4().hex[:8]}",
                tenant_id=fixture.tenant_id,
                status=0,
                is_system=False,
                data_scope=1,
            )
            setup_db.add_all([user, manual_role])
            await setup_db.flush()
            user_id = user.id
            manual_role_id = manual_role.id
            await setup_db.commit()

        async def bind_default() -> None:
            async with async_db_session() as bind_db, bind_db.begin():
                await start.wait()
                await DefaultUserRoleService(bind_db).bind_if_user_has_no_active_role(
                    fixture.tenant_id,
                    user_id,
                )

        async def assign_manual() -> None:
            async with async_db_session() as manual_db, manual_db.begin():
                await start.wait()
                auth = AuthSchema(
                    db=manual_db,
                    tenant_id=fixture.tenant_id,
                    check_data_scope=False,
                )
                await UserCRUD(auth).set_user_roles([user_id], [manual_role_id])

        start.set()
        await asyncio.gather(bind_default(), assign_manual())

        async with async_db_session() as check_db:
            assigned_codes = set(
                (
                    await check_db.execute(
                        select(RoleModel.code)
                        .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                        .where(UserRolesModel.user_id == user_id)
                    )
                ).scalars().all()
            )

    assert assigned_codes == {manual_role.code}


@pytest.mark.parametrize("operation", ("update", "delete", "permission", "disable"))
@pytest.mark.asyncio
async def test_is_system_role_cannot_be_mutated_through_role_service(
    db_session,
    operation: str,
) -> None:
    tenant = TenantModel(
        name=f"系统角色租户{uuid4().hex[:8]}",
        code=f"sr{uuid4().hex[:8]}",
        site_id=1,
        status=0,
    )
    db_session.add(tenant)
    await db_session.flush()
    role = RoleModel(
        name="内置受保护角色",
        code=f"SYSTEM_{uuid4().hex[:8]}",
        tenant_id=tenant.id,
        status=0,
        is_system=True,
        data_scope=1,
    )
    db_session.add(role)
    await db_session.flush()
    auth = AuthSchema(
        db=db_session,
        tenant_id=tenant.id,
        user=SimpleNamespace(id=1, is_superuser=False, roles=[]),
        check_data_scope=False,
    )
    service = RoleService(auth)

    calls = {
        "update": lambda: service.update(
            role.id,
            RoleUpdateSchema(
                name="试图修改",
                code=role.code,
                status=0,
                data_scope=1,
            ),
        ),
        "delete": lambda: service.delete([role.id]),
        "permission": lambda: service.set_permission(
            RolePermissionSettingSchema(
                role_ids=[role.id],
                menu_ids=[],
                data_scope=1,
            )
        ),
        "disable": lambda: service.set_available(
            BatchSetAvailable(ids=[role.id], status=1)
        ),
    }
    with pytest.raises(CustomException, match="系统.*角色"):
        await calls[operation]()


@pytest.mark.asyncio
async def test_role_service_rejects_manual_user_role_creation(db_session) -> None:
    auth = AuthSchema(
        db=db_session,
        tenant_id=1,
        user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
        check_data_scope=False,
    )
    with use_assembly(PRODUCT_CASES[0][0]), pytest.raises(CustomException, match="系统.*角色"):
        await RoleService(auth).create(
            RoleCreateSchema(name="伪造普通用户", code="USER")
        )


@pytest.mark.asyncio
async def test_user_role_crud_rejects_direct_system_role_assignment(db_session) -> None:
    suffix = uuid4().hex[:8]
    tenant = TenantModel(
        name=f"用户角色租户{suffix}",
        code=f"ur{suffix}",
        site_id=1,
        status=0,
    )
    db_session.add(tenant)
    await db_session.flush()
    user = UserModel(
        username=f"role_user_{suffix}",
        password="unused",
        name="角色防线用户",
        tenant_id=tenant.id,
        status=0,
        is_superuser=False,
    )
    reserved_role = RoleModel(
        name="伪造系统角色",
        code=f"reserved_{suffix}",
        tenant_id=tenant.id,
        status=0,
        is_system=True,
        data_scope=1,
    )
    reserved_code_role = RoleModel(
        name="伪造治理角色",
        code="member",
        tenant_id=tenant.id,
        status=0,
        is_system=False,
        data_scope=1,
    )
    db_session.add_all([user, reserved_role, reserved_code_role])
    await db_session.flush()
    auth = AuthSchema(
        db=db_session,
        tenant_id=tenant.id,
        user=SimpleNamespace(id=1, is_superuser=False, roles=[]),
        check_data_scope=False,
    )

    for role in (reserved_role, reserved_code_role):
        with pytest.raises(CustomException, match="系统.*角色"):
            await UserCRUD(auth).set_user_roles([user.id], [role.id])


@pytest.mark.asyncio
async def test_user_service_manual_role_replaces_existing_default_user_role(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        default_role = await DefaultUserRoleService(db_session).ensure(
            fixture.tenant_id
        )
        user = UserModel(
            username=f"replace_user_{uuid4().hex[:8]}",
            password="unused",
            name="人工覆盖普通用户",
            tenant_id=fixture.tenant_id,
            status=0,
            is_superuser=False,
        )
        manual_role = RoleModel(
            name="产品人工角色",
            code=f"manual_replace_{uuid4().hex[:8]}",
            tenant_id=fixture.tenant_id,
            status=0,
            is_system=False,
            data_scope=1,
        )
        db_session.add_all([user, manual_role])
        await db_session.flush()
        db_session.add(UserRolesModel(user_id=user.id, role_id=default_role.id))
        await db_session.flush()
        auth = AuthSchema(
            db=db_session,
            tenant_id=fixture.tenant_id,
            user=SimpleNamespace(id=1, is_superuser=False, roles=[]),
            check_data_scope=False,
        )

        await UserService(auth).update(
            user.id,
            UserUpdateSchema(username=user.username, role_ids=[manual_role.id]),
        )

    assigned_codes = set(
        (
            await db_session.execute(
                select(RoleModel.code)
                .join(UserRolesModel, UserRolesModel.role_id == RoleModel.id)
                .where(UserRolesModel.user_id == user.id)
            )
        ).scalars().all()
    )
    assert assigned_codes == {manual_role.code}


@pytest.mark.asyncio
async def test_set_user_roles_preserves_every_role_from_other_tenants(db_session) -> None:
    fixture = await seed_partitioned_user_roles(db_session)
    auth = AuthSchema(
        db=db_session,
        tenant_id=fixture.current_tenant_id,
        check_data_scope=False,
    )

    await UserCRUD(auth).set_user_roles(
        [fixture.user_id],
        [fixture.replacement_manual_role_id],
    )

    role_ids = await assigned_role_ids(db_session, fixture.user_id)
    assert set(fixture.other_role_ids) <= role_ids


@pytest.mark.asyncio
async def test_set_user_roles_preserves_current_tenant_governance_and_system_roles(
    db_session,
) -> None:
    fixture = await seed_partitioned_user_roles(db_session)
    auth = AuthSchema(
        db=db_session,
        tenant_id=fixture.current_tenant_id,
        check_data_scope=False,
    )

    await UserCRUD(auth).set_user_roles(
        [fixture.user_id],
        [fixture.replacement_manual_role_id],
    )

    role_ids = await assigned_role_ids(db_session, fixture.user_id)
    assert set(fixture.current_governance_role_ids) <= role_ids
    assert fixture.current_system_role_id in role_ids
    assert fixture.current_manual_role_id not in role_ids
    assert fixture.current_user_role_id not in role_ids
    assert fixture.replacement_manual_role_id in role_ids


@pytest.mark.asyncio
async def test_set_user_roles_empty_only_removes_current_tenant_manual_and_user_roles(
    db_session,
) -> None:
    fixture = await seed_partitioned_user_roles(db_session)
    auth = AuthSchema(
        db=db_session,
        tenant_id=fixture.current_tenant_id,
        check_data_scope=False,
    )

    await UserCRUD(auth).set_user_roles([fixture.user_id], [])

    role_ids = await assigned_role_ids(db_session, fixture.user_id)
    assert fixture.current_manual_role_id not in role_ids
    assert fixture.current_user_role_id not in role_ids
    assert fixture.replacement_manual_role_id not in role_ids
    assert set(fixture.current_governance_role_ids) <= role_ids
    assert fixture.current_system_role_id in role_ids
    assert set(fixture.other_role_ids) <= role_ids


@pytest.mark.asyncio
async def test_user_delete_rejects_federated_user(
    db_session,
) -> None:
    suffix = uuid4().hex[:8]
    tenant = TenantModel(
        name=f"联邦删除租户{suffix}",
        code=f"feddelete{suffix}",
        site_id=1,
        status=0,
    )
    db_session.add(tenant)
    await db_session.flush()
    user, role, _, membership = await seed_user_delete_candidate(
        db_session,
        tenant_id=tenant.id,
        auth_source="federated",
    )
    user.status = 0
    await db_session.flush()
    auth = AuthSchema(
        db=db_session,
        tenant_id=tenant.id,
        user=SimpleNamespace(id=user.id + 1, is_superuser=False, roles=[]),
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="中控.*撤权|中控.*管理"):
        await UserService(auth).delete([user.id])

    await db_session.refresh(user)
    assert user.is_deleted is False
    assert role.id in await assigned_role_ids(db_session, user.id)
    assert await db_session.get(TenantUserModel, membership.id) is not None


@pytest.mark.asyncio
async def test_user_delete_rejects_user_with_other_tenant_membership(
    db_session,
) -> None:
    fixture = await seed_partitioned_user_roles(db_session)
    user = await db_session.get(UserModel, fixture.user_id)
    user.status = 1
    db_session.add_all(
        [
            TenantUserModel(
                user_id=user.id,
                tenant_id=fixture.current_tenant_id,
                role="member",
                is_default=1,
            ),
            TenantUserModel(
                user_id=user.id,
                tenant_id=fixture.other_tenant_id,
                role="member",
                is_default=0,
            ),
        ]
    )
    await db_session.flush()
    auth = AuthSchema(
        db=db_session,
        tenant_id=fixture.current_tenant_id,
        user=SimpleNamespace(id=user.id + 1, is_superuser=False, roles=[]),
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="其他租户.*成员管理"):
        await UserService(auth).delete([user.id])

    await db_session.refresh(user)
    assert user.is_deleted is False
    assert await assigned_role_ids(db_session, user.id)
    memberships = (
        await db_session.execute(
            select(TenantUserModel.tenant_id).where(
                TenantUserModel.user_id == user.id
            )
        )
    ).scalars().all()
    assert set(memberships) == {
        fixture.current_tenant_id,
        fixture.other_tenant_id,
    }


@pytest.mark.asyncio
async def test_user_delete_does_not_remove_other_tenants_only_owner(
    db_session,
) -> None:
    suffix = uuid4().hex[:8]
    current_tenant = TenantModel(
        name=f"当前租户{suffix}",
        code=f"currentdelete{suffix}",
        site_id=1,
        status=0,
    )
    other_tenant = TenantModel(
        name=f"唯一owner租户{suffix}",
        code=f"onlyowner{suffix}",
        site_id=1,
        status=0,
    )
    db_session.add_all([current_tenant, other_tenant])
    await db_session.flush()
    user, _, _, _ = await seed_user_delete_candidate(
        db_session,
        tenant_id=current_tenant.id,
    )
    other_membership = TenantUserModel(
        user_id=user.id,
        tenant_id=other_tenant.id,
        role="owner",
        is_default=0,
    )
    db_session.add(other_membership)
    await db_session.flush()
    auth = AuthSchema(
        db=db_session,
        tenant_id=current_tenant.id,
        user=SimpleNamespace(id=user.id + 1, is_superuser=False, roles=[]),
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="其他租户.*成员管理"):
        await UserService(auth).delete([user.id])

    assert await db_session.get(TenantUserModel, other_membership.id) is not None
    owner_count = (
        await db_session.execute(
            select(TenantUserModel.id).where(
                TenantUserModel.tenant_id == other_tenant.id,
                TenantUserModel.role == "owner",
            )
        )
    ).scalars().all()
    assert owner_count == [other_membership.id]


@pytest.mark.asyncio
async def test_user_delete_rejects_current_tenants_last_owner(db_session) -> None:
    suffix = uuid4().hex[:8]
    tenant = TenantModel(
        name=f"最后owner租户{suffix}",
        code=f"lastowner{suffix}",
        site_id=1,
        status=0,
    )
    db_session.add(tenant)
    await db_session.flush()
    user, role, _, membership = await seed_user_delete_candidate(
        db_session,
        tenant_id=tenant.id,
        membership_role="owner",
    )
    auth = AuthSchema(
        db=db_session,
        tenant_id=tenant.id,
        user=SimpleNamespace(id=user.id + 1, is_superuser=False, roles=[]),
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="至少需要保留一个拥有者"):
        await UserService(auth).delete([user.id])

    await db_session.refresh(user)
    assert user.is_deleted is False
    assert role.id in await assigned_role_ids(db_session, user.id)
    assert await db_session.get(TenantUserModel, membership.id) is not None


@pytest.mark.asyncio
async def test_user_delete_single_tenant_member_only_cleans_current_tenant(
    db_session,
) -> None:
    suffix = uuid4().hex[:8]
    tenant = TenantModel(
        name=f"单租户删除{suffix}",
        code=f"singledelete{suffix}",
        site_id=1,
        status=0,
    )
    other_tenant = TenantModel(
        name=f"历史关联租户{suffix}",
        code=f"staleother{suffix}",
        site_id=1,
        status=0,
    )
    db_session.add_all([tenant, other_tenant])
    await db_session.flush()
    user, role, position, membership = await seed_user_delete_candidate(
        db_session,
        tenant_id=tenant.id,
    )
    other_role = RoleModel(
        name="其他租户历史角色",
        code=f"stale_role_{suffix}",
        tenant_id=other_tenant.id,
        status=0,
        is_system=False,
        data_scope=1,
    )
    other_position = PositionModel(
        name="其他租户历史岗位",
        code=f"stale_position_{suffix}",
        tenant_id=other_tenant.id,
        status=0,
    )
    db_session.add_all([other_role, other_position])
    await db_session.flush()
    db_session.add_all(
        [
            UserRolesModel(user_id=user.id, role_id=other_role.id),
            UserPositionsModel(user_id=user.id, position_id=other_position.id),
        ]
    )
    await db_session.flush()
    auth = AuthSchema(
        db=db_session,
        tenant_id=tenant.id,
        user=SimpleNamespace(id=user.id + 1, is_superuser=False, roles=[]),
        check_data_scope=False,
    )

    await UserService(auth).delete([user.id])

    await db_session.refresh(user)
    assert user.is_deleted is True
    role_ids = await assigned_role_ids(db_session, user.id)
    assert role.id not in role_ids
    assert other_role.id in role_ids
    position_ids = set(
        (
            await db_session.execute(
                select(UserPositionsModel.position_id).where(
                    UserPositionsModel.user_id == user.id
                )
            )
        ).scalars().all()
    )
    assert position.id not in position_ids
    assert other_position.id in position_ids
    assert await db_session.get(TenantUserModel, membership.id) is None


@pytest.mark.asyncio
async def test_user_delete_batch_preflight_is_atomic(db_session) -> None:
    suffix = uuid4().hex[:8]
    tenant = TenantModel(
        name=f"批量删除租户{suffix}",
        code=f"batchdelete{suffix}",
        site_id=1,
        status=0,
    )
    db_session.add(tenant)
    await db_session.flush()
    local_user, local_role, _, local_membership = (
        await seed_user_delete_candidate(db_session, tenant_id=tenant.id)
    )
    federated_user, federated_role, _, federated_membership = (
        await seed_user_delete_candidate(
            db_session,
            tenant_id=tenant.id,
            auth_source="federated",
        )
    )
    auth = AuthSchema(
        db=db_session,
        tenant_id=tenant.id,
        user=SimpleNamespace(
            id=federated_user.id + 1,
            is_superuser=False,
            roles=[],
        ),
        check_data_scope=False,
    )

    with pytest.raises(CustomException, match="中控"):
        await UserService(auth).delete([local_user.id, federated_user.id])

    for user in (local_user, federated_user):
        await db_session.refresh(user)
        assert user.is_deleted is False
    assert local_role.id in await assigned_role_ids(db_session, local_user.id)
    assert federated_role.id in await assigned_role_ids(
        db_session,
        federated_user.id,
    )
    assert await db_session.get(TenantUserModel, local_membership.id) is not None
    assert (
        await db_session.get(TenantUserModel, federated_membership.id)
        is not None
    )


@pytest.mark.asyncio
async def test_tenant_record_creation_ensures_default_user_role(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        source_tenant = await db_session.get(TenantModel, fixture.tenant_id)
        tenant = await TenantService(
            AuthSchema(db=db_session, tenant_id=1, check_data_scope=False)
        ).create_tenant_record(
            TenantCreateSchema(
                name=f"新建默认角色租户{uuid4().hex[:8]}",
                code=f"new{uuid4().hex[:8]}",
                site_id=source_tenant.site_id,
                package_id=fixture.package_id,
            )
        )

    role = (
        await db_session.execute(
            select(RoleModel).where(
                RoleModel.tenant_id == tenant.id,
                RoleModel.code == "USER",
            )
        )
    ).scalar_one_or_none()
    assert role is not None
    assert role.is_system is True


@pytest.mark.asyncio
async def test_package_menu_change_reconciles_default_user_role(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        role = await DefaultUserRoleService(db_session).ensure(fixture.tenant_id)
        replacement = MenuModel(
            name="套餐替换业务",
            title="套餐替换业务",
            type=2,
            order=10,
            permission=f"{permission_prefix}:package-replacement:query",
            route_name="TracePackageReplacement",
            route_path=f"/{route_group}/package-replacement",
            component_path=f"{route_group}/package-replacement/index",
            client="pc",
            scope="tenant",
            status=0,
        )
        db_session.add(replacement)
        await db_session.flush()
        auth = AuthSchema(
            db=db_session,
            tenant_id=1,
            user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
            check_data_scope=False,
        )

        await PackageService(auth).set_menus(
            fixture.package_id,
            PackageMenuSetSchema(menu_ids=[replacement.id]),
        )
        await db_session.refresh(role, attribute_names=["menus"])

    assert {menu.id for menu in role.menus} == {replacement.id}


@pytest.mark.asyncio
async def test_tenant_package_change_reconciles_default_user_role(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        role = await DefaultUserRoleService(db_session).ensure(fixture.tenant_id)
        tenant = await db_session.get(TenantModel, fixture.tenant_id)
        package = PackageModel(
            site_id=tenant.site_id,
            name=f"变更套餐{uuid4().hex[:8]}",
            code=f"changed{uuid4().hex[:8]}",
            status=0,
        )
        replacement = MenuModel(
            name="租户套餐替换业务",
            title="租户套餐替换业务",
            type=2,
            order=10,
            permission=f"{permission_prefix}:tenant-package:query",
            route_name="TraceTenantPackageReplacement",
            route_path=f"/{route_group}/tenant-package-replacement",
            component_path=f"{route_group}/tenant-package-replacement/index",
            client="pc",
            scope="tenant",
            status=0,
        )
        db_session.add_all([package, replacement])
        await db_session.flush()
        db_session.add(
            PackageMenuModel(package_id=package.id, menu_id=replacement.id)
        )
        await db_session.flush()
        auth = AuthSchema(
            db=db_session,
            tenant_id=1,
            user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
            check_data_scope=False,
        )

        await TenantService(auth).apply_package_change(fixture.tenant_id, package.id)
        await db_session.refresh(role, attribute_names=["menus"])

    assert {menu.id for menu in role.menus} == {replacement.id}


@pytest.mark.asyncio
async def test_tenant_package_change_acquires_role_assignment_lock_before_plan(
    db_session,
    monkeypatch,
) -> None:
    import app.api.v1.module_platform.tenant.service as tenant_service_module

    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        tenant = await db_session.get(TenantModel, fixture.tenant_id)
        package = PackageModel(
            site_id=tenant.site_id,
            name=f"锁序套餐{uuid4().hex[:8]}",
            code=f"lock{uuid4().hex[:8]}",
            status=0,
        )
        db_session.add(package)
        await db_session.flush()
        events: list[str] = []
        lock_held = False

        @asynccontextmanager
        async def tracked_lock(db, tenant_id):
            nonlocal lock_held
            assert db is db_session
            assert tenant_id == fixture.tenant_id
            events.append("lock_enter")
            lock_held = True
            try:
                yield
            finally:
                events.append("lock_exit")
                lock_held = False

        original_plan = TenantService.plan_package_change

        async def tracked_plan(service, tenant_id, package_id):
            events.append("plan")
            assert lock_held, "plan must run after the tenant role-assignment lock"
            return await original_plan(service, tenant_id, package_id)

        async def tracked_ensure(service, tenant_id):
            events.append("ensure")
            assert lock_held, "default-role reconciliation must remain inside the lock"

        monkeypatch.setattr(
            tenant_service_module,
            "lock_tenant_role_assignment",
            tracked_lock,
            raising=False,
        )
        monkeypatch.setattr(TenantService, "plan_package_change", tracked_plan)
        monkeypatch.setattr(DefaultUserRoleService, "ensure", tracked_ensure)
        auth = AuthSchema(
            db=db_session,
            tenant_id=1,
            user=SimpleNamespace(id=1, is_superuser=True, roles=[]),
            check_data_scope=False,
        )

        await TenantService(auth).apply_package_change(
            fixture.tenant_id,
            package.id,
        )

    assert events == ["lock_enter", "plan", "ensure", "lock_exit"]


def test_reconcile_script_requires_explicit_apply_flag() -> None:
    assembly = PRODUCT_CASES[0][0]
    with use_assembly(assembly):
        with pytest.raises(SystemExit):
            parse_args([])
        with pytest.raises(SystemExit):
            parse_args(["--tenant-id", "1", "--all-tenants"])
        preview_args = parse_args(["--tenant-id", "1", "--tenant-id", "2"])
        all_args = parse_args(["--all-tenants"])
        with pytest.raises(SystemExit):
            parse_args(
                [
                    "--apply",
                    "--all-tenants",
                    "--confirm-product",
                    "alpha",
                ]
            )
        with pytest.raises(SystemExit):
            parse_args(
                [
                    "--apply",
                    "--all-tenants",
                    "--confirm-product",
                    "alpha",
                    "--confirm-database",
                    "wrong_database",
                ]
            )
        args = parse_args(
            [
                "--apply",
                "--all-tenants",
                "--confirm-product",
                "alpha",
                "--confirm-database",
                settings.DATABASE_NAME,
            ]
        )

    assert preview_args.apply is False
    assert preview_args.tenant_ids == [1, 2]
    assert preview_args.all_tenants is False
    assert all_args.all_tenants is True
    assert args.apply is True
    assert args.confirm_product == "alpha"
    assert args.confirm_database == settings.DATABASE_NAME
    assert build_parser() is not None


@pytest.mark.asyncio
async def test_reconcile_run_rejects_database_or_scope_before_opening_session(
    monkeypatch,
) -> None:
    opened = False

    @asynccontextmanager
    async def forbidden_session():
        nonlocal opened
        opened = True
        raise AssertionError("确认失败时不得打开数据库会话")
        yield

    monkeypatch.setattr(
        "app.scripts.reconcile_default_user_roles.async_db_session",
        forbidden_session,
    )
    with use_assembly(PRODUCT_CASES[0][0]):
        with pytest.raises(ReconciliationError, match="数据库"):
            await run(
                apply=True,
                confirm_product="alpha",
                confirm_database="wrong_database",
                tenant_ids={1},
            )
        with pytest.raises(ReconciliationError, match="scope"):
            await run(
                apply=False,
                confirm_product=None,
                confirm_database=None,
            )
        with pytest.raises(ReconciliationError, match="scope"):
            await run(
                apply=False,
                confirm_product=None,
                confirm_database=None,
                tenant_ids={1},
                all_tenants=True,
            )

    assert opened is False


@pytest.mark.asyncio
async def test_reconcile_script_rejects_any_planning_error_before_apply(db_session) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        await db_session.execute(
            delete(PackageMenuModel).where(
                PackageMenuModel.package_id == fixture.package_id
            )
        )
        await db_session.flush()

        with pytest.raises(ReconciliationError, match="tenant"):
            await reconcile_default_user_roles(
                db_session,
                apply=True,
                tenant_ids={fixture.tenant_id},
            )

    role = (
        await db_session.execute(
            select(RoleModel).where(
                RoleModel.tenant_id == fixture.tenant_id,
                RoleModel.code == "USER",
            )
        )
    ).scalar_one_or_none()
    assert role is None


@pytest.mark.asyncio
async def test_reconcile_explicit_scope_rejects_mixed_valid_and_missing_tenants(
    db_session,
) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        tenant = await db_session.get(TenantModel, fixture.tenant_id)
        suffix = uuid4().hex[:8]
        deleted_tenant = TenantModel(
            name=f"混合已删除租户{suffix}",
            code=f"mixdeleted{suffix}",
            site_id=tenant.site_id,
            package_id=fixture.package_id,
            status=0,
            is_deleted=True,
        )
        no_package_tenant = TenantModel(
            name=f"混合无套餐租户{suffix}",
            code=f"mixnopackage{suffix}",
            site_id=tenant.site_id,
            package_id=None,
            status=0,
        )
        db_session.add_all([deleted_tenant, no_package_tenant])
        await db_session.flush()
        missing_id = no_package_tenant.id + 10_000_000

        with pytest.raises(
            ReconciliationError,
            match="不存在.*已删除.*未关联套餐",
        ):
            await reconcile_default_user_roles(
                db_session,
                apply=True,
                tenant_ids={
                    fixture.tenant_id,
                    deleted_tenant.id,
                    no_package_tenant.id,
                    missing_id,
                },
            )

    role = (
        await db_session.execute(
            select(RoleModel).where(
                RoleModel.tenant_id == fixture.tenant_id,
                RoleModel.code == "USER",
            )
        )
    ).scalar_one_or_none()
    assert role is None


@pytest.mark.asyncio
async def test_reconcile_explicit_scope_rejects_all_invalid_tenants(db_session) -> None:
    suffix = uuid4().hex[:8]
    deleted_tenant = TenantModel(
        name=f"已删除租户{suffix}",
        code=f"deleted{suffix}",
        site_id=1,
        package_id=1,
        status=0,
        is_deleted=True,
    )
    no_package_tenant = TenantModel(
        name=f"无套餐租户{suffix}",
        code=f"nopackage{suffix}",
        site_id=1,
        package_id=None,
        status=0,
    )
    db_session.add_all([deleted_tenant, no_package_tenant])
    await db_session.flush()
    missing_id = max(deleted_tenant.id, no_package_tenant.id) + 10_000_000

    with use_assembly(PRODUCT_CASES[0][0]):
        with pytest.raises(
            ReconciliationError,
            match="不存在.*已删除.*未关联套餐",
        ):
            await reconcile_default_user_roles(
                db_session,
                apply=True,
                tenant_ids={
                    deleted_tenant.id,
                    no_package_tenant.id,
                    missing_id,
                },
            )


@pytest.mark.asyncio
async def test_reconcile_run_rolls_back_all_tenants_on_execution_error(
    db_session,
    monkeypatch,
) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        first = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        second = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        original_ensure = DefaultUserRoleService.ensure
        calls = 0

        async def fail_second_tenant(service, tenant_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise CustomException(msg="注入的租户执行失败")
            return await original_ensure(service, tenant_id)

        @asynccontextmanager
        async def current_test_session():
            yield db_session

        monkeypatch.setattr(DefaultUserRoleService, "ensure", fail_second_tenant)
        monkeypatch.setattr(
            "app.scripts.reconcile_default_user_roles.async_db_session",
            current_test_session,
        )

        with pytest.raises(ReconciliationError, match=f"tenant {second.tenant_id}"):
            await run(
                apply=True,
                confirm_product="alpha",
                confirm_database=settings.DATABASE_NAME,
                tenant_ids={first.tenant_id, second.tenant_id},
            )

    db_session.expire_all()
    remaining_tenants = set(
        (
            await db_session.execute(
                select(TenantModel.id).where(
                    TenantModel.id.in_([first.tenant_id, second.tenant_id])
                )
            )
        ).scalars().all()
    )
    assert remaining_tenants == set()


@pytest.mark.asyncio
async def test_reconcile_script_previews_without_mutation_and_applies_explicitly(
    db_session,
) -> None:
    assembly, permission_prefix, route_group = PRODUCT_CASES[0]
    with use_assembly(assembly):
        fixture = await seed_product_tenant_with_standard_package(
            db_session,
            assembly=assembly,
            permission_prefix=permission_prefix,
            route_group=route_group,
        )
        tenant = await db_session.get(TenantModel, fixture.tenant_id)
        site_id = tenant.site_id

        preview = await build_reconciliation_plan(
            db_session,
            tenant_ids={fixture.tenant_id},
        )
        role_before_apply = (
            await db_session.execute(
                select(RoleModel).where(
                    RoleModel.tenant_id == fixture.tenant_id,
                    RoleModel.code == "USER",
                )
            )
        ).scalar_one_or_none()
        applied = await reconcile_default_user_roles(
            db_session,
            apply=True,
            tenant_ids={fixture.tenant_id},
        )

    assert preview["mode"] == "preview"
    assert preview["product"] == "alpha"
    assert preview["database"] == settings.DATABASE_NAME
    assert preview["site_ids"] == [site_id]
    assert preview["site_count"] == 1
    assert preview["tenant_count"] == 1
    assert len(preview["tenants"]) == 1
    preview_tenant = preview["tenants"][0]
    assert preview_tenant["tenant_id"] == fixture.tenant_id
    assert preview_tenant["action"] == "create"
    assert preview_tenant["desired_menu_count"] == 3
    assert preview_tenant["current_menu_count"] == 0
    assert preview_tenant["drift_reasons"] == ["missing_system_user_role"]
    assert role_before_apply is None
    assert applied["mode"] == "apply"
    assert applied["tenants"][0]["action"] == "create"
    role_after_apply = (
        await db_session.execute(
            select(RoleModel).where(
                RoleModel.tenant_id == fixture.tenant_id,
                RoleModel.code == "USER",
            )
        )
    ).scalar_one()
    assert role_after_apply.is_system is True


def test_manual_policy_does_not_reserve_ordinary_user_role_code():
    from unittest.mock import patch

    from app.api.v1.module_system.role.constants import system_managed_role_codes
    from app.core.assembly import AssemblyConfig
    with patch("app.api.v1.module_system.role.constants.get_assembly", return_value=AssemblyConfig()):
        assert "USER" not in system_managed_role_codes()


def test_declared_permission_selection_rejects_admin_ancestors_and_empty_grants():
    tree = [
        {"id": 1, "permission": "", "children": [{"id": 2, "permission": "module_alpha:read"}]},
        {"id": 3, "permission": "module_platform:admin", "children": [{"id": 4, "permission": "module_alpha:read"}]},
        {"id": 5, "permission": "module_beta:read"},
    ]
    assert DefaultUserRoleService._collect_business_menu_ids(tree, permission_codes={"module_alpha:read"}) == {1, 2}
    assert DefaultUserRoleService._collect_business_menu_ids(tree) == set()
