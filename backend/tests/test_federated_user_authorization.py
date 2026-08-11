from dataclasses import dataclass
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
from app.api.v1.module_platform.site.model import SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.api.v1.module_system.dept.model import DeptModel
from app.api.v1.module_system.position.model import PositionModel
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.authorization import UserAuthorizationResolver
from app.api.v1.module_system.user.model import UserModel, UserPositionsModel, UserRolesModel
from app.api.v1.module_system.user.schema import UserAuthorizationStatus, UserQueryParam
from app.api.v1.module_system.user.service import UserService
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


@dataclass(frozen=True)
class FederatedAuthorizationFixture:
    auth: AuthSchema
    authorized_auth: AuthSchema
    authorized_user_id: int
    no_role_user_id: int
    disabled_role_user_id: int
    outside_package_user_id: int
    authorized_username: str
    pending_username: str
    dept_id: int
    role_id: int
    position_id: int


@pytest_asyncio.fixture
async def db_session(_api_client):
    async with async_db_session() as db:
        yield db
        await db.rollback()


@pytest_asyncio.fixture
async def federated_authorization_fixture(db_session):
    return await seed_federated_authorization_fixture(db_session)


def _filter_test_assembly(items: list[dict], *, audience: str | None = None) -> list[dict]:
    del audience
    result: list[dict] = []
    for item in items:
        if item.get("route_name") == "AssemblyExcluded":
            continue
        filtered = dict(item)
        filtered["children"] = _filter_test_assembly(item.get("children") or [])
        result.append(filtered)
    return result


