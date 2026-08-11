# 联邦用户本地授权闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让启用中控 SSO 的业务系统在现有用户管理中准确识别、筛选并授权联邦影子账号，同时保持部门、角色和菜单权限完全由目标系统维护。

**Architecture:** 后端新增一个用户授权状态解析器，复用租户套餐与 Assembly 菜单规则，并把 `authorization_status` 注入现有用户分页响应和查询条件。前端扩展现有用户管理筛选、标签及编辑抽屉；等待页使用一次完整顶层导航重新建立菜单和动态路由，不增加轮询或第二套用户页面。

**Tech Stack:** FastAPI、SQLAlchemy 2 AsyncSession、Pydantic v2、pytest、Vue 3、TypeScript、Element Plus、Pinia、Vue Router、Vitest。

---

## 实施前约束

- 工作仓库：`/Users/chou/code/FastapiAdmin`
- 基线设计：`docs/superpowers/specs/2026-08-11-federated-user-authorization-design.md`
- 只修改本计划列出的文件；保留 `.superpowers/`、`.understand-anything/` 和其他未跟踪文件。
- 先运行失败测试，再写最小实现；每个任务独立提交，不 push。
- 不增加数据库迁移，不同步中控部门或角色，不创建新的待授权页面。

## 文件结构

- Create: `backend/app/api/v1/module_system/user/authorization.py`
  - 集中解析当前租户与 Assembly 下的有效菜单 ID，并批量找出已授权联邦用户 ID。
- Modify: `backend/app/api/v1/module_system/user/schema.py`
  - 定义查询枚举、列表查询字段和输出字段。
- Modify: `backend/app/api/v1/module_system/user/service.py`
  - 复用授权解析器、增强分页结果、执行联邦字段更新边界。
- Modify: `backend/app/api/v1/module_system/user/crud.py`
  - 支持在分页前追加用户 ID 条件；不改通用 `CRUDBase`。
- Create: `backend/tests/test_federated_user_authorization.py`
  - 覆盖状态、筛选、分页、套餐/角色/菜单变化、租户隔离和字段更新边界。
- Modify: `frontend/web/src/api/module_system/user.ts`
  - 增加授权状态类型、查询字段和响应字段。
- Modify: `frontend/web/src/views/module_system/user/index.vue`
  - 增加筛选、标签、“去授权”和联邦字段只读行为。
- Create: `frontend/web/src/views/module_system/user/user-authorization.ts`
  - 保存筛选映射、授权状态和只读字段判定纯函数，避免测试依赖大型 SFC。
- Create: `frontend/web/src/views/module_system/auth/control-waiting/retry.ts`
  - 生成并执行返回根路由的顶层刷新，便于独立测试。
- Modify: `frontend/web/src/views/module_system/auth/control-waiting/index.vue`
  - 完善等待文案、用户标识和重新检查行为。
- Create: `frontend/web/src/__tests__/federated-user-authorization.spec.ts`
  - 覆盖 API 类型契约、用户管理行为和等待页刷新。
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_sso_integration.py`
  - 以 Control 为 Provider、当前框架为 Target，在真实双库 SSO 闭环中加入待授权到已授权状态变化。

### Task 1: 建立共享的有效菜单解析器

**Files:**
- Create: `backend/app/api/v1/module_system/user/authorization.py`
- Modify: `backend/app/api/v1/module_system/user/service.py:236-283`
- Test: `backend/tests/test_federated_user_authorization.py`

- [ ] **Step 1: 写授权解析器失败测试**

在新测试文件中构造最小租户、两个联邦用户、启用角色、禁用角色、套餐菜单和 Assembly 菜单。至少加入以下测试名称与断言：

```python
from dataclasses import dataclass

import pytest_asyncio

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
async def db_session():
    async with async_db_session() as db:
        yield db
        await db.rollback()


@pytest_asyncio.fixture
async def federated_authorization_fixture(db_session):
    return await seed_federated_authorization_fixture(db_session)


