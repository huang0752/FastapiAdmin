# Food Logi Products Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立三个食品物流 SaaS 的单仓、独立装配、双品牌和独立部署工程基线。

**Architecture:** 使用一等产品注册表统一控制路由、模型、Seed、权限、前端和迁移范围；每个 Assembly 只启用一个产品。品牌通过 Host/Site 解析，同一产品的双品牌共享运行实例但保持 Site/Tenant 上下文隔离。

**Tech Stack:** FastAPI、SQLAlchemy、Alembic、pytest、Vue 3、TypeScript、Vitest、Vite、TOML、Nginx。

---

### Task 1: 产品注册与独立 Assembly

**Files:** `backend/app/core/product_modules.py`、`backend/app/assemblies/*.toml`、`backend/tests/test_food_logi_product_boundaries.py`

- [ ] 先写测试，断言每个 Assembly 只启用自己的模块、权限和 Seed，且不存在合集 Assembly。
- [ ] 运行测试，确认因注册表或装配文件缺失而失败。
- [ ] 实现最小产品注册表与三个 Assembly。
- [ ] 重跑测试并提交 `feat: 建立三产品独立装配`。

### Task 2: Seed 与品牌公共契约

**Files:** `backend/app/scripts/seeds/food-common/**`、三个产品 Seed 目录、`backend/tests/test_food_logi_seed_contract.py`

- [ ] 先写幂等清单、Site/Domain/Package 归属和权限前缀测试。
- [ ] 确认测试因 Seed 缺失失败。
- [ ] 实现公共 Seed 与三个原子产品 Seed，不写真实密钥或未确认品牌资料。
- [ ] 重跑测试并提交 `feat: 建立双品牌种子契约`。

### Task 3: 认证与 Site 四方一致

**Files:** `backend/app/api/v1/module_system/auth/**`、`backend/app/core/dependencies.py`、认证测试。

- [ ] 先写 JWT claim、Redis session、Host Site、Tenant Site 任一不一致均拒绝的测试。
- [ ] 确认 JWT 当前缺少 `site_id` 导致 RED。
- [ ] 最小修改签发与鉴权逻辑，保留刷新和切租户一致性。
- [ ] 重跑认证回归并提交 `feat: 强化站点会话四方校验`。

### Task 4: 模块与大屏契约骨架

**Files:** 四个后端模块目录、四个前端 API/View 目录、大屏 Schema/identity 测试。

- [ ] 先写三系统聚合接口、固定状态集合和 Site/Package variant 的失败测试。
- [ ] 实现不含完整业务模型的最小路由、Schema 与空数据状态。
- [ ] 建立两个品牌 variant 的页面入口，禁止随机数和前端品牌参数。
- [ ] 重跑测试并提交 `feat: 建立三系统大屏契约骨架`。

### Task 5: 独立迁移与构建部署

**Files:** 产品迁移目录、构建配置、部署示例、迁移/构建契约测试。

- [ ] 先写 `core + 当前产品` 迁移选择及构建排除测试。
- [ ] 实现三个独立迁移入口和三个独立构建产物配置。
- [ ] 增加端口、数据库、Redis、六 Host Nginx 与框架升级文档。
- [ ] 重跑边界测试并提交 `deploy: 增加三系统独立部署基线`。

### Task 6: 验证

- [ ] 运行所有新增后端测试及相关认证回归。
- [ ] 运行前端 Vitest、类型检查和三个生产构建。
- [ ] 检查 Git diff、Secret 扫描、Assembly/Seed/迁移/产物矩阵。
- [ ] 仅在所有证据通过后完成最后提交，不 push。