async def seed_federated_authorization_fixture(db) -> FederatedAuthorizationFixture:
    suffix = uuid4().hex[:8]
    site = SiteModel(code=f"fa{suffix}", name=f"联邦授权站点{suffix}", status=0)
    db.add(site)
    await db.flush()

    package = PackageModel(
        site_id=site.id,
        name=f"联邦授权套餐{suffix}",
        code=f"pkg{suffix}",
        status=0,
    )
    db.add(package)
    await db.flush()

    tenant = TenantModel(
        site_id=site.id,
        package_id=package.id,
        name=f"联邦授权租户{suffix}",
        code=f"tenant{suffix}",
        status=0,
    )
    db.add(tenant)
    await db.flush()

    dept = DeptModel(
        tenant_id=tenant.id,
        name=f"授权部门{suffix}",
        code=f"dept{suffix}",
        order=1,
        status=0,
    )
    position = PositionModel(
        tenant_id=tenant.id,
        name=f"授权岗位{suffix}",
        code=f"position{suffix}",
        order=1,
        status=0,
    )
    db.add_all([dept, position])
    await db.flush()

    allowed_menu = MenuModel(
        name=f"允许菜单{suffix}",
        title=f"允许菜单{suffix}",
        type=2,
        order=1,
        route_name=f"FederatedAllowed{suffix}",
        route_path=f"/federated-allowed-{suffix}",
        component_path="module_system/user/index",
        client="pc",
        scope="tenant",
        status=0,
    )
    outside_package_menu = MenuModel(
        name=f"套餐外菜单{suffix}",
        title=f"套餐外菜单{suffix}",
        type=2,
        order=2,
        route_name=f"OutsidePackage{suffix}",
        route_path=f"/outside-package-{suffix}",
        component_path="module_system/user/index",
        client="pc",
        scope="tenant",
        status=0,
    )
    assembly_excluded_menu = MenuModel(
        name=f"装配外菜单{suffix}",
        title=f"装配外菜单{suffix}",
        type=2,
        order=3,
        route_name="AssemblyExcluded",
        route_path=f"/assembly-excluded-{suffix}",
        component_path="module_system/user/index",
        client="pc",
        scope="tenant",
        status=0,
    )
    db.add_all([allowed_menu, outside_package_menu, assembly_excluded_menu])
    await db.flush()
    db.add_all(
        [
            PackageMenuModel(package_id=package.id, menu_id=allowed_menu.id),
            PackageMenuModel(package_id=package.id, menu_id=assembly_excluded_menu.id),
        ]
    )

    authorized_role = RoleModel(
        tenant_id=tenant.id,
        name=f"有效角色{suffix}",
        code=f"authorized{suffix}",
        order=1,
        status=0,
        data_scope=4,
    )
    disabled_role = RoleModel(
        tenant_id=tenant.id,
        name=f"禁用角色{suffix}",
        code=f"disabled{suffix}",
        order=2,
        status=1,
        data_scope=4,
    )
    outside_role = RoleModel(
        tenant_id=tenant.id,
        name=f"越界角色{suffix}",
        code=f"outside{suffix}",
        order=3,
        status=0,
        data_scope=4,
    )
    db.add_all([authorized_role, disabled_role, outside_role])
    await db.flush()
    db.add_all(
        [
            RoleMenusModel(role_id=authorized_role.id, menu_id=allowed_menu.id),
            RoleMenusModel(role_id=disabled_role.id, menu_id=allowed_menu.id),
            RoleMenusModel(role_id=outside_role.id, menu_id=outside_package_menu.id),
            RoleMenusModel(role_id=outside_role.id, menu_id=assembly_excluded_menu.id),
        ]
    )

    admin = UserModel(
        tenant_id=tenant.id,
        username=f"tenant_admin_{suffix}",
        password="not-used",
        name=f"租户管理员{suffix}",
        dept_id=dept.id,
        auth_source="local",
        password_login_enabled=True,
        is_superuser=False,
        status=0,
    )
    users = [
        UserModel(
            tenant_id=tenant.id,
            username=f"federated_authorized_{suffix}",
            password="not-used",
            name=f"已授权用户{suffix}",
            dept_id=dept.id,
            auth_source="federated",
            password_login_enabled=False,
            is_superuser=False,
            status=0,
        ),
        UserModel(
            tenant_id=tenant.id,
            username=f"federated_no_role_{suffix}",
            password="not-used",
            name=f"无角色用户{suffix}",
            dept_id=dept.id,
            auth_source="federated",
            password_login_enabled=False,
            is_superuser=False,
            status=0,
        ),
        UserModel(
            tenant_id=tenant.id,
            username=f"federated_disabled_{suffix}",
            password="not-used",
            name=f"禁用角色用户{suffix}",
            dept_id=dept.id,
            auth_source="federated",
            password_login_enabled=False,
            is_superuser=False,
            status=0,
        ),
        UserModel(
            tenant_id=tenant.id,
            username=f"federated_outside_{suffix}",
            password="not-used",
            name=f"越界用户{suffix}",
            dept_id=dept.id,
            auth_source="federated",
            password_login_enabled=False,
            is_superuser=False,
            status=0,
        ),
    ]
    db.add_all([admin, *users])
    await db.flush()
    authorized_user, no_role_user, disabled_role_user, outside_package_user = users
    db.add_all(
        [
            UserRolesModel(user_id=admin.id, role_id=authorized_role.id),
            UserRolesModel(user_id=authorized_user.id, role_id=authorized_role.id),
            UserRolesModel(user_id=disabled_role_user.id, role_id=disabled_role.id),
            UserRolesModel(user_id=outside_package_user.id, role_id=outside_role.id),
            UserPositionsModel(user_id=authorized_user.id, position_id=position.id),
            UserPositionsModel(user_id=no_role_user.id, position_id=position.id),
            UserPositionsModel(user_id=disabled_role_user.id, position_id=position.id),
            UserPositionsModel(user_id=outside_package_user.id, position_id=position.id),
        ]
    )
    await db.flush()

    load_user = (
        select(UserModel)
        .options(
            selectinload(UserModel.roles).selectinload(RoleModel.menus),
            selectinload(UserModel.dept),
            selectinload(UserModel.positions),
            selectinload(UserModel.created_by),
            selectinload(UserModel.updated_by),
            selectinload(UserModel.deleted_by),
            selectinload(UserModel.tenant_by),
        )
    )
    loaded_admin = (await db.scalars(load_user.where(UserModel.id == admin.id))).one()
    loaded_authorized = (
        await db.scalars(load_user.where(UserModel.id == authorized_user.id))
    ).one()
    auth = AuthSchema(
        db=db,
        user=loaded_admin,
        tenant_id=tenant.id,
        site_id=site.id,
        check_data_scope=False,
    )
    authorized_auth = AuthSchema(
        db=db,
        user=loaded_authorized,
        tenant_id=tenant.id,
        site_id=site.id,
        check_data_scope=False,
    )
    return FederatedAuthorizationFixture(
        auth=auth,
        authorized_auth=authorized_auth,
        authorized_user_id=authorized_user.id,
        no_role_user_id=no_role_user.id,
        disabled_role_user_id=disabled_role_user.id,
        outside_package_user_id=outside_package_user.id,
        authorized_username=authorized_user.username,
        pending_username=no_role_user.username,
        dept_id=dept.id,
        role_id=authorized_role.id,
        position_id=position.id,
    )


@pytest.mark.asyncio
async def test_effective_menu_resolver_intersects_role_package_and_assembly(
    db_session,
    monkeypatch,
):
    fixture = await seed_federated_authorization_fixture(db_session)
    monkeypatch.setattr(
        "app.api.v1.module_system.user.authorization.filter_menu_tree_by_assembly",
        _filter_test_assembly,
    )
    resolver = UserAuthorizationResolver(fixture.auth)

    allowed = await resolver.authorized_federated_user_ids()

    assert fixture.authorized_user_id in allowed
    assert fixture.no_role_user_id not in allowed
    assert fixture.disabled_role_user_id not in allowed
    assert fixture.outside_package_user_id not in allowed


