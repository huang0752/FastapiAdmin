# 中控租户自动开通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让中控平台管理员一次创建租户并选择任意产品及套餐，由后台可靠地在各产品独立数据库中创建租户和联邦 owner，同时提供状态、一次自动重试、人工重试与对账。

**Architecture:** 基础框架提供统一社会信用代码、联邦租户映射、无内部提交的联邦用户 primitive、按 handler 配置的任务重试间隔，以及默认关闭的目标开户适配器。Control 产品在合并框架后提供应用套餐目录、开通状态账本、一次性开户票据、Celery 编排任务和三步向导；目标产品使用现有 SSO 客户端凭据回中控兑换声明，整个本地开户在单事务内完成。

**Tech Stack:** FastAPI、SQLAlchemy 2、Pydantic v2、PostgreSQL、Alembic、Celery、Redis、httpx、pytest、Vue 3、TypeScript、Element Plus、Pinia、Vitest。

---

## 实施前约束

- 框架仓库：`/Users/chou/code/FastapiAdmin`
- Control 仓库：`/Users/chou/code/FastapiAdmin-Control`
- 设计基线：`docs/superpowers/specs/2026-08-11-control-tenant-auto-provisioning-design.md`
- 两个仓库都保留现有用户未跟踪文件，不批量 `git add`，不 push。
- 每个任务先写测试并运行得到与缺失功能一致的 RED，再写最小实现得到 GREEN。
- 自动开户能力默认关闭；基础租户创建、普通 SSO JIT、现有 Portal 和手工应用开通契约必须保持兼容。
- 套餐编码沿用现有字母数字约束；测试使用 `basic`、`pro`、`enterprise`，不引入带下划线的新编码。
- 不修改数据库 `alembic_version`，迁移必须通过 Alembic upgrade/downgrade/re-upgrade。

## 文件结构

### 框架仓库

- `backend/app/api/v1/module_platform/tenant/credit_code.py`：信用代码规范化和校验位算法。
- `backend/app/api/v1/module_platform/tenant/model.py`、`schema.py`、`service.py`：租户企业字段和稳定冲突错误。
- `backend/app/api/v1/module_platform/federated_tenant/model.py`：目标侧中央租户映射。
- `backend/app/api/v1/module_system/auth/federated_identity_service.py`：无内部提交的联邦用户和成员关系 primitive。
- `backend/app/api/v1/module_system/auth/control_tenant_provisioning_service.py`：目标侧开户兑换、校验、幂等和事务。
- `backend/app/plugin/module_task/runtime/registry.py`、`executor.py`：按 handler 配置固定重试间隔。
- `frontend/web/src/views/module_platform/tenant/credit-code.ts`：前端信用代码校验 helper。
- `backend/app/alembic/versions/20260811_01_add_tenant_provisioning_foundation.py`：信用代码和联邦租户映射。

### Control 仓库

- `backend/app/api/v1/module_control/application_package/`：目标应用套餐目录。
- `backend/app/api/v1/module_control/tenant_provision/`：状态账本、票据、接口、编排服务。
- `backend/app/plugin/module_control_provision/`：Control 专属业务任务 handler。
- `backend/app/alembic/versions/20260811_02_merge_framework_and_control_heads.py`：双 head merge。
- `backend/app/alembic/versions/20260811_03_add_control_tenant_provisioning.py`：Control 领域表和应用字段。
- `frontend/web/src/views/module_platform/tenant/ControlTenantProvisionWizard.vue`：三步创建向导。
- `frontend/web/src/views/module_control/application/ApplicationPackageDialog.vue`：应用套餐管理。
- `frontend/web/src/views/module_control/tenant-application/index.vue`：开通状态运维页。

---

### Task 1: 建立租户企业标识与联邦租户持久化基础

**Files:**
- Create: `backend/app/api/v1/module_platform/tenant/credit_code.py`
- Create: `backend/app/api/v1/module_platform/federated_tenant/__init__.py`
- Create: `backend/app/api/v1/module_platform/federated_tenant/model.py`
- Create: `backend/app/alembic/versions/20260811_01_add_tenant_provisioning_foundation.py`
- Modify: `backend/app/api/v1/module_platform/tenant/model.py`
- Modify: `backend/app/api/v1/module_platform/tenant/schema.py`
- Modify: `backend/app/api/v1/module_platform/tenant/service.py`
- Modify: `backend/tests/test_migration_baseline.py`
- Create: `backend/tests/test_tenant_enterprise_identity.py`

- [ ] **Step 1: 写信用代码和映射模型失败测试**

新增测试，至少固定以下行为：

```python
VALID_USCC = "91350100M000100Y43"


def test_uscc_normalizes_and_validates_checksum() -> None:
    assert validate_unified_social_credit_code(f"  {VALID_USCC.lower()}  ") == VALID_USCC
    with pytest.raises(ValueError, match="统一社会信用代码"):
        validate_unified_social_credit_code("91350100M000100Y44")


def test_federated_tenant_has_stable_unique_keys() -> None:
    keys = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in FederatedTenantModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("central_tenant_uuid", "issuer", "site_id") in keys
    assert ("issuer", "local_tenant_id", "site_id") in keys
    assert ("provision_request_uuid",) in keys
```

补充 API/数据库用例：空值兼容；同 Site 非空信用代码重复返回稳定 400；不同 Site 允许相同信用代码；平台更新可以修改；租户侧自助字段不能修改；现有租户创建仍返回一次性初始管理员。

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_tenant_enterprise_identity.py -q
```

预期：collection 因 `credit_code.py` 或 `FederatedTenantModel` 不存在而失败。

- [ ] **Step 3: 实现信用代码纯函数和模型**

`credit_code.py` 使用固定算法：

```python
USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def normalize_unified_social_credit_code(value: str | None) -> str | None:
    normalized = value.strip().upper() if value else ""
    return normalized or None


