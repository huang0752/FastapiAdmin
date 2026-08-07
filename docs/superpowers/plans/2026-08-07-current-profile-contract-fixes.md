# Current Profile Contract Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复个人中心资料保存、修改密码路由、敏感字段响应和保存后用户状态刷新问题。

**Architecture:** 前端 API 层负责构造最小资料和密码请求，页面保存后重新获取完整当前用户信息。后端当前用户 schema 接受并规范化性别、保存描述，输出 schema 排除密码哈希，服务层只放开当前用户安全白名单并返回正确业务错误码。

**Tech Stack:** FastAPI、Pydantic、pytest、Vue 3、TypeScript、Vitest

---

### Task 1: 前端请求契约

**Files:**

- Modify: `frontend/web/src/api/module_system/user.ts`
- Create: `frontend/web/src/__tests__/current-profile-contract.spec.ts`

- [ ] 先测试修改密码请求使用 `/system/user/password/change` 且不发送 `confirm_password`。
- [ ] 先测试资料 payload 只包含 `name/gender/mobile/email/avatar/description`，并将数字性别转换为字符串。
- [ ] 运行目标 Vitest，确认路由和缺少 payload 构造函数导致失败。
- [ ] 实现最小请求契约并回跑测试。

### Task 2: 后端资料和安全响应契约

**Files:**

- Modify: `backend/app/api/v1/module_system/user/schema.py`
- Modify: `backend/app/api/v1/module_system/user/service.py`
- Create: `backend/tests/test_user_profile_contract.py`

- [ ] 先测试数字性别规范化、描述保留和 `UserOutSchema` 不序列化密码。
- [ ] 先测试超级管理员允许通过当前用户白名单修改资料，且部分更新不清空未提交字段。
- [ ] 先测试原密码错误返回 400。
- [ ] 运行目标 pytest，确认测试按上述现状失败。
- [ ] 实现 schema 和 service 最小修复并回跑测试。

### Task 3: 页面保存后刷新

**Files:**

- Modify: `frontend/web/src/views/fastlink/current/profile.vue`
- Test: `frontend/web/src/__tests__/current-profile-contract.spec.ts`

- [ ] 先测试页面使用资料白名单构造函数，并在保存后调用 `userStore.getUserInfo()`。
- [ ] 运行目标 Vitest，确认页面仍直接提交整个对象并覆盖 Store。
- [ ] 修改页面串联逻辑并回跑测试。

### Task 4: 验证

**Files:**

- Verify only

- [ ] 运行目标前后端测试。
- [ ] 运行 Ruff、Prettier、ESLint 和前端类型检查。
- [ ] 运行前端生产构建。
- [ ] 检查 `git diff --check` 与 `git status --short`，确认未触碰现有无关文件。