@pytest.mark.asyncio
async def test_effective_menu_resolver_intersects_role_package_and_assembly(db_session):
    fixture = await seed_federated_authorization_fixture(db_session)
    resolver = UserAuthorizationResolver(fixture.auth)

    allowed = await resolver.authorized_federated_user_ids()

    assert fixture.authorized_user_id in allowed
    assert fixture.no_role_user_id not in allowed
    assert fixture.disabled_role_user_id not in allowed
    assert fixture.outside_package_user_id not in allowed


@pytest.mark.asyncio
async def test_current_info_and_list_status_use_same_effective_menu_rule(db_session):
    fixture = await seed_federated_authorization_fixture(db_session)

    current = await UserService(fixture.authorized_auth).current_info()
    allowed = await UserAuthorizationResolver(fixture.auth).authorized_federated_user_ids()

    assert bool(current.menus) is (fixture.authorized_user_id in allowed)
```

`seed_federated_authorization_fixture()` 必须在同一测试文件中完整实现并返回上面的 dataclass：显式创建当前 `site_id`、当前 `tenant_id`、四个联邦用户、部门、岗位、启用角色、禁用角色、角色菜单关联和租户套餐菜单关联；所有 ID 取自 `flush()` 后对象，不得依赖固定数据库 ID。`authorized_auth` 使用已授权联邦用户，`auth` 使用该租户管理员。每个名字附加 `uuid4().hex[:8]`，避免 session 级测试数据库重名。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_user_authorization.py -q
```

Expected: collection 失败，明确指出 `UserAuthorizationResolver` 或 `authorization.py` 尚不存在。

- [ ] **Step 3: 实现解析器最小接口**

`authorization.py` 提供稳定的小接口：

```python
from __future__ import annotations

from sqlalchemy import select

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.menu.schema import MenuOutSchema
from app.api.v1.module_platform.package.service import PackageService
from app.api.v1.module_system.role.model import RoleMenusModel, RoleModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.core.assembly import filter_menu_tree_by_assembly
from app.core.base_schema import AuthSchema
from app.utils.common_util import traversal_to_tree


class UserAuthorizationResolver:
    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def effective_tenant_menu_ids(self) -> set[int]:
        if self.auth.tenant_id is None:
            return set()
        package_ids = set(
            await PackageService(self.auth).get_tenant_available_menu_ids(self.auth.tenant_id)
        )
        if not package_ids:
            return set()
        stmt = (
            select(MenuModel)
            .where(
                MenuModel.id.in_(package_ids),
                MenuModel.status == 0,
                MenuModel.client == "pc",
                MenuModel.scope == "tenant",
            )
            .order_by(MenuModel.order.asc())
        )
        menus = (await self.auth.db.scalars(stmt)).all()
        tree = traversal_to_tree(
            [MenuOutSchema.model_validate(menu).model_dump() for menu in menus]
        )
        filtered = filter_menu_tree_by_assembly(tree, audience="tenant")
        return self._collect_ids(filtered)

    async def effective_menu_ids_for_user(self, user: UserModel) -> set[int]:
        allowed_ids = await self.effective_tenant_menu_ids()
        if not allowed_ids:
            return set()
        if user.is_superuser:
            return allowed_ids
        return {
            menu.id
            for role in user.roles or []
            if role.status == 0
            for menu in role.menus or []
            if menu.id in allowed_ids and menu.status == 0 and menu.client == "pc"
        }

    async def authorized_federated_user_ids(self) -> set[int]:
        allowed_ids = await self.effective_tenant_menu_ids()
        if not allowed_ids or self.auth.tenant_id is None:
            return set()
        stmt = (
            select(UserRolesModel.user_id)
            .join(RoleModel, RoleModel.id == UserRolesModel.role_id)
            .join(RoleMenusModel, RoleMenusModel.role_id == RoleModel.id)
            .join(UserModel, UserModel.id == UserRolesModel.user_id)
            .where(
                UserModel.tenant_id == self.auth.tenant_id,
                UserModel.auth_source == "federated",
                RoleModel.tenant_id == self.auth.tenant_id,
                RoleModel.status == 0,
                RoleMenusModel.menu_id.in_(allowed_ids),
            )
            .distinct()
        )
        ids = set((await self.auth.db.scalars(stmt)).all())
        super_stmt = select(UserModel.id).where(
            UserModel.tenant_id == self.auth.tenant_id,
            UserModel.auth_source == "federated",
            UserModel.is_superuser.is_(True),
        )
        ids.update((await self.auth.db.scalars(super_stmt)).all())
        return ids

    async def federated_user_ids(self) -> set[int]:
        if self.auth.tenant_id is None:
            return set()
        stmt = select(UserModel.id).where(
            UserModel.tenant_id == self.auth.tenant_id,
            UserModel.auth_source == "federated",
        )
        return set((await self.auth.db.scalars(stmt)).all())

    @classmethod
    def _collect_ids(cls, items: list[dict]) -> set[int]:
        result: set[int] = set()
        for item in items:
            if isinstance(item.get("id"), int):
                result.add(item["id"])
            result.update(cls._collect_ids(item.get("children") or []))
        return result
```