def validate_unified_social_credit_code(value: str | None) -> str | None:
    normalized = normalize_unified_social_credit_code(value)
    if normalized is None:
        return None
    if len(normalized) != 18 or any(char not in USCC_ALPHABET for char in normalized):
        raise ValueError("统一社会信用代码格式不正确")
    total = sum(USCC_ALPHABET.index(char) * weight for char, weight in zip(normalized[:17], USCC_WEIGHTS, strict=True))
    expected = USCC_ALPHABET[(31 - total % 31) % 31]
    if normalized[-1] != expected:
        raise ValueError("统一社会信用代码校验位不正确")
    return normalized
```

`TenantModel.__table_args__` 改为 tuple，加入 `UniqueConstraint("site_id", "unified_social_credit_code", name="uq_platform_tenant_site_uscc")`；字段为 nullable `String(18)`。Schema 的 create/update/out/query 全部加入该字段，query 使用规范化后的精确匹配。Service 在 ORM 唯一冲突前提供友好检查，数据库约束继续兜并发。

`FederatedTenantModel` 字段固定为：`id/site_id/issuer/central_tenant_uuid/central_tenant_code/local_tenant_id/provision_request_uuid/target_package_code/owner_central_user_uuid/created_time`，三个唯一约束与测试一致。

- [ ] **Step 4: 写并验证迁移**

迁移 `revision="20260811_01"`、`down_revision="20260810_01"`，upgrade 增加信用代码列、复合唯一约束和 `platform_federated_tenant`；downgrade 先删映射表，再删约束和字段。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run alembic heads
uv run pytest tests/test_migration_baseline.py -q
```

预期：唯一 head 为 `20260811_01 (head)`；随机 PostgreSQL upgrade/downgrade/re-upgrade 通过。

- [ ] **Step 5: 运行 GREEN 和回归**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_tenant_enterprise_identity.py tests/test_api_module_platform.py tests/test_security_foundation.py tests/test_migration_baseline.py -q
uv run ruff check app/api/v1/module_platform/tenant app/api/v1/module_platform/federated_tenant tests/test_tenant_enterprise_identity.py tests/test_migration_baseline.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/v1/module_platform/tenant/credit_code.py \
  backend/app/api/v1/module_platform/tenant/model.py \
  backend/app/api/v1/module_platform/tenant/schema.py \
  backend/app/api/v1/module_platform/tenant/service.py \
  backend/app/api/v1/module_platform/federated_tenant \
  backend/app/alembic/versions/20260811_01_add_tenant_provisioning_foundation.py \
  backend/tests/test_tenant_enterprise_identity.py \
  backend/tests/test_migration_baseline.py
git commit -m "feat: 增加租户企业标识与联邦映射"
```

---

### Task 2: 补齐租户企业信息前端契约

**Files:**
- Modify: `frontend/web/src/api/module_platform/tenant.ts`
- Create: `frontend/web/src/views/module_platform/tenant/credit-code.ts`
- Modify: `frontend/web/src/views/module_platform/tenant/index.vue`
- Create: `frontend/web/src/__tests__/tenant-enterprise-info.spec.ts`

- [ ] **Step 1: 写前端 RED**

```typescript
import { normalizeCreditCode, validateCreditCode } from "@/views/module_platform/tenant/credit-code";

it("normalizes an optional enterprise credit code", () => {
  expect(normalizeCreditCode(" 91350100m000100y43 ")).toBe("91350100M000100Y43");
  expect(normalizeCreditCode(" ")).toBeUndefined();
  expect(validateCreditCode("91350100M000100Y44")).toBe(false);
});
```

用源码契约同时断言 Tenant API 类型、搜索项、列表/详情和 create/update payload 包含 `unified_social_credit_code`。

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm test -- src/__tests__/tenant-enterprise-info.spec.ts
```

预期：helper 缺失或字段契约缺失而失败。

- [ ] **Step 3: 实现最小前端支持**

API 的 `TenantListItem/TenantForm/TenantCreateForm/TenantUpdateForm` 增加可选字段。租户页增加精确搜索、列表/详情字段和创建/更新输入；前端即时校验使用与后端相同字符集和权重，空值合法。基础版仍保持单页创建 Dialog，不加入产品选择。

- [ ] **Step 4: GREEN、类型和样式检查**

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm test -- src/__tests__/tenant-enterprise-info.spec.ts src/__tests__/tenant-initial-admin-dialog.spec.ts
pnpm type-check
pnpm exec eslint src/api/module_platform/tenant.ts src/views/module_platform/tenant/index.vue src/views/module_platform/tenant/credit-code.ts src/__tests__/tenant-enterprise-info.spec.ts
pnpm exec stylelint "src/views/module_platform/tenant/index.vue"
pnpm exec prettier --check src/api/module_platform/tenant.ts src/views/module_platform/tenant/index.vue src/views/module_platform/tenant/credit-code.ts src/__tests__/tenant-enterprise-info.spec.ts
```

- [ ] **Step 5: 提交**

```bash
git add frontend/web/src/api/module_platform/tenant.ts \
  frontend/web/src/views/module_platform/tenant/index.vue \
  frontend/web/src/views/module_platform/tenant/credit-code.ts \
  frontend/web/src/__tests__/tenant-enterprise-info.spec.ts
