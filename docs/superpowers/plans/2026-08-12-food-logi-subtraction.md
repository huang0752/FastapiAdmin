# 食品物流产品减法实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将三个食品物流产品收敛为保留租户工作台、使用证明和 AI 演示数据底座的最小可运行 SaaS，同时排除通用聊天、生成器、框架示例和虚假业务指标。

**Architecture:** 以三个 Assembly 作为后端插件、前端路由组和 Seed Pack 的权威边界；以共享的产品契约测试同时约束三套产品。租户工作台复用现有自助服务接口，但在食品产品配置下只显示真实的租户、套餐、配额和使用证明信息；产品业务仍由各自空态页面承载。AI 仅保留服务端模型配置、功能绑定和 `demo_data.blueprint` 注册底座，不重新挂载通用聊天 UI。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Pytest、Vue 3、TypeScript、Vitest、pnpm、PostgreSQL、Redis。

---

### Task 1: 锁定三个产品的减法契约

**Files:**
- Modify: `backend/tests/test_food_logi_product_baseline.py`
- Create: `frontend/web/src/__tests__/food-logi-subtraction.spec.ts`

- [ ] **Step 1: 写后端失败测试**

在参数化的三个 Assembly 契约中断言：`workspace` 已启用；`module_ai`、`module_generator`、`module_example` 均禁用；通用聊天和生成器路由组未启用；`tenant_workspace` 与 AI 演示蓝图底座功能标志保持开启。

- [ ] **Step 2: 运行后端测试确认 RED**

Run: `cd backend && uv run pytest tests/test_food_logi_product_baseline.py -q`

Expected: 因三个 Assembly 缺少 `workspace` 和明确的底座标志而失败。

- [ ] **Step 3: 写前端失败测试**

读取三个 Assembly 与静态路由源，断言产品允许 `workspace`，禁止 `ai-chat`、`pricing`、`article`、`tutorial`、`changelog`，且食品产品工作台不暴露通用分析页、通用大屏或聊天视图。

- [ ] **Step 4: 运行前端测试确认 RED**

Run: `cd frontend/web && pnpm vitest run src/__tests__/food-logi-subtraction.spec.ts`

Expected: 因 Assembly 未启用工作台或仍装配通用仪表盘子路由而失败。

- [ ] **Step 5: 提交测试 RED**

```bash
git add backend/tests/test_food_logi_product_baseline.py frontend/web/src/__tests__/food-logi-subtraction.spec.ts
git commit -m "test: 锁定食品物流产品减法边界"
```

### Task 2: 收敛 Assembly 与租户工作台路由

**Files:**
- Modify: `backend/app/assemblies/food-traceability.toml`
- Modify: `backend/app/assemblies/agricultural-delivery.toml`
- Modify: `backend/app/assemblies/cold-chain-vehicle.toml`
- Modify: `frontend/web/src/router/staticRoutes.ts`
- Modify: `frontend/web/src/config/assembly/default.ts`

- [ ] **Step 1: 最小修改三个 Assembly**

为三个产品启用 `workspace`，继续禁用 `module_ai`、`module_generator`、`module_example` 和通用聊天路由；显式启用 `tenant_workspace`、`usage_certificate`、`ai_model_foundation`、`demo_data_blueprint`，并保持 `ai_assistant=false`、`demo_content=false`。

- [ ] **Step 2: 将通用仪表盘子路由排除出食品产品**

为 `workplace`、`analysis`、通用 `screen` 增加只在非食品 Assembly 出现的过滤契约。三个产品仍通过各自 Seed 菜单进入 `module_trace/screen`、`module_agri/screen` 或 `module_logistic/screen` 空态页。

- [ ] **Step 3: 运行聚焦测试确认 GREEN**

Run: `cd backend && uv run pytest tests/test_food_logi_product_baseline.py tests/test_assembly.py -q`

Run: `cd frontend/web && pnpm vitest run src/__tests__/food-logi-subtraction.spec.ts src/__tests__/assembly-router.spec.ts`

Expected: 全部通过。

- [ ] **Step 4: 提交 Assembly 收敛**

```bash
git add backend/app/assemblies frontend/web/src/router/staticRoutes.ts frontend/web/src/config/assembly/default.ts
git commit -m "feat: 收敛食品物流租户工作台入口"
```

### Task 3: 保留工作台真实数据与使用证明

**Files:**
- Modify: `backend/tests/test_food_logi_product_baseline.py`
- Modify: `frontend/web/src/__tests__/food-logi-subtraction.spec.ts`
- Modify if required: `frontend/web/src/views/module_platform/self_service/index.vue`

- [ ] **Step 1: 增加工作台真实性失败测试**

断言食品产品工作台只消费 `/platform/tenant/workspace` 和使用证明 API，不出现追溯批次、订单、车辆、轨迹、温控等未实现统计字段；断言工作台和使用证明权限继续包含在食品产品套餐菜单中。

- [ ] **Step 2: 运行测试确认 RED 或记录既有 GREEN**