实现使用显式 `select(MenuModel)`，避免 `MenuCRUD` 的当前操作者权限过滤收窄管理员查看其他用户授权状态；套餐和 Assembly 判定仍分别复用 `PackageService` 与 `filter_menu_tree_by_assembly`，不得复制其规则。

将 `UserService.current_info()` 的普通租户分支改为调用 `effective_menu_ids_for_user(self.auth.user)`，平台全局分支保持现状。这样登录菜单与列表状态共用相同规则。

- [ ] **Step 4: 运行聚焦测试**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_user_authorization.py tests/test_api_module_system.py -q
uv run ruff check app/api/v1/module_system/user tests/test_federated_user_authorization.py
```

Expected: 新测试通过；现有系统用户 API 测试通过；Ruff 输出 `All checks passed!`。

- [ ] **Step 5: 提交共享解析器**

```bash
git add backend/app/api/v1/module_system/user/authorization.py \
  backend/app/api/v1/module_system/user/service.py \
  backend/tests/test_federated_user_authorization.py
git commit -m "feat: 统一解析联邦用户有效菜单"
```

### Task 2: 在现有用户分页中增加授权状态与服务端筛选

**Files:**
- Modify: `backend/app/api/v1/module_system/user/schema.py:339-398`
- Modify: `backend/app/api/v1/module_system/user/crud.py:16-32`
- Modify: `backend/app/api/v1/module_system/user/service.py:119-141`
- Test: `backend/tests/test_federated_user_authorization.py`

- [ ] **Step 1: 写分页契约失败测试**

加入 API 级测试：

```python
def test_user_page_exposes_and_filters_federated_authorization_status(
    test_client,
    auth_headers,
    federated_authorization_fixture,
):
    all_users = test_client.get(
        "/system/user/list",
        headers=auth_headers,
        params={"auth_source": "federated", "page_no": 1, "page_size": 20},
    )
    assert all_users.status_code == 200
    by_name = {item["username"]: item for item in all_users.json()["data"]["items"]}
    assert by_name[federated_authorization_fixture.pending_username]["authorization_status"] == "pending"
    assert by_name[federated_authorization_fixture.authorized_username]["authorization_status"] == "authorized"

    pending = test_client.get(
        "/system/user/list",
        headers=auth_headers,
        params={"authorization_status": "pending", "page_no": 1, "page_size": 1},
    )
    assert pending.status_code == 200
    assert pending.json()["data"]["total"] == 1
    assert pending.json()["data"]["items"][0]["auth_source"] == "federated"


def test_local_user_authorization_status_is_null(test_client, auth_headers):
    response = test_client.get(
        "/system/user/list",
        headers=auth_headers,
        params={"auth_source": "local", "page_no": 1, "page_size": 20},
    )
    assert response.status_code == 200
    assert all(item["authorization_status"] is None for item in response.json()["data"]["items"])
```

另加一项第二租户夹具，断言当前租户的 `pending/authorized` 结果不包含另一个租户用户。

- [ ] **Step 2: 运行新增测试并确认 RED**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_user_authorization.py -q
```

Expected: 失败于未知查询字段或响应缺少 `authorization_status`。

- [ ] **Step 3: 扩展 schema**

在 `schema.py` 增加：

```python
from enum import StrEnum


class UserAuthorizationStatus(StrEnum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
```