git commit -m "feat: 完善租户企业信息管理"
```

---

### Task 3: 支持业务任务按处理器设置固定重试间隔

**Files:**
- Modify: `backend/app/plugin/module_task/runtime/registry.py`
- Modify: `backend/app/plugin/module_task/runtime/executor.py`
- Modify: `backend/app/plugin/module_task/runtime/dispatcher.py`
- Modify: `backend/tests/test_business_task_runtime.py`

- [ ] **Step 1: 写 RED**

增加两个处理器定义：一个设置 `retry_backoff_seconds=10`，另一个不设置。断言首次 retryable 失败的 `ExecutionOutcome.retry_countdown` 分别为 10 和当前全局 `CELERY_RETRY_BACKOFF`。再增加事务内 prepare 测试：同一调用者事务内同时落业务行和 pending task，rollback 后两者都不存在；commit 后 `publish_existing()` 可以发布该 task。

```python
registry.register(
    handler_code="control.tenant_provision",
    handler=retryable_handler,
    module="control",
    max_retries=1,
    retry_backoff_seconds=10,
)
assert (await executor.execute(task_id)).retry_countdown == 10
```

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_business_task_runtime.py -q
```

预期：`register()` 不接受 `retry_backoff_seconds`。

- [ ] **Step 3: 最小实现**

`BusinessTaskDefinition` 增加 `retry_backoff_seconds: int`。`register()` 参数可空，空时取 `settings.CELERY_RETRY_BACKOFF`，并拒绝小于 1 的值。Executor 使用：

```python
countdown = definition.retry_backoff_seconds * (2 ** max(0, task.attempt - 1))
```

Control handler 的 `max_retries=1` 因而只有首次和一次 10 秒自动重试；其他任务保持原全局默认。

同时给 Dispatcher 增加：

```python
async def prepare(
    self,
    *,
    auth: AuthSchema,
    request: DispatchRequest,
) -> BusinessTaskModel:
    """在 auth.db 当前事务中写 pending outbox task；只 flush，不 commit、不发布。"""
```

`dispatch()` 复用同一套 definition/payload 校验，但继续保持“自有 session commit 后发布”的现有行为。`prepare()` 使用 savepoint 处理 idempotency_key 并设置 `external_task_id`，让业务 API 能把领域记录和任务 outbox 原子写入；提交后调用 `publish_existing()`，进程中断时现有 recovery 仍能扫描 pending task。

- [ ] **Step 4: GREEN 与回归**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_business_task_runtime.py tests/test_business_task_plugin.py -q
uv run ruff check app/plugin/module_task/runtime tests/test_business_task_runtime.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/plugin/module_task/runtime/registry.py \
  backend/app/plugin/module_task/runtime/executor.py \
  backend/app/plugin/module_task/runtime/dispatcher.py \
  backend/tests/test_business_task_runtime.py
git commit -m "feat: 支持任务处理器配置重试间隔"
```

---

### Task 4: 抽取无内部提交的联邦身份 primitive

**Files:**
- Create: `backend/app/api/v1/module_system/auth/federated_identity_service.py`
- Modify: `backend/app/api/v1/module_system/auth/control_sso_service.py`
- Modify: `backend/tests/test_control_sso_client.py`

- [ ] **Step 1: 写 RED**

新增测试直接调用共享 primitive，验证创建/复用用户、identity 和 membership 只 `flush` 不 `commit`；外层 rollback 后数据库无记录。再用已有 SSO API 验证普通联邦用户仍为 member、0 角色，已有 owner membership 不会被降级。

```python
async with async_db_session() as db:
    async with db.begin():
        user = await FederatedIdentityService.upsert_user_and_membership(
            db=db,
            site_id=1,
            issuer="https://control.example/api/v1",
            tenant_id=2,
            profile=FederatedUserProfile(
                central_user_uuid=subject,
                name="联邦负责人",
                mobile=None,
                email=None,
                avatar=None,
                status=0,
            ),
        )
        assert user.id is not None
        await db.rollback()
assert await identity_count(subject) == 0
```

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_control_sso_client.py -q
```

预期：共享 service/profile 不存在。

- [ ] **Step 3: 实现 primitive 并改造普通 SSO**

定义 `FederatedUserProfile`，并实现方法签名 `FederatedIdentityService.upsert_user_and_membership(*, db: AsyncSession, site_id: int, issuer: str, tenant_id: int, profile: FederatedUserProfile, membership_role: str = "member", is_default: int = 1) -> UserModel`。

创建路径继续使用 synthetic username、随机不可用密码、`auth_source="federated"`、`password_login_enabled=False` 和 savepoint 并发兜底；复用路径同步姓名/手机/邮箱/头像/状态并补 membership，但不覆盖已有 membership.role。方法只 flush。`ControlSSOClientService.exchange_and_login()` 由请求事务统一提交，普通登录不调用 `ensure_tenant_owner()`。

- [ ] **Step 4: GREEN 与兼容回归**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_control_sso_client.py tests/test_federated_user_authorization.py tests/test_api_module_system.py -q
uv run ruff check app/api/v1/module_system/auth tests/test_control_sso_client.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/v1/module_system/auth/federated_identity_service.py \
  backend/app/api/v1/module_system/auth/control_sso_service.py \
  backend/tests/test_control_sso_client.py
git commit -m "refactor: 复用联邦身份开户事务"
```

---

### Task 5: 实现目标系统默认关闭的租户开户适配器

**Files:**
- Modify: `backend/app/config/setting.py`
- Modify: `backend/env/.env.dev.example`
- Modify: `backend/env/.env.prod.example`
- Modify: `backend/app/api/v1/module_system/auth/control_sso_schema.py`
- Create: `backend/app/api/v1/module_system/auth/control_tenant_provisioning_service.py`
- Modify: `backend/app/api/v1/module_system/auth/controller.py`
- Modify: `backend/app/api/v1/module_platform/tenant/service.py`
- Create: `backend/tests/test_control_tenant_provisioning.py`

- [ ] **Step 1: 写 default-off、成功、幂等和回滚 RED**

测试至少覆盖：

```python
def test_tenant_provision_returns_404_without_network_when_disabled(test_client, monkeypatch):
    monkeypatch.setattr(settings, "CONTROL_TENANT_PROVISIONING_ENABLED", False, raising=False)
    response = test_client.post("/system/auth/control/tenant/provision", json={"code": "x" * 20})
    assert response.status_code == 404
    assert transport.calls == 0