Run: `cd backend && uv run pytest tests/test_food_logi_product_baseline.py tests/test_usage_certificate_routes.py tests/test_usage_certificate_identity.py -q`

Run: `cd frontend/web && pnpm vitest run src/__tests__/food-logi-subtraction.spec.ts src/__tests__/usage-certificate.spec.ts`

Expected: 若现有实现已满足契约则记录 GREEN；若出现框架营销或虚假业务内容则测试失败并进入下一步。

- [ ] **Step 3: 仅删除测试指出的虚假或无关内容**

保留租户身份、套餐、真实用户/角色/部门配额、近期真实订单（存在才显示）和使用证明；不添加三类领域业务统计，不添加 AI 演示入口。

- [ ] **Step 4: 重跑聚焦测试**

Expected: 后端和前端聚焦测试全部通过。

- [ ] **Step 5: 提交真实性保护**

```bash
git add backend/tests/test_food_logi_product_baseline.py frontend/web/src/__tests__/food-logi-subtraction.spec.ts frontend/web/src/views/module_platform/self_service/index.vue
git commit -m "test: 保护工作台与使用证明真实边界"
```

### Task 4: 保护无聊天 UI 的 AI 演示底座

**Files:**
- Modify: `backend/tests/test_food_logi_product_baseline.py`
- Modify: `frontend/web/src/__tests__/food-logi-subtraction.spec.ts`

- [ ] **Step 1: 增加 AI 底座契约测试**

断言 `demo_data.blueprint` 注册项存在且可由 `AiFeatureBindingService` 绑定，同时三个产品 OpenAPI 不挂载 `/ai/chat` 路由，前端 Assembly 不包含 `ai-chat` 路由组。

- [ ] **Step 2: 运行测试**

Run: `cd backend && uv run pytest tests/test_ai_platform_foundation.py tests/test_food_logi_product_baseline.py -q`

Run: `cd frontend/web && pnpm vitest run src/__tests__/ai-feature-binding.spec.ts src/__tests__/food-logi-subtraction.spec.ts`

Expected: AI 服务端底座测试通过，食品产品通用聊天暴露为零。

- [ ] **Step 3: 提交底座保护契约**

```bash
git add backend/tests/test_food_logi_product_baseline.py frontend/web/src/__tests__/food-logi-subtraction.spec.ts
git commit -m "test: 保留演示数据 AI 底座"
```

### Task 5: 全量静态回归和三产品构建

**Files:**
- Modify: `docs/superpowers/specs/2026-08-12-food-logi-subtraction-design.md` only if verification reveals an inaccurate statement

- [ ] **Step 1: 后端格式、契约与安全回归**

Run: `cd backend && uv run ruff check app tests`

Run: `cd backend && uv run pytest tests/test_food_logi_product_baseline.py tests/test_assembly.py tests/test_site_auth_boundaries.py tests/test_security_foundation.py tests/test_usage_certificate_routes.py tests/test_usage_certificate_identity.py tests/test_ai_platform_foundation.py -q`

Expected: Ruff 零错误，所列测试零失败。

- [ ] **Step 2: 前端测试与类型检查**

Run: `cd frontend/web && pnpm vitest run`

Run: `cd frontend/web && pnpm type-check`

Expected: 全部测试通过，类型检查退出码 0。

- [ ] **Step 3: 三产品真实构建**

Run: `cd frontend/web && pnpm build:trace && pnpm build:agri && pnpm build:logistic`

Expected: 三个构建均退出码 0，产物分别写入既定独立目录。

- [ ] **Step 4: 检查工作树并提交验证修订**

Run: `git diff --check && git status --short`

Expected: 无空白错误；只存在本计划范围内变更。

### Task 6: 真实数据库与浏览器验收

**Files:**
- Create: `docs/verification/food-logi-subtraction-local-acceptance.md`

- [ ] **Step 1: 验证四个后端健康状态**

对 8100、8101、8102、8103 的 `/api/v1/common/health` 发起请求并记录 HTTP 200、Assembly 和数据库身份；不记录密码、Token 或密钥。

- [ ] **Step 2: 验证三个品牌产品登录与工作台**

分别使用对应 Host 登录 trace、agri、logistic，获取菜单并访问 `/workspace`；记录当前 Site、租户、产品入口和工作台请求成功，不截取或保存凭据。

- [ ] **Step 3: 验证减法结果**

确认菜单和可达路由中没有通用 AI 聊天、代码生成器、框架示例、定价、教程和文章；确认三个空态产品页没有虚假业务指标。

- [ ] **Step 4: 验证使用证明与 AI 底座边界**

通过租户权限验证使用证明预览/下载及公开 token 查验；通过自动化后端契约证明 AI 模型配置、功能绑定与 `demo_data.blueprint` 保留，但不声称产品演示数据生成闭环已实现。

- [ ] **Step 5: 写入脱敏验收记录并提交**

```bash
git add docs/verification/food-logi-subtraction-local-acceptance.md
git commit -m "docs: 记录食品物流减法本地验收"
```