`UserOutSchema` 增加：

```python
authorization_status: UserAuthorizationStatus | None = Field(
    default=None,
    description="联邦账号在当前租户的本地菜单授权状态",
)
```

`UserQueryParam` 增加原始查询字段，并在 `__post_init__` 中只把 ORM 字段交给通用 CRUD：

```python
auth_source: str | None = Query(None, pattern=r"^(local|federated)$")
authorization_status: UserAuthorizationStatus | None = Query(None)
```

保留 `authorization_status` 给 `UserService.page()` 消费，随后从通用 `search` 字典中移除，禁止通用 CRUD 把它当作数据库列。

- [ ] **Step 4: 在分页前追加授权用户 ID 条件**

在 `UserCRUD` 增加一个窄接口，不修改全局 `CRUDBase`：

```python
async def page_with_user_ids(
    self,
    *,
    offset: int,
    limit: int,
    order_by: list[dict[str, str]],
    search: dict,
    include_ids: set[int] | None = None,
):
    scoped = dict(search)
    if include_ids is not None:
        scoped["id"] = ("in", sorted(include_ids) or [-1])
    return await self.page(
        offset=offset,
        limit=limit,
        order_by=order_by,
        search=scoped,
        out_schema=UserOutSchema,
    )
```

待授权 ID 使用“当前租户全部联邦用户 ID - 已授权联邦用户 ID”得到，避免为通用 `QueueEnum` 增加新的排除运算符，也不要拼接原始 SQL 字符串。

`UserService.page()` 的核心流程应为：

```python
authorization_status = search.authorization_status if search else None
search_dict = vars(search).copy() if search else {}
search_dict.pop("authorization_status", None)

resolver = UserAuthorizationResolver(self.auth)
authorized_ids = await resolver.authorized_federated_user_ids()
include_ids: set[int] | None = None
if authorization_status == UserAuthorizationStatus.AUTHORIZED:
    search_dict["auth_source"] = "federated"
    include_ids = authorized_ids
elif authorization_status == UserAuthorizationStatus.PENDING:
    search_dict["auth_source"] = "federated"
    federated_ids = await resolver.federated_user_ids()
    include_ids = federated_ids - authorized_ids

result = await UserCRUD(self.auth).page_with_user_ids(
    offset=(page_no - 1) * page_size,
    limit=page_size,
    order_by=order_by or [{"id": "asc"}],
    search=search_dict,
    include_ids=include_ids,
)
for item in result.items:
    if item["auth_source"] == "federated":
        item["authorization_status"] = (
            UserAuthorizationStatus.AUTHORIZED
            if item["id"] in authorized_ids
            else UserAuthorizationStatus.PENDING
        )
return result
```

实际实现要保持 `PageResultSchema` 当前的 dict/model 类型一致。授权状态筛选必须进入 count 和 data 的共同 where 条件，禁止先分页再用 Python 过滤。

- [ ] **Step 5: 验证分页与租户隔离**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_user_authorization.py \
  tests/test_tenant_context_scoping.py \
  tests/test_multitenant_hardening.py -q
```

Expected: 全部通过，`pending` 的 `total`、`items` 和租户边界一致。

- [ ] **Step 6: 提交分页契约**

```bash
git add backend/app/api/v1/module_system/user/schema.py \
  backend/app/api/v1/module_system/user/crud.py \
  backend/app/api/v1/module_system/user/service.py \
  backend/tests/test_federated_user_authorization.py
git commit -m "feat: 增加联邦用户授权状态筛选"
```

### Task 3: 限制联邦账号的本地可编辑字段

**Files:**
- Modify: `backend/app/api/v1/module_system/user/service.py:170-214`
- Test: `backend/tests/test_federated_user_authorization.py`

- [ ] **Step 1: 写字段边界失败测试**

```python
@pytest.mark.asyncio
async def test_federated_user_rejects_changed_central_identity_fields(
    db_session,
    federated_authorization_fixture,
):
    fixture = federated_authorization_fixture
    service = UserService(fixture.auth)
    user = await db_session.get(UserModel, fixture.no_role_user_id)
    payload = UserUpdateSchema.model_validate(
        {
            **UserOutSchema.model_validate(user).model_dump(),
            "name": "目标系统私改姓名",
            "role_ids": [],
            "position_ids": [],
        }
    )
    with pytest.raises(CustomException, match="统一登录账号的身份资料由中控维护"):
        await service.update(user.id, payload)