def test_tenant_provision_creates_federated_owner_without_local_admin(test_client, monkeypatch):
    response = test_client.post("/system/auth/control/tenant/provision", json={"code": "p" * 20})
    assert response.json()["data"]["result"] == "created"
    assert shadow.auth_source == "federated"
    assert shadow.password_login_enabled is False
    assert owner_role_count == 1
    assert local_admin_named_from_tenant_code is None
```

另测：配置启用但 SSO 配置不完整 fail-fast；Site/套餐/租户编码/信用代码冲突整单回滚；相同 request、相同中央 UUID 和两个并发请求只产生一个租户；套餐、owner UUID、信用代码漂移返回 409；后续普通 SSO 复用 owner 且菜单非空。

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_control_tenant_provisioning.py -q
```

预期：配置、schema、service 或 route 缺失。

- [ ] **Step 3: 实现配置和 schema**

新增 `CONTROL_TENANT_PROVISIONING_ENABLED: bool = False`。当它为 true 时要求 `CONTROL_SSO_ENABLED` 同时为 true；issuer/client/secret/timeout 继续复用现有 SSO validator。Schema 固定为：

```python
class ControlTenantProvisionIn(BaseModel):
    code: str = Field(..., min_length=20, max_length=512)


class ControlTenantProvisionOwnerClaim(BaseModel):
    central_user_uuid: str
    username: str
    name: str
    mobile: str | None = None
    email: str | None = None
    avatar: str | None = None
    status: int


class ControlTenantProvisionClaims(BaseModel):
    provision_request_uuid: str
    central_tenant_uuid: str
    central_tenant_code: str
    tenant_name: str
    unified_social_credit_code: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    address: str | None = None
    site_code: str
    target_tenant_code: str
    target_package_code: str
    owner: ControlTenantProvisionOwnerClaim
    issuer: str
```

- [ ] **Step 4: 实现无密码管理员的目标事务**

从 `TenantService.create()` 提取只负责 Site/Package/唯一性和 `TenantModel` flush 的 `create_tenant_record()`；原 create 继续调用它后生成本地密码管理员，行为不变。

`ControlTenantProvisioningService`：

1. POST `{issuer}/control/provisioning/exchange`，使用现有 BasicAuth 和 timeout。
2. 校验 issuer、Host Site、site_code 和启用套餐编码。
3. 先按中央 UUID/request UUID 查询映射；一致返回 `already_exists`，漂移 409。
4. 不存在时在 savepoint 创建 Tenant、联邦 owner、owner role、套餐插件和映射。
5. IntegrityError 后回读映射；存在且一致返回 `already_exists`，否则返回明确编码/信用代码冲突。
6. 不记录 claims、code、secret 或随机密码；只由 `db_getter` 外层事务提交。

Controller 第一条判断 feature flag，false 直接 404，再调用 service。

- [ ] **Step 5: GREEN 与回归**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_control_tenant_provisioning.py tests/test_control_sso_client.py tests/test_package_transition_closure.py tests/test_api_module_platform.py -q
uv run ruff check app tests/test_control_tenant_provisioning.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/config/setting.py backend/env/.env.dev.example backend/env/.env.prod.example \
  backend/app/api/v1/module_system/auth/control_sso_schema.py \
  backend/app/api/v1/module_system/auth/control_tenant_provisioning_service.py \
  backend/app/api/v1/module_system/auth/controller.py \
  backend/app/api/v1/module_platform/tenant/service.py \
  backend/tests/test_control_tenant_provisioning.py
git commit -m "feat: 增加目标租户自动开户适配器"
```

---

### Task 6: 执行框架阶段发布门禁

**Files:**
- Verify only: all framework files changed in Tasks 1-5

- [ ] **Step 1: 后端全量**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run ruff check app tests
uv run pytest tests -q
uv run alembic heads
```

预期：Ruff 和 pytest 退出 0；唯一 head `20260811_01 (head)`。

- [ ] **Step 2: 前端全量**

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm type-check
pnpm test
pnpm build
```

- [ ] **Step 3: 范围审计**

```bash
cd /Users/chou/code/FastapiAdmin
git diff --check
git status --short --branch
git log --oneline 5f15f1dc..HEAD
```

确认用户未跟踪路径仍未纳入，且没有 dist、日志或临时数据库文件。Task 6 不创建“门禁通过”空提交。

---

### Task 7: 将框架基础能力合并到 Control 并收口双迁移 head

**Files:**
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/alembic/versions/20260811_02_merge_framework_and_control_heads.py`
- Verify: both repositories' ancestry and migration heads

- [ ] **Step 1: 只读预检两个仓库**

```bash
git -C /Users/chou/code/FastapiAdmin status --short --branch
git -C /Users/chou/code/FastapiAdmin-Control status --short --branch
git -C /Users/chou/code/FastapiAdmin-Control merge-base master framework/master
```

读取本地框架对象并进行 merge-tree 预演：

```bash
git -C /Users/chou/code/FastapiAdmin-Control fetch /Users/chou/code/FastapiAdmin master
git -C /Users/chou/code/FastapiAdmin-Control merge-tree --write-tree master FETCH_HEAD
```

预期：只出现真实可处理冲突；用户未跟踪文件保持原样。

- [ ] **Step 2: 合并框架提交**

```bash
cd /Users/chou/code/FastapiAdmin-Control
git fetch /Users/chou/code/FastapiAdmin master
git merge --no-ff FETCH_HEAD -m "merge: 同步租户自动开户基础能力"
```

