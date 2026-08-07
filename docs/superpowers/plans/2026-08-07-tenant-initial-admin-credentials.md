# Tenant Initial Admin Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建租户成功后立即、安全地展示一次性初始管理员账号和临时密码，避免创建响应被前端丢弃。

**Architecture:** 后端现有 `initial_admin` 一次性响应契约保持不变。前端 API 层增加精确类型和纯提取函数，租户页面只在内存中暂存凭据并交给独立弹窗展示，用户确认已保存后清空状态。

**Tech Stack:** Vue 3、TypeScript、Element Plus、Vitest、Vue Test Utils

---

### Task 1: 创建响应契约

**Files:**

- Modify: `frontend/web/src/api/module_platform/tenant.ts`
- Test: `frontend/web/src/__tests__/tenant-status.spec.ts`

- [ ] 在测试中构造 Axios 风格 `{ data: { data: { initial_admin } } }` 响应，断言提取函数返回账号密码。
- [ ] 运行 `pnpm vitest run src/__tests__/tenant-status.spec.ts`，确认因缺少导出而失败。
- [ ] 增加 `TenantInitialAdmin`、`TenantCreateResult` 和 `extractTenantInitialAdmin()`，并把 `createTenant()` 泛型改为 `ApiResponse<TenantCreateResult>`。
- [ ] 再次运行目标测试，确认通过。

### Task 2: 一次性凭据弹窗

**Files:**

- Create: `frontend/web/src/views/module_platform/tenant/TenantInitialAdminDialog.vue`
- Create: `frontend/web/src/__tests__/tenant-initial-admin-dialog.spec.ts`

- [ ] 先写组件测试，覆盖租户名称、账号、密码和一次性提示文本。
- [ ] 覆盖复制账号、复制密码、复制完整交付文本。
- [ ] 覆盖未确认时不能关闭，确认后发出 `closed` 事件。
- [ ] 运行目标测试，确认组件缺失导致失败。
- [ ] 实现独立 `FaDialog`，禁止遮罩、ESC 和右上角关闭，使用确认复选框控制关闭按钮。
- [ ] 再次运行目标测试，确认通过。

### Task 3: 串联租户创建流程

**Files:**

- Modify: `frontend/web/src/views/module_platform/tenant/index.vue`

- [ ] 创建成功后保存 `response.data.data.initial_admin` 到内存状态。
- [ ] 刷新列表并关闭创建表单后打开凭据弹窗。
- [ ] 凭据弹窗关闭时将账号、密码和租户名全部清空，不写入浏览器持久化或日志。
- [ ] 执行目标测试和 `pnpm type-check`。

### Task 4: 完整验证

**Files:**

- Verify only

- [ ] 运行 `pnpm vitest run src/__tests__/tenant-status.spec.ts src/__tests__/tenant-initial-admin-dialog.spec.ts`。
- [ ] 运行相关文件的 Prettier、ESLint 和 Stylelint 检查。
- [ ] 运行 `pnpm type-check`。
- [ ] 运行 `pnpm build`。
- [ ] 检查 `git diff --check` 和 `git status --short`，确认没有触碰 `.understand-anything/`。