@pytest.mark.asyncio
async def test_federated_user_can_update_local_department_roles_and_positions(
    db_session,
    federated_authorization_fixture,
):
    fixture = federated_authorization_fixture
    service = UserService(fixture.auth)
    user = await db_session.get(UserModel, fixture.no_role_user_id)
    result = await service.update(
        user.id,
        UserUpdateSchema(
            username=user.username,
            name=user.name,
            mobile=user.mobile,
            email=user.email,
            status=user.status,
            dept_id=fixture.dept_id,
            role_ids=[fixture.role_id],
            position_ids=[fixture.position_id],
        ),
    )
    assert result.dept.id == fixture.dept_id
    assert [role.id for role in result.roles] == [fixture.role_id]
    assert [position.id for position in result.positions] == [fixture.position_id]
```

再增加本地账号更新姓名和清空全部角色仍成功的回归测试。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_user_authorization.py -q
```

Expected: 联邦身份字段仍可修改，或空 `role_ids` 没有清空角色。

- [ ] **Step 3: 实现联邦字段保护与空数组语义**

在 `UserService` 中集中声明受控字段：

```python
FEDERATED_CENTRAL_FIELDS = {
    "username",
    "name",
    "mobile",
    "email",
    "avatar",
    "status",
    "auth_source",
    "password_login_enabled",
}


@staticmethod
def _reject_federated_identity_changes(user: UserModel, data: UserUpdateSchema) -> None:
    if user.auth_source != "federated":
        return
    incoming = data.model_dump(exclude_unset=True)
    changed = {
        field
        for field in FEDERATED_CENTRAL_FIELDS & incoming.keys()
        if incoming[field] != getattr(user, field)
    }
    if changed:
        raise CustomException(
            msg="统一登录账号的身份资料由中控维护",
            status_code=400,
        )
```

在唯一性检查和写数据库之前调用该方法。传给 `UserCRUD.update()` 时排除 `role_ids` 和 `position_ids`，关系字段由专用方法维护：

```python
fields_set = data.model_fields_set
user_payload = data.model_dump(
    exclude_unset=True,
    exclude={"role_ids", "position_ids"},
)
new_user = await UserCRUD(self.auth).update(id=id, data=user_payload)

if "role_ids" in fields_set:
    role_ids = data.role_ids or []
    # 非空时先校验存在且启用；空列表表示明确清空。
    await UserCRUD(self.auth).set_user_roles([id], role_ids)
if "position_ids" in fields_set:
    position_ids = data.position_ids or []
    # 非空时先校验存在且启用；空列表表示明确清空。
    await UserCRUD(self.auth).set_user_positions([id], position_ids)
```

不要限制 `dept_id`、`role_ids`、`position_ids`、`description` 等目标系统字段。保留现有超级管理员保护。

- [ ] **Step 4: 运行用户与 SSO 回归**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_user_authorization.py \
  tests/test_api_module_system.py \
  tests/test_control_sso_client.py \
  tests/test_password_reset_email.py -q
uv run ruff check app/api/v1/module_system/user tests/test_federated_user_authorization.py
```

Expected: 全部通过；Ruff 输出 `All checks passed!`。

- [ ] **Step 5: 提交字段保护**

```bash
git add backend/app/api/v1/module_system/user/service.py \
  backend/tests/test_federated_user_authorization.py
git commit -m "fix: 限制联邦账号本地身份字段修改"
```

### Task 4: 完善用户管理筛选、标签与去授权入口

**Files:**
- Modify: `frontend/web/src/api/module_system/user.ts:70-180`
- Modify: `frontend/web/src/views/module_system/user/index.vue:252-633`
- Create: `frontend/web/src/__tests__/federated-user-authorization.spec.ts`

- [ ] **Step 1: 写前端契约失败测试**

新建 Vitest：

```typescript
import { describe, expect, it } from "vitest";
import {
  buildUserReplaceParams,
  isPendingFederatedUser,
  isFederatedIdentityFieldReadonly,
} from "@/views/module_system/user/user-authorization";