不要 cherry-pick 复制公共实现；Control 后续继续从框架历史合并升级。

- [ ] **Step 3: 先验证真实双 head RED**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run alembic heads
```

预期同时出现 `20260810_02` 和 `20260811_01`；这是历史分叉的真实 RED，不得改旧 revision。

- [ ] **Step 4: 创建无 DDL merge revision**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run alembic merge --rev-id 20260811_02 \
  -m "merge framework and control tenant provisioning heads" \
  20260810_02 20260811_01
uv run alembic heads
```

预期只有 `20260811_02 (head)`。检查生成文件 `down_revision` 精确为 `("20260810_02", "20260811_01")`，upgrade/downgrade 均为 `pass`。

- [ ] **Step 5: 聚焦回归并提交 merge revision**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_migration_baseline.py tests/test_control_provider.py tests/test_control_sso_client.py -q
uv run ruff check app tests

cd /Users/chou/code/FastapiAdmin-Control
git add backend/app/alembic/versions/20260811_02_merge_framework_and_control_heads.py
git commit -m "chore: 合并中控与框架迁移分支"
```

---

### Task 8: 建立 Control 应用套餐与开通账本模型

**Files:**
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/model.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/schema.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/service.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/application_package/__init__.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/application_package/model.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/application_package/schema.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/__init__.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/model.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/schema.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/alembic/versions/20260811_03_add_control_tenant_provisioning.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_tenant_provision_models.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_migration_baseline.py`

- [ ] **Step 1: 写模型和应用配置 RED**

测试固定：

```python
def test_application_exposes_provisioning_configuration() -> None:
    application = ControlApplicationModel(
        site_id=1,
        code="wms",
        name="仓储系统",
        base_url="https://wms.example.com",
        callback_url="https://wms.example.com/web#/auth/control/callback",
        client_id="wms-client",
        client_secret_hash="hashed",
    )
    assert application.provisioning_enabled is False
    assert application.provisioning_timeout_seconds == 10


def test_tenant_provision_has_one_row_per_tenant_application() -> None:
    assert unique_key(ControlTenantProvisionModel, "tenant_id", "application_id")
    assert unique_key(ControlTenantProvisionModel, "provision_request_uuid")
```

另断言 package 的两个唯一键、ticket.code_hash 唯一、provision.status check constraint、owner_user_id 和 application_package_id 外键，以及 Application create/update/out schema 对 URL、开关和 1-60 秒 timeout 的校验。

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_tenant_provision_models.py -q
```

预期新模型/字段不存在。

- [ ] **Step 3: 实现模型和 schema**

`ControlApplicationModel` 新增 nullable provisioning URL、default false enable、default 10 timeout。`ControlApplicationBaseSchema` 接受这些字段；启用时 URL 必填，生产环境仍要求 HTTPS。

`ControlApplicationPackageModel` 字段为 `site_id/application_id/code/name/description/target_package_code/is_default/status/sort`，code 与 target code 均规范化为小写字母数字。

`ControlTenantProvisionModel` 字段和状态严格按设计，`max_attempts=2`；`ControlTenantProvisionTicketModel` 只存 hash 和生命周期。Schema 提供 create/page/update/result/exchange claims，输出不含 code hash。

- [ ] **Step 4: 编写 Control 领域迁移并验证 RED→GREEN**

`20260811_03` 的 `down_revision="20260811_02"`。upgrade：扩展 application、创建 package/provision/ticket 及所有索引约束；downgrade 反序删除。迁移测试验证：

```text
upgrade 20260811_03
→ 新字段/三表存在
downgrade 20260811_02
→ 三表和应用字段消失，框架信用代码与联邦租户表仍存在
downgrade 20260810_02 和 20260811_01 的父链
→ 各自表按既有契约消失
re-upgrade head
→ 唯一 head 20260811_03
```

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_tenant_provision_models.py tests/test_migration_baseline.py -q
uv run alembic heads
uv run ruff check app/api/v1/module_control tests/test_control_tenant_provision_models.py tests/test_migration_baseline.py
```

- [ ] **Step 5: 提交**

```bash
cd /Users/chou/code/FastapiAdmin-Control
git add backend/app/api/v1/module_control/model.py \
  backend/app/api/v1/module_control/schema.py \
  backend/app/api/v1/module_control/service.py \
  backend/app/api/v1/module_control/application_package \
  backend/app/api/v1/module_control/tenant_provision \
  backend/app/alembic/versions/20260811_03_add_control_tenant_provisioning.py \
  backend/tests/test_control_tenant_provision_models.py \
  backend/tests/test_migration_baseline.py
git commit -m "feat: 建立中控租户开通账本"
```

---

### Task 9: 实现应用套餐管理和一次性开户声明兑换

**Files:**
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/application_package/service.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/application_package/controller.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/ticket_service.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/exchange_controller.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/__init__.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/service.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_application_package.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_provision_ticket.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_provider.py`

- [ ] **Step 1: 写套餐 CRUD 和票据 RED**

套餐测试覆盖同 Site、应用启用、默认套餐唯一、重复 code/target code、跨 Site 403、停用套餐不能用于新开通。票据测试覆盖：

```python
def test_provision_ticket_is_single_use(test_client, target_basic_auth):
    first = test_client.post("/control/provisioning/exchange", auth=target_basic_auth, json={"code": code})
    replay = test_client.post("/control/provisioning/exchange", auth=target_basic_auth, json={"code": code})
    assert first.status_code == 200
    assert replay.status_code == 401
```

另测过期、错误 client、应用/租户/owner/套餐停用、两个并发兑换只有一个成功，以及响应包含租户企业/联系人字段而不包含 secret/hash。

- [ ] **Step 2: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_application_package.py tests/test_control_provision_ticket.py -q
```