@pytest.mark.asyncio
async def test_current_info_and_list_status_use_same_effective_menu_rule(
    db_session,
    monkeypatch,
):
    fixture = await seed_federated_authorization_fixture(db_session)
    monkeypatch.setattr(
        "app.api.v1.module_system.user.authorization.filter_menu_tree_by_assembly",
        _filter_test_assembly,
    )
    monkeypatch.setattr(
        "app.api.v1.module_system.user.service.filter_menu_tree_by_assembly",
        _filter_test_assembly,
    )

    current = await UserService(fixture.authorized_auth).current_info()
    allowed = await UserAuthorizationResolver(fixture.auth).authorized_federated_user_ids()

    assert bool(current.menus) is (fixture.authorized_user_id in allowed)


@pytest.mark.asyncio
async def test_soft_deleted_role_does_not_authorize_federated_user(db_session):
    fixture = await seed_federated_authorization_fixture(db_session)
    role = fixture.authorized_auth.user.roles[0]
    role.is_deleted = True
    await db_session.flush()
    resolver = UserAuthorizationResolver(fixture.authorized_auth)

    effective = await resolver.effective_menu_ids_for_user(
        fixture.authorized_auth.user
    )
    authorized = await resolver.authorized_federated_user_ids()

    assert effective == set()
    assert fixture.authorized_user_id not in authorized


@pytest.mark.asyncio
async def test_soft_deleted_menu_does_not_authorize_federated_user(db_session):
    fixture = await seed_federated_authorization_fixture(db_session)
    menu = fixture.authorized_auth.user.roles[0].menus[0]
    menu.is_deleted = True
    await db_session.flush()
    resolver = UserAuthorizationResolver(fixture.authorized_auth)

    effective = await resolver.effective_menu_ids_for_user(
        fixture.authorized_auth.user
    )
    authorized = await resolver.authorized_federated_user_ids()

    assert effective == set()
    assert fixture.authorized_user_id not in authorized


@pytest.mark.asyncio
async def test_user_page_exposes_federated_authorization_status(
    federated_authorization_fixture,
    monkeypatch,
):
    fixture = federated_authorization_fixture
    monkeypatch.setattr(
        "app.api.v1.module_system.user.authorization.filter_menu_tree_by_assembly",
        _filter_test_assembly,
    )

    result = await UserService(fixture.auth).page(
        page_no=1,
        page_size=20,
        search=UserQueryParam(auth_source="federated"),
    )

    by_name = {item["username"]: item for item in result.items}
    assert result.total == 4
    assert by_name[fixture.authorized_username]["authorization_status"] == "authorized"
    assert by_name[fixture.pending_username]["authorization_status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization_status", "expected_total", "expected_username"),
    [
        (UserAuthorizationStatus.AUTHORIZED, 1, "authorized_username"),
        (UserAuthorizationStatus.PENDING, 3, "pending_username"),
    ],
)
async def test_user_page_filters_authorization_status_before_pagination(
    federated_authorization_fixture,
    monkeypatch,
    authorization_status,
    expected_total,
    expected_username,
):
    fixture = federated_authorization_fixture
    monkeypatch.setattr(
        "app.api.v1.module_system.user.authorization.filter_menu_tree_by_assembly",
        _filter_test_assembly,
    )

    result = await UserService(fixture.auth).page(
        page_no=1,
        page_size=1,
        search=UserQueryParam(authorization_status=authorization_status),
    )

    assert result.total == expected_total
    assert len(result.items) == 1
    assert result.items[0]["auth_source"] == "federated"
    assert result.items[0]["authorization_status"] == authorization_status
    assert result.items[0]["username"] == getattr(fixture, expected_username)


@pytest.mark.asyncio
async def test_local_user_authorization_status_is_null(
    federated_authorization_fixture,
):
    fixture = federated_authorization_fixture

    result = await UserService(fixture.auth).page(
        page_no=1,
        page_size=20,
        search=UserQueryParam(auth_source="local"),
    )

    assert result.total == 1
    assert all(item["authorization_status"] is None for item in result.items)


@pytest.mark.asyncio
async def test_user_page_authorization_status_is_tenant_scoped(
    db_session,
    monkeypatch,
):
    first = await seed_federated_authorization_fixture(db_session)
    second = await seed_federated_authorization_fixture(db_session)
    monkeypatch.setattr(
        "app.api.v1.module_system.user.authorization.filter_menu_tree_by_assembly",
        _filter_test_assembly,
    )

    result = await UserService(first.auth).page(
        page_no=1,
        page_size=20,
        search=UserQueryParam(auth_source="federated"),
    )

    usernames = {item["username"] for item in result.items}
    assert result.total == 4
    assert first.authorized_username in usernames
    assert second.authorized_username not in usernames


@pytest.mark.asyncio
async def test_authorized_filter_with_empty_include_ids_returns_empty_page(
    federated_authorization_fixture,
    monkeypatch,
):
    fixture = federated_authorization_fixture
    monkeypatch.setattr(
        "app.api.v1.module_system.user.authorization.filter_menu_tree_by_assembly",
        lambda items, *, audience=None: [],
    )

    result = await UserService(fixture.auth).page(
        page_no=1,
        page_size=20,
        search=UserQueryParam(
            authorization_status=UserAuthorizationStatus.AUTHORIZED,
        ),
    )

    assert result.total == 0
    assert result.items == []