describe("federated user authorization", () => {
  it("forwards source and authorization filters", () => {
    expect(
      buildUserReplaceParams({
        auth_source: "federated",
        authorization_status: "pending",
      })
    ).toMatchObject({ auth_source: "federated", authorization_status: "pending" });
  });

  it("shows grant action only for pending federated users", () => {
    expect(
      isPendingFederatedUser({
        id: 9,
        auth_source: "federated",
        authorization_status: "pending",
      })
    ).toBe(true);
    expect(
      isPendingFederatedUser({
        id: 10,
        auth_source: "local",
        authorization_status: null,
      })
    ).toBe(false);
  });

  it("keeps central fields readonly only for federated users", () => {
    expect(isFederatedIdentityFieldReadonly("name", "federated")).toBe(true);
    expect(isFederatedIdentityFieldReadonly("role_ids", "federated")).toBe(false);
    expect(isFederatedIdentityFieldReadonly("name", "local")).toBe(false);
  });
});
```

为避免从大型 SFC 导出逻辑，新建同目录纯函数文件：

- Create: `frontend/web/src/views/module_system/user/user-authorization.ts`

该文件只保存筛选映射、标签和行操作判定；不导入 Pinia、Router 或 Element Plus。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm exec vitest run src/__tests__/federated-user-authorization.spec.ts
```

Expected: 失败于 `user-authorization.ts` 不存在或类型字段缺失。

- [ ] **Step 3: 扩展 API 类型**

在 `user.ts` 增加：

```typescript
export type UserAuthSource = "local" | "federated";
export type UserAuthorizationStatus = "pending" | "authorized";

export interface UserPageQuery extends PageQuery, UserByQueryParams, TenantByQueryParams {
  username?: string;
  name?: string;
  mobile?: string;
  email?: string;
  dept_id?: number;
  auth_source?: UserAuthSource;
  authorization_status?: UserAuthorizationStatus;
}
```

`UserInfo` 增加：

```typescript
auth_source?: UserAuthSource;
password_login_enabled?: boolean;
authorization_status?: UserAuthorizationStatus | null;
```

- [ ] **Step 4: 实现纯函数与用户管理 UI**

`user-authorization.ts` 至少提供：

```typescript
import type {
  UserAuthSource,
  UserAuthorizationStatus,
  UserInfo,
} from "@/api/module_system/user";

export interface UserAuthorizationSearch {
  username?: string;
  name?: string;
  status?: number;
  auth_source?: UserAuthSource;
  authorization_status?: UserAuthorizationStatus;
  created_id?: number;
  created_time?: string[];
}

export function buildUserReplaceParams(
  state: UserAuthorizationSearch
): Record<string, unknown> {
  return {
    username: state.username,
    name: state.name,
    status: state.status,
    auth_source: state.auth_source,
    authorization_status: state.authorization_status,
    created_id: state.created_id,
    created_time:
      Array.isArray(state.created_time) && state.created_time.length === 2
        ? state.created_time
        : undefined,
  };
}

export const FEDERATED_READONLY_FIELDS = new Set([
  "username",
  "name",
  "mobile",
  "email",
  "avatar",
  "status",
]);

export function isFederatedIdentityFieldReadonly(
  field: string,
  source?: UserAuthSource
): boolean {
  return source === "federated" && FEDERATED_READONLY_FIELDS.has(field);
}

export function isPendingFederatedUser(user: UserInfo): boolean {
  return user.auth_source === "federated" && user.authorization_status === "pending";
}

export function authorizationLabel(status?: UserAuthorizationStatus | null): string {
  if (status === "pending") return "待授权";
  if (status === "authorized") return "已授权";
  return "—";
}
```

在 `index.vue` 中：