- [ ] **Step 3: 实现套餐 API**

路由前缀：

```text
GET    /control/application-packages
POST   /control/application-packages
PUT    /control/application-packages/{id}
DELETE /control/application-packages/{id}
```

每个 service 查询都同时限定 `auth.site_id` 和 application.site_id。设置 `is_default=true` 时，同一应用其他未删除套餐原子改为 false。已被成功 provision 引用的套餐只允许停用，不物理删除。

- [ ] **Step 4: 实现票据签发与兑换**

复用现有应用 BasicAuth 校验，但抽为一个内部 helper，让 SSO 和 provisioning exchange 使用同一 client_id/secret_hash 校验。签发使用 `secrets.token_urlsafe(32)`，只保存 SHA-256。兑换用条件 UPDATE：

```python
update(ControlTenantProvisionTicketModel)
.where(
    code_hash == digest,
    status == "issued",
    expires_at > now,
)
.values(status="redeemed", redeemed_at=now)
.returning(provision_id)
```

再加载同 Site/application/client 的 provision、tenant、owner、package 和 site，组装已确认的 claims。日志只记录 provision_id/application_id/error_code。

- [ ] **Step 5: GREEN 与回归**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_application_package.py tests/test_control_provision_ticket.py tests/test_control_provider.py -q
uv run ruff check app/api/v1/module_control tests/test_control_application_package.py tests/test_control_provision_ticket.py
```

- [ ] **Step 6: 提交**

```bash
cd /Users/chou/code/FastapiAdmin-Control
git add backend/app/api/v1/module_control/application_package \
  backend/app/api/v1/module_control/tenant_provision/ticket_service.py \
  backend/app/api/v1/module_control/tenant_provision/exchange_controller.py \
  backend/app/api/v1/module_control/__init__.py \
  backend/app/api/v1/module_control/service.py \
  backend/tests/test_control_application_package.py \
  backend/tests/test_control_provision_ticket.py \
  backend/tests/test_control_provider.py
git commit -m "feat: 增加应用套餐和开户票据兑换"
```

---

### Task 10: 实现中控租户创建编排、一次自动重试和对账

**Files:**
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/service.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/controller.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/tenant_provision/task_schema.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/plugin/module_control_provision/__init__.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/plugin/module_control_provision/plugin.toml`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/app/plugin/module_control_provision/handlers.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/assemblies/control.toml`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/api/v1/module_control/__init__.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_tenant_provision.py`
- Create: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_provision_worker.py`

- [ ] **Step 1: 写一站式创建事务 RED**

测试 `POST /control/tenants/provision`：

```python
response = test_client.post(
    "/control/tenants/provision",
    headers=platform_headers,
    json={
        "tenant": {"name": name, "code": code, "site_id": 1, "package_id": control_package_id, "unified_social_credit_code": VALID_USCC},
        "applications": [
            {"application_id": wms_id, "application_package_id": wms_pro_id},
            {"application_id": mes_id, "application_package_id": mes_basic_id},
        ],
    },
)
assert response.status_code == 200
assert response.json()["data"]["tenant"]["initial_admin"]["username"] == f"{code}_admin"
assert {item["status"] for item in response.json()["data"]["provisions"]} == {"pending"}
```

断言租户、owner、两条 provision 和两条 pending BusinessTask outbox 在同一事务；任一应用/套餐无效时全部为 0；Celery 未启用时返回 503 且不创建租户。

- [ ] **Step 2: 写 Worker 重试和成功记账 RED**

用 MockTransport 固定：WMS 首次成功；MES 首次 timeout、第二次成功。断言 handler definition：

```python
assert definition.max_retries == 1
assert definition.retry_backoff_seconds == 10
```

执行器结果依次为 retrying/countdown 10、success；provision.attempt_count 为 2，最终 succeeded。再测两次临时失败后 failed；业务 409 首次即 failed；人工 retry 的新 DispatchRequest 使用 `max_retries=0`；成功事务幂等创建 opening 和 owner grant。

- [ ] **Step 3: 运行 RED**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_tenant_provision.py tests/test_control_provision_worker.py -q
```

- [ ] **Step 4: 实现事务内创建和 outbox**

`ControlTenantProvisionCreateService.create()` 调现有 `TenantService.create()`，从返回租户查 `{code}_admin`，校验所有选项后创建 provision，并为每条调用 `BusinessTaskDispatcher.prepare()`：

```python
task = await dispatcher.prepare(
    auth=self.auth,
    request=DispatchRequest(
        handler_code="control.tenant_provision",
        module="control",
        biz_type="tenant_provision",
        biz_id=str(provision.id),
        payload={"provision_id": provision.id, "mode": "initial"},
        max_retries=1,
        idempotency_key=f"tenant-provision:{provision.provision_request_uuid}:initial",
    ),
)
```

Controller 用 `BackgroundTasks` 在响应提交后逐条 `publish_existing(task_id)`；即使进程在发布前退出，现有 task recovery 会扫描 pending outbox。

- [ ] **Step 5: 实现 handler 和错误记账**

`plugin.toml`：

```toml
name = "control_provision"
title = "中控租户开通"
optional = true

[runtime]
business_task_modules = ["app.plugin.module_control_provision.handlers"]
```

handler：

```python
@register_business_task(
    handler_code="control.tenant_provision",
    module="control",
    max_retries=1,
    retry_backoff_seconds=10,
    payload_schema=ControlTenantProvisionTaskPayload,
)
async def provision_tenant(context: BusinessTaskContext, payload: ControlTenantProvisionTaskPayload) -> dict:
    return await ControlTenantProvisionWorkerService(context).execute(payload)
```

