# Food Logi 登录验证码关闭实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Food Logi 中控和三套产品环境关闭登录图片验证码与滑块，同时保留框架实现和恢复开关。

**Architecture:** 继续使用既有 `CAPTCHA_ENABLE` 服务端开关作为唯一权威源。关闭时验证码接口返回 `enable=false`，登录服务不校验验证码；前端收到禁用状态后隐藏验证码输入和滑块。框架默认值保持开启，Food Logi 专属运行环境显式关闭。

**Tech Stack:** FastAPI、Pydantic Settings、Vue 3、Pytest、Vitest、PostgreSQL、Redis。

---

### Task 1: 建立关闭验证码契约

**Files:**
- Modify: `backend/tests/test_food_logi_product_baseline.py`
- Modify: `/Users/chou/code/food‑logi/control/backend/tests/test_food_logi_control_baseline.py`

- [ ] 写失败测试，断言 Food Logi 环境显式设置 `CAPTCHA_ENABLE=false`，禁用时验证码接口返回 `enable=false`，无验证码登录可进入认证流程。
- [ ] 分别运行 products 与 control 聚焦测试，确认 RED 原因为禁用分支仍抛出“未开启验证码服务”或缺少环境配置。
- [ ] 提交 RED 测试。

### Task 2: 实现可恢复的服务端关闭

**Files:**
- Modify: `backend/app/api/v1/module_system/auth/service.py`
- Modify: `/Users/chou/code/food‑logi/control/backend/app/api/v1/module_system/auth/service.py`
- Modify: Food Logi 专属本地/部署环境配置

- [ ] 关闭时返回空 key、空图片且 `enable=false`，不生成 Redis 验证码。
- [ ] 为中控及三套产品的专属运行配置显式设置 `CAPTCHA_ENABLE=false`，不改框架默认值。
- [ ] 运行聚焦测试确认 GREEN，分别提交 products 和 control 实现。

### Task 3: 真实运行与浏览器验收

**Files:**
- Modify: `docs/verification/food-logi-subtraction-local-acceptance.md`

- [ ] 重启 8100–8103，使新配置生效。
- [ ] 验证四个验证码接口均返回 `enable=false`，无图片和 key。
- [ ] 使用本地测试账号登录三套产品，验证工作台、菜单减法和使用证明入口。
- [ ] 运行后端聚焦回归、前端测试和类型检查。
- [ ] 更新脱敏验收记录并提交；不保存密码、验证码或 Token。