- 扩展 `UserSearchForm` 和 `buildUserReplaceParams()`，传递 `auth_source`、`authorization_status`。
- 增加两个 select 搜索项。
- 增加“账号来源”“授权状态”两列，使用 Element Plus Tag 或现有状态列能力。
- `pending` 联邦行在操作列表首位增加 `key: "grant"`、`label: "去授权"`，回调仍调用 `handleOpenDialog("update", id)`。
- 联邦行不显示“重置密码”。
- 编辑联邦账号时，中央字段对应 FormItem 的 `props.disabled` 为 `true`，并在抽屉顶部显示“身份资料由中控统一维护；本系统只维护部门、岗位和角色”。
- 本地账号的字段、按钮和校验保持原样。

- [ ] **Step 5: 运行前端聚焦门禁**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm exec vitest run src/__tests__/federated-user-authorization.spec.ts \
  src/__tests__/control-sso-client.spec.ts
pnpm type-check
pnpm exec eslint src/api/module_system/user.ts \
  src/views/module_system/user/index.vue \
  src/views/module_system/user/user-authorization.ts \
  src/__tests__/federated-user-authorization.spec.ts
pnpm exec stylelint src/views/module_system/user/index.vue
pnpm exec prettier --check src/api/module_system/user.ts \
  src/views/module_system/user/index.vue \
  src/views/module_system/user/user-authorization.ts \
  src/__tests__/federated-user-authorization.spec.ts
```

Expected: 测试和 type-check 通过，ESLint/Stylelint/Prettier 均退出 0。

- [ ] **Step 6: 提交用户管理交互**

```bash
git add frontend/web/src/api/module_system/user.ts \
  frontend/web/src/views/module_system/user/index.vue \
  frontend/web/src/views/module_system/user/user-authorization.ts \
  frontend/web/src/__tests__/federated-user-authorization.spec.ts
git commit -m "feat: 完善统一登录账号本地授权入口"
```

### Task 5: 修复等待页重新检查闭环

**Files:**
- Create: `frontend/web/src/views/module_system/auth/control-waiting/retry.ts`
- Modify: `frontend/web/src/views/module_system/auth/control-waiting/index.vue:1-30`
- Test: `frontend/web/src/__tests__/federated-user-authorization.spec.ts`

- [ ] **Step 1: 写顶层刷新失败测试**

```typescript
import { buildAuthorizationRetryUrl } from "@/views/module_system/auth/control-waiting/retry";

it("builds a root hash URL for a complete authorization recheck", () => {
  expect(
    buildAuthorizationRetryUrl(
      "http://127.0.0.1:15394/web#/auth/control/waiting",
      "/web#/"
    )
  ).toBe("http://127.0.0.1:15394/web#/");
});
```

再对组件源码或浅挂载断言：页面包含管理员路径、当前用户名，以及按钮调用 `window.location.replace` 而不是 `router.replace` 或定时器。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm exec vitest run src/__tests__/federated-user-authorization.spec.ts
```

Expected: `retry.ts` 不存在，或旧组件仍调用 `router.replace("/")`。

- [ ] **Step 3: 实现可测试的顶层刷新**

`retry.ts`：

```typescript
export function buildAuthorizationRetryUrl(currentUrl: string, rootHref: string): string {
  return new URL(rootHref, currentUrl).href;
}

export function reloadAuthorization(rootHref: string): void {
  window.location.replace(buildAuthorizationRetryUrl(window.location.href, rootHref));
}
```

等待页组件使用 `useRouter()` 和 `useUserStore()`：

```typescript
const router = useRouter();
const userStore = useUserStore();
const displayName = computed(
  () => userStore.basicInfo.name || userStore.basicInfo.username || "当前账号"
);
const account = computed(() => userStore.basicInfo.username || "—");
const retry = () => reloadAuthorization(router.resolve({ path: "/" }).href);
```

页面文案必须包含：

- “账号已创建，目标系统尚未授予有效菜单权限”
- “系统管理 → 用户管理 → 筛选待授权 → 去授权”
- 当前姓名和账号

不得显示手机、邮箱、token、启动码，不得增加 `setInterval`。

- [ ] **Step 4: 验证等待页与 SSO 回调**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm exec vitest run src/__tests__/federated-user-authorization.spec.ts \
  src/__tests__/control-sso-client.spec.ts