每次尝试先用 `context.session_factory` 独立事务原子增加 attempt_count 并标记 processing。临时错误先独立写 last_error/next_retry_at，再抛 `RetryableBusinessTaskError`；业务错误独立写 failed 后抛普通领域异常；成功在 handler 主事务写 succeeded、opening 和 grant。不能在即将 rollback 的同一事务内写失败账本。BusinessTask 的现有 lease recovery 负责恢复进程中断的任务；恢复执行进入 handler 时，必须在同一原子认领步骤覆盖陈旧 processing 状态，领域表不另建第二套租约。

HTTP 客户端只向 application.provisioning_url POST code；临时错误仅限 connect/timeout/429/502/503/504。目标 4xx 保存稳定业务错误码，不自动重试。

- [ ] **Step 6: 实现状态、修改、人工重试和对账 API**

```text
GET  /control/tenant-provisions
PUT  /control/tenant-provisions/{id}
POST /control/tenant-provisions/{id}/retry
POST /control/tenant-provisions/{id}/reconcile
```

平台管理员和 Site 边界由权限依赖与 service 双重校验。PUT 只允许 failed 修改 package/desired code，并重置安全错误字段；retry/reconcile 创建 `max_retries=0` 的新 outbox task，idempotency key 固定为 `tenant-provision:{provision_request_uuid}:{action}:{uuid4().hex}`，保证每次人工操作唯一且只执行一次。succeeded 更新/retry 返回 409。

- [ ] **Step 7: GREEN、任务运行时和 API 回归**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_tenant_provision.py tests/test_control_provision_worker.py tests/test_business_task_runtime.py tests/test_control_provider.py -q
uv run ruff check app tests/test_control_tenant_provision.py tests/test_control_provision_worker.py
```

- [ ] **Step 8: 提交**

```bash
cd /Users/chou/code/FastapiAdmin-Control
git add backend/app/api/v1/module_control/tenant_provision \
  backend/app/api/v1/module_control/__init__.py \
  backend/app/plugin/module_control_provision \
  backend/app/assemblies/control.toml \
  backend/tests/test_control_tenant_provision.py \
  backend/tests/test_control_provision_worker.py
git commit -m "feat: 实现中控租户自动开通编排"
```

---

### Task 11: 增加 Control 权限 Seed 和三步创建向导

**Files:**
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/scripts/data/platform_menu.json`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/app/scripts/seeds/control/platform_package_menu.json`
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_seed.py`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/config/assembly/default.ts`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/api/module_control/index.ts`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/views/module_platform/tenant/index.vue`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/views/module_platform/tenant/TenantInitialAdminDialog.vue`
- Create: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/views/module_platform/tenant/ControlTenantProvisionWizard.vue`
- Create: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/views/module_platform/tenant/TenantProvisionResult.vue`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/views/module_control/application/index.vue`
- Create: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/views/module_control/application/ApplicationPackageDialog.vue`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/views/module_control/tenant-application/index.vue`
- Create: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/__tests__/control-tenant-provisioning.spec.ts`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/__tests__/control-provider-portal.spec.ts`
- Modify: `/Users/chou/code/FastapiAdmin-Control/frontend/web/src/__tests__/tenant-initial-admin-dialog.spec.ts`

- [ ] **Step 1: 写 Seed 权限 RED**

断言 Control 平台菜单包含 `module_control:application_package:*` 和 `module_control:tenant_provision:*` 九个权限；Control tenant 套餐映射明确不包含这些平台权限，但保留 user_grant/portal。

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_seed.py -q
```

- [ ] **Step 2: 实现 Seed 和 Assembly flag**

应用套餐按钮归属应用管理；现有租户应用页面和原 `tenant_application` 权限继续保留，作为高级兼容/查询入口，不改变旧 API 权限码。新增开通账本菜单和 `tenant_provision:*` 按钮权限，正常创建路径不再要求手填目标租户编码。Control assembly 在 TOML 中新增 `tenant_auto_provisioning=true`，后端按现有 camelCase 转换向前端下发 `tenantAutoProvisioning`；`default.ts` 对缺失字段和基础 profile 明确 false，调用 `isFeatureEnabled("tenantAutoProvisioning", false)`，不能使用默认 true fallback。

- [ ] **Step 3: 写前端 RED**

测试固定：

```typescript
expect(resolveTenantCreateMode({ tenantAutoProvisioning: false })).toBe("basic");
expect(resolveTenantCreateMode({ tenantAutoProvisioning: true })).toBe("control-wizard");
expect(buildProvisionCreatePayload(tenant, selections).applications).toEqual([
  { application_id: 10, application_package_id: 101 },
]);
```

源码/组件测试同时断言：基础模式没有新增 API 请求；向导三步校验；默认目标编码来自 tenant.code；成功只展示一次密码和 provision 状态；failed 才显示修改/重试/对账；succeeded 只读；应用表单包含 provisioning config；套餐对话框使用新 CRUD。

```bash
cd /Users/chou/code/FastapiAdmin-Control/frontend/web
pnpm test -- src/__tests__/control-tenant-provisioning.spec.ts
```

- [ ] **Step 4: 实现 typed API 和组件**

`module_control/index.ts` 新增：

```typescript
type TenantProvisionStatus = "pending" | "processing" | "succeeded" | "failed";

interface TenantProvisionSelection {
  application_id: number;
  application_package_id: number;
}

interface CreateTenantWithProvisions {
  tenant: TenantCreateForm;
  applications: TenantProvisionSelection[];
}
```

并实现 application package CRUD、createTenantWithProvisions、list/update/retry/reconcile provisions。向导仅在 build/runtime capability 都存在时懒加载；普通 tenant create/update/detail 路径保持原实现。密码只保存在组件 ref，close/unmount 清空，不进入 store/storage。

- [ ] **Step 5: 实现运维页和应用套餐管理**