pnpm type-check
```

Expected: 两个测试文件全部通过，type-check 无诊断。

- [ ] **Step 5: 提交等待页闭环**

```bash
git add frontend/web/src/views/module_system/auth/control-waiting/retry.ts \
  frontend/web/src/views/module_system/auth/control-waiting/index.vue \
  frontend/web/src/__tests__/federated-user-authorization.spec.ts
git commit -m "fix: 完善统一登录待授权重新检查"
```

### Task 6: 真实双库闭环与全量发布门禁

**Files:**
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_sso_integration.py`
- Verify only: all changed backend/frontend files

- [ ] **Step 1: 扩展真实双库集成测试并确认 RED**

在现有真实 PostgreSQL、Redis、Control API、Target API 测试中追加以下顺序：

```python
# 首次兑换后，Target 用户列表显示 pending。
target_admin_login = _login(target, "test_admin", "123456")
target_admin_headers = _auth_headers(target_admin_login["access_token"])
pending = target.get(
    "/system/user/list",
    headers=target_admin_headers,
    params={"authorization_status": "pending", "page_no": 1, "page_size": 20},
)
assert pending.status_code == 200
shadow = next(item for item in pending.json()["data"]["items"] if item["auth_source"] == "federated")
assert shadow["authorization_status"] == "pending"

# Target 管理员用现有更新接口授予一个含有效菜单的本地角色。
target_roles = _response_data(
    target.get("/system/role/list?page_no=1&page_size=100", headers=target_admin_headers),
    "查询目标系统角色",
)
target_role_id = next(item["id"] for item in target_roles["items"] if item["code"] == "owner")
detail = _response_data(
    target.get(f"/system/user/detail/{shadow['id']}", headers=target_admin_headers),
    "查询目标系统影子账号",
)
detail["role_ids"] = [target_role_id]
detail["position_ids"] = []
updated = target.put(
    f"/system/user/update/{shadow['id']}",
    headers=target_admin_headers,
    json=detail,
)
assert updated.status_code == 200

# 同一影子账号变为 authorized，中控和 Target 的部门/角色数据没有同步写入。
authorized = target.get(
    "/system/user/list",
    headers=target_admin_headers,
    params={"authorization_status": "authorized", "page_no": 1, "page_size": 20},
)
assert authorized.status_code == 200
assert shadow["id"] in {item["id"] for item in authorized.json()["data"]["items"]}
after_grant = _shadow_snapshot(
    databases.target,
    issuer=control_url,
    central_user_uuid=central_user_uuid,
)
assert after_grant.role_count == 1
```

首次运行应因 API 尚未提供准确状态或测试 helper 尚未实现而失败；记录真实 RED，不允许跳过。

- [ ] **Step 2: 运行真实 SSO 闭环**

Run:

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
FASTAPIADMIN_TARGET_BACKEND=/Users/chou/code/FastapiAdmin/backend \
  uv run pytest tests/test_control_sso_integration.py -q
```

Expected: 临时 PostgreSQL 双库、Redis 和两个随机端口后端全部启动；测试通过；finally 清理数据库和进程。

- [ ] **Step 3: 运行后端全量门禁**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run ruff check app tests
uv run pytest tests -q
uv run alembic heads
```

Expected: Ruff 退出 0；pytest 全部通过；迁移仍只有 `20260810_01 (head)`。

- [ ] **Step 4: 运行前端全量门禁**

Run:

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm type-check
pnpm test
pnpm build
```

Expected: 类型检查、全部 Vitest 和生产构建退出 0。

- [ ] **Step 5: 最终范围审计并提交集成测试**

Run:

```bash
cd /Users/chou/code/FastapiAdmin
git diff --check
git status --short --branch
git diff --name-only HEAD~5..HEAD

cd /Users/chou/code/FastapiAdmin-Control
git diff --check
git status --short --branch
```

确认没有纳入 `.superpowers/`、`.understand-anything/`、构建产物、运行日志或其他代理文件，然后提交：

```bash
cd /Users/chou/code/FastapiAdmin-Control
git add backend/tests/test_control_sso_integration.py
git commit -m "test: 验证联邦用户本地授权闭环"
```

最终报告必须列出每个提交、后端/前端测试数量、真实双库结果、迁移 head、未跟踪文件保留情况，以及“未 push”。