应用管理增加 provisioning URL/enable/timeout 和“套餐管理”。租户应用页的数据源切换为 provision page；保留已成功 opening 的撤销操作，但不能把 failed/pending 当成已开通应用。错误列只展示后端安全摘要。

- [ ] **Step 6: GREEN、类型、格式和 Seed 回归**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run pytest tests/test_control_seed.py tests/test_control_provider.py -q
uv run ruff check app tests/test_control_seed.py

cd /Users/chou/code/FastapiAdmin-Control/frontend/web
pnpm test -- src/__tests__/control-tenant-provisioning.spec.ts src/__tests__/control-provider-portal.spec.ts src/__tests__/tenant-initial-admin-dialog.spec.ts src/__tests__/assembly-filter.spec.ts
pnpm type-check
pnpm exec eslint src/api/module_control src/views/module_platform/tenant src/views/module_control src/__tests__/control-tenant-provisioning.spec.ts
pnpm exec stylelint "src/views/module_platform/tenant/*.vue" "src/views/module_control/**/*.vue"
pnpm exec prettier --check src/api/module_control src/views/module_platform/tenant src/views/module_control src/__tests__/control-tenant-provisioning.spec.ts
```

- [ ] **Step 7: 提交**

```bash
cd /Users/chou/code/FastapiAdmin-Control
git add backend/app/scripts/data/platform_menu.json \
  backend/app/scripts/seeds/control/platform_package_menu.json \
  backend/tests/test_control_seed.py \
  frontend/web/src/config/assembly/default.ts \
  frontend/web/src/api/module_control/index.ts \
  frontend/web/src/views/module_platform/tenant \
  frontend/web/src/views/module_control/application \
  frontend/web/src/views/module_control/tenant-application/index.vue \
  frontend/web/src/__tests__/control-tenant-provisioning.spec.ts \
  frontend/web/src/__tests__/control-provider-portal.spec.ts \
  frontend/web/src/__tests__/tenant-initial-admin-dialog.spec.ts
git commit -m "feat: 增加中控租户产品开通向导"
```

---

### Task 12: 完成真实多产品闭环和两仓发布门禁

**Files:**
- Modify: `/Users/chou/code/FastapiAdmin-Control/backend/tests/test_control_sso_integration.py`
- Verify only: all files changed by Tasks 1-11

- [ ] **Step 1: 扩展真实集成并取得 RED**

将现有夹具扩为 Control + WMS Target + MES Target 三个随机 PostgreSQL 数据库、一个隔离 Redis、三个随机 Uvicorn 端口和一个 Control Celery worker。目标 revision 改为 `20260811_01`，Control revision 改为 `20260811_03`；目标环境开启 `CONTROL_TENANT_PROVISIONING_ENABLED=true`，Control 开启 Celery 和 `APP_ASSEMBLY=control`。

测试顺序：

1. 注册 WMS/MES 两个应用和各自 `pro/basic` 套餐。
2. `POST /control/tenants/provision` 创建含有效信用代码的中控租户。
3. WMS 正常成功；MES 测试服务第一次返回 503，10 秒后的唯一自动重试成功。
4. Control provision 都为 succeeded、attempt 分别为 1/2，owner 有两个 grants。
5. 两个目标 DB 各有一个 tenant、federated tenant mapping、同一中央 owner 的本地影子账号和 owner role；无 `{code}_admin` 本地密码账号。
6. owner 从中控启动两个产品，`current/info.menus` 均非空。
7. 普通成员获应用 grant 后进入，仍为 pending/0 role。
8. reconcile/retry 不产生重复目标租户。

首次对旧 Control/Target 运行必须因新 API/表/字段缺失而 RED，不接受跳过。

- [ ] **Step 2: 运行真实 GREEN**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
FASTAPIADMIN_TARGET_BACKEND=/Users/chou/code/FastapiAdmin/backend \
  uv run pytest tests/test_control_sso_integration.py -q
```

finally 必须停止 Worker、三个 HTTP、Redis，terminate DB connections 并删除本次精确随机数据库；不得停止用户现有手工 smoke 服务。

- [ ] **Step 3: 提交真实集成测试**

```bash
cd /Users/chou/code/FastapiAdmin-Control
git add backend/tests/test_control_sso_integration.py
git commit -m "test: 验证中控租户多产品自动开通"
```

- [ ] **Step 4: 框架最终门禁**

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run ruff check app tests
uv run pytest tests -q
uv run alembic heads

cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm type-check
pnpm test
pnpm build
```

预期框架唯一 head `20260811_01`。

- [ ] **Step 5: Control 最终门禁**

```bash
cd /Users/chou/code/FastapiAdmin-Control/backend
uv run ruff check app tests
uv run pytest tests -q
uv run alembic heads

cd /Users/chou/code/FastapiAdmin-Control/frontend/web
pnpm type-check
pnpm test
pnpm build
```

预期 Control 唯一 head `20260811_03`。

- [ ] **Step 6: 浏览器人工验收**

启动独立 Control、WMS、MES 前后端和 Worker，人工验证：三步向导、一次性密码、逐产品状态、失败摘要、10 秒唯一自动重试、人工重试、对账、owner 首次直达、普通成员待授权。记录真实 URL、数据库名、应用 client、关键响应和截图路径；不得把凭据写入仓库。

- [ ] **Step 7: 最终范围审计**

```bash
git -C /Users/chou/code/FastapiAdmin diff --check
git -C /Users/chou/code/FastapiAdmin status --short --branch
git -C /Users/chou/code/FastapiAdmin-Control diff --check
git -C /Users/chou/code/FastapiAdmin-Control status --short --branch
```

最终报告列出两仓所有提交、测试数量、迁移 heads、三个真实数据库结果、10 秒重试证据、浏览器验收限制、保留的未跟踪文件和“未 push”。
