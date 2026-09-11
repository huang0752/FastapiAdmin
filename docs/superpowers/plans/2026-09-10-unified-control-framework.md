# FastapiAdmin 统一中控与产品授权 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Implementation authorized by the user in a new worktree; merge, push and deployment remain outside this execution.

**Goal:** 在 FastapiAdmin 主线中提供可选中控与可靠产品授权，吸收 Food 的修复并保持独立 SaaS 兼容。

**Architecture:** 模块化单仓库、三种装配。中控和独立产品保留自己的数据库，以数据库任务 + Celery/Redis + HTTP 同步访问资格；产品角色和数据权限由本地维护。

**Tech Stack:** Python、FastAPI、SQLAlchemy、Alembic、PostgreSQL、Redis、Celery、Vue 3、TypeScript、pytest、Vitest。此次不引入 RabbitMQ。

---

## 文档范围与使用方式

本文件是跨子系统的实施总计划和阶段门禁；设计依据为同目录相邻 specs 下的 `2026-09-10-unified-control-framework-design.md`。复杂会话/迁移实现不在评审前伪造完整补丁。执行每个阶段前按下述精确源码、约束与用例形成该阶段的补丁计划，再修改实现。阶段不得跳过失败门禁。

实施目标根为 `/Users/chou/code/FastapiAdmin-unified-control/`；下方历史命令中的 `/Users/chou/code/FastapiAdmin/` 为规划基线，执行时使用新工作树；来源路径为设计中的四个固定 SHA。标记“新增”的路径是规划目标，不代表已经存在。测试函数和测试文件中未存在的名称是拟新增验收项。

依赖：P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P7 -> P8。P9 是独立的下游接入/发布计划，代码完成不自动授权 P9 生产操作。

## 执行记录

新工作树已建立；实现覆盖 P0–P8 的框架功能，定向验证已通过。实际测试结果、例外和未验证项统一记入 `docs/framework/control-validation.md`。原始颗粒清单保留为设计要求，下表为本次执行状态：

| 阶段 | 本次结果 |
|---|---|
| P0 来源与基线 | 已固定 SHA，隔离工作树，记录来源 |
| P1 能力与装配 | 已实现并验证，名称无关、前后端接口同步裁剪 |
| P2 数据迁移 | 已实现，新库/原框架/原中控真实 PG 路径通过 |
| P3 角色与成员 | 已实现 manual/declared、人工角色保留、共享身份边界 |
| P4 资格与回执 | 已实现，重复/乱序/版本/隔离与拒绝路径通过 |
| P5 会话 | 已实现，真实 Redis Lua 与历史会话补偿通过 |
| P6 中控与 Worker | 已实现，生命周期撤权、离线重试、失败收口通过 |
| P7 前端 | 已实现，定向 Vitest、类型及 ESLint 通过 |
| P8 本地整链验收 | 三库双产品真实 HTTP/Worker 通过；固定 Food schema 合同通过，新旧服务真实混跑未执行 |
| P9 下游发布 | 未执行，独立范围 |


## P0：固定基线和来源清单

**交付物**：新增 `docs/framework/control-integration-source-map.md`，逐项记录来源 SHA、文件、修复用途、目标文件、依赖、测试和迁移归属。

- [ ] 执行以下只读检查，任何工作区出现用户未提交修改时记录并隔离，不覆盖：

```bash
git -C /Users/chou/code/FastapiAdmin status --short
git -C /Users/chou/code/FastapiAdmin-Control status --short
git -C '/Users/chou/code/food‑logi/control' status --short
git -C '/Users/chou/code/food‑logi/products' status --short
git -C /Users/chou/code/FastapiAdmin rev-parse HEAD
git -C /Users/chou/code/FastapiAdmin-Control rev-parse HEAD
git -C '/Users/chou/code/food‑logi/control' rev-parse HEAD
git -C '/Users/chou/code/food‑logi/products' rev-parse HEAD
```

- [ ] 按设计中的 SHA 比较结果；有差异时先补充来源清单，不能继续使用过期行号或提交数量。
- [ ] 将修复分为：中控基础、授权账本、角色/成员治理、会话生命周期、任务运行时、前端授权恢复、产品专属业务、部署配置。
- [ ] 对共享文件逐块审阅，特别是 `dependencies.py`、`auth/service.py`、`user/crud.py`、`role/service.py`、`module_task/runtime/`；不整文件覆盖框架新改进。
- [ ] 记录 Food 的两个同名 `20260831_01` 和业务迁移依赖，形成不得整批复制的清单。
- [ ] 在获准实施后使用短期 `codex/unified-control-framework` 分支或等效隔离工作区，不重置任何来源分支。

**门禁**：每项需要吸收的行为有实现和测试来源；原中控 49 个独有提交不被当成可盲目合并的单一补丁。

## P1：有效能力、路由和装配隔离

**文件**：修改 `backend/app/core/assembly.py`、`backend/app/init_app.py`、`backend/app/core/auth_features.py`、`backend/app/config/setting.py`、`frontend/web/src/config/assembly/default.ts`；新增 `backend/app/core/control_features.py`、`backend/app/assemblies/control.toml`、`backend/app/assemblies/federated-saas.toml`；测试 `backend/tests/test_assembly.py` 和新增 `backend/tests/test_control_capabilities.py`。

- [ ] 先补三种装配的路由存在性、能力输出、配置依赖测试；独立 SaaS 不出现 `/control` 服务端端点，不加载中控 Worker。
- [ ] 对显式启用同步却禁用 SSO、启用强制检查却禁用同步、缺客户端凭据、无 Worker 发送配置分别断言启动失败。
- [ ] 把中控注册条件由固定 `assembly.name` 改为统一能力解析，后端、前端和任务共用相同语义。
- [ ] 复制中控基础路由和通用 seed 的相关补丁；沿用 `module_control` 权限命名空间，不复制 Food catalog/域名/品牌。
- [ ] 保持旧 standalone 配置默认行为，服务端和客户端角色误组合时报清晰错误；暂不支持同实例同时扮演两端。
- [ ] 运行定向测试并检查真实 OpenAPI 注册表，无重复路由或通过隐藏菜单绕过的写接口。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_assembly.py tests/test_control_capabilities.py -q
```

**门禁**：单 SaaS 不需要中控/Worker 也能完成本地登录；名称任意的自定义装配按能力工作。

## P2：统一数据库模型和迁移图

**文件**：吸收原中控 `backend/app/api/v1/module_control/` 模型和既有中控基础迁移；新增产品 `backend/app/api/v1/module_system/federated_access/model.py`；新增 `backend/app/api/v1/module_control/user_entitlement/model.py`；新增统一 revision `20260910_fa_access`、`20260910_fa_control_access`；修改 `backend/app/alembic/env.py` 仅限必要模型/图注册；测试 `backend/tests/test_migration_baseline.py`、新增 `backend/tests/test_control_upgrade_paths.py`。

- [ ] 先增加四项迁移图检查：ID 唯一、父节点存在、单个统一核心 head、不依赖 Food 业务 migration。
- [ ] 保留原框架/原 Control 已发布基础 revision 的内容及父节点，审阅原 Control 合并迁移的实际父节点后接新 revision。
- [ ] 从 Food 的两套账本迁移提取 SQL 语义，赋予新的全局唯一 ID；保留唯一约束、状态/version 检查、备份或清洗步骤的目的。
- [ ] 在隔离库分别测试空库、框架 `20260812_01`、原 Control `20260812_02` 到统一 head。测试样本预置用户、成员、人工角色和授权行，升级后比较 ID、关联、计数和权限。
- [ ] 对重复绑定和定义不匹配的遗留表断言失败并输出可操作报告；不通过 stamp 或忽略异常继续。
- [ ] 对新库默认创建空的可选中控表，验证不开 seed/能力时无中控入口或任务。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_migration_baseline.py tests/test_control_upgrade_paths.py -q
```

**门禁**：隔离 PostgreSQL 上升级通过、旧数据保留；Food 的已应用迁移未被改动。需要库连接时用测试专属配置，不读取并复用生产连接运行 upgrade。

## P3：通用产品角色策略与成员治理

**文件**：新增/吸收 `backend/app/api/v1/module_system/federated_access/default_role.py`、`tenant_role_lock.py`；修改 `backend/app/core/assembly.py`、`backend/app/api/v1/module_system/role/constants.py`、`role/model.py`、`role/service.py`、`user/crud.py`、`user/service.py`、`user/authorization.py`、`backend/app/api/v1/module_platform/tenant/service.py`、`package/service.py`。测试吸收 `test_default_federated_user_role.py`、`test_reconcile_default_user_roles.py`。

- [ ] 从 Food 测试移入角色/成员行为用例，用两个中立的测试产品装配替换三食品产品依赖。
- [ ] 新增 manual/declared 策略配置校验：默认 manual；declared 的空授权声明报错；保留管理命名空间被明确拒绝。
- [ ] 提取纯策略输入，替换 `ASSEMBLY_TO_PRODUCT` 与 `current_product` 导入；声明权限与套餐、装配及有效菜单求交集。
- [ ] 保留 Food 对无有效角色才补 USER、人工有效角色优先、系统角色不可越权修改、跨租户角色保留和最后 owner 保护的修复。
- [ ] 定义手工清空角色的结果并测试再次授权行为，避免把“清空角色”误当永久撤权。
- [ ] 验证套餐变化、禁用菜单、两个产品相同角色代码、同一用户多租户、并发人工分配和默认分配。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_default_federated_user_role.py tests/test_reconcile_default_user_roles.py tests/test_federated_user_authorization.py -q
```

**门禁**：框架通用角色模块没有 Food 专属导入；非 Food 配置也通过同样用例；不存在同步把普通员工提升为 superuser 的路径。

## P4：产品接收端的版本化访问资格

**文件**：吸收 `backend/app/api/v1/module_system/federated_access/schema.py`、`service.py`、`controller.py`；修改 `backend/app/api/v1/module_system/__init__.py`、`auth/control_sso_schema.py`、`auth/control_sso_service.py`、`auth/control_tenant_provisioning_service.py`；测试 `test_federated_access_entitlement.py`、`test_control_sso_client.py`、`test_control_tenant_provisioning.py`。

- [ ] 固定现有 `code` 请求和回执字段契约，测试关闭时 404、非法客户端/issuer/Site/映射被拒绝。
- [ ] 在产品事务内创建用户、成员、角色、访问账本和事件回执；原角色策略失败时不得遗留半个有效授权。
- [ ] 测试同事件重放返回原回执、低版本不覆盖、同版本异事件/指纹冲突、并发 active/inactive 以最高版本为准。
- [ ] manual 模式有访问资格但无角色时返回可区分状态并进入待授权页；declared 模式按声明补角色。
- [ ] 租户 owner 开户、普通用户授权、SSO 建会话全部接入同一资格约束，禁止跳过账本创建 token。
- [ ] 验证内部协议请求从读入之前就禁用操作日志，不在参数错误详情中回显启动码。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_access_entitlement.py tests/test_control_sso_client.py tests/test_control_tenant_provisioning.py -q
```

**门禁**：两个隔离产品以相同协议工作；错误 Site/租户不能通过同名用户或代码混淆获得访问。

## P5：产品撤权与所有会话入口

**文件**：吸收 `backend/app/api/v1/module_system/auth/session_registry.py`；修改 `auth/service.py`、`auth/controller.py`、`backend/app/core/dependencies.py`、`backend/app/core/database.py` 中相关初始化、`backend/app/api/v1/module_monitor/online/service.py`、流式鉴权调用点和 `init_app.py` 恢复注册。测试 `test_federated_session_revocation.py` 以及相关路由测试。

- [ ] 先移入 Food 的登录、退出、刷新、切租户、在线删除、锁失效和恢复用例，保留测试注入而不依赖真实用户库。
- [ ] 在独立只读会话中检查有效访问资格；修改会话前后按原锁序和 fencing 语义校验，不把数据库连接占用跨越 Redis 长等待。
- [ ] inactive 先持久化；Redis 清理失败保留 `session_cleanup_pending`，新请求继续拒绝。
- [ ] 验证用户 A 租户撤权不清除 B 租户会话；刷新、切租户、重登与撤权竞争不能复活已失效会话。
- [ ] 验证 WebSocket/SSE 使用同一资格边界及必要重检查；明确普通在途请求不追溯回滚。
- [ ] 恢复扫描验证有界、幂等、可续跑和正确锁所有权。使用隔离 Redis 补充 Lua 执行验证；mock 成功不算真实 Lua 验收。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_federated_session_revocation.py -q
```

**门禁**：产品收到撤权后，即使 Redis 清理失败也拒绝新业务请求；锁竞态、长连接用例和本地普通账号回归通过。

## P6：中控建用户、同步任务和撤权编排

**文件**：从 Food 吸收 `backend/app/api/v1/module_control/user_entitlement/`、中控 `model.py/service.py/schema.py/controller.py` 的关联补丁、`backend/app/plugin/module_control_provision/entitlement_handlers.py` 和 `plugin.toml`；必要的 `module_task/runtime/{context,dispatcher,executor,registry,state}.py` 补丁按 P0 逐项核对；不整体覆盖运行时。

- [ ] 移入中控用户创建、账本、票据、Worker 和 PostgreSQL 竞态测试，去除 Food 地址/目录依赖。
- [ ] 在用户创建事务中创建普通成员和每个授权任务；事务失败无残留，提交后派发失败能扫描恢复。
- [ ] 授权/revoke/regrant 递增版本；应用门户只允许 desired active 且同步成功、版本一致的用户启动。
- [ ] Worker 重查执行人权限、租户/应用状态和任务执行令牌；过时结果不能覆盖新意图。
- [ ] 补齐用户停用、删除成员、租户/应用停用、关闭企业产品的失效扇出；大批量操作保存批次/游标并可续跑，不在单个请求内无限 HTTP 循环。
- [ ] 目标超时/离线/成功但回执丢失/重试期间又撤权分别做故障注入；状态显示 pending/failed/superseded 的语义不能混用。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_control_user_entitlement.py tests/test_control_user_entitlement_model.py tests/test_control_user_entitlement_ticket.py tests/test_control_user_entitlement_worker.py tests/test_business_task_runtime.py -q
uv run pytest tests/test_control_entitlement_postgresql_races.py -q
```

**门禁**：持久化任务派发恢复、权限失效、乱序和产品离线测试通过；没有 RabbitMQ 依赖，也没有跨库直接写产品用户表。

## P7：前端完整反馈与授权恢复

**文件**：吸收 `frontend/web/src/views/module_system/user/components/ControlUserCreateDrawer.vue`、`frontend/web/src/views/module_control/user-grant/index.vue`、`frontend/web/src/api/module_control/index.ts`；修改 `views/module_control/portal/index.vue`、`views/module_system/auth/control-callback/`、`control-waiting/`、`store/modules/user.store.ts`、`utils/http/index.ts`、相关动态路由调用点；从 Food 提取有用 HTTP/冷启动修复，不带业务页面。

- [ ] 新建用户支持选已开通产品；独立 SaaS 保持原新建用户入口，中控普通员工不能看到管理权限。
- [ ] 每产品显示同步中/已生效/失败/撤权同步中及可读原因；创建接口成功不等于产品授权已生效。
- [ ] 只重试目标失败项，不重复创建中央用户；高版本撤权可以取代进行中的授权。
- [ ] 等待页“重新检查”重新加载身份、资格、有效菜单和动态路由，不能只刷新当前文档或复用旧缓存。
- [ ] 回调成功、明确失败、超时均能结束 loading；401 清理会话和动态路由，403 不误当未登录无限跳转。
- [ ] 运行相关 Vitest 和类型检查；类型生成产物若含无关修改，拆开审阅，不整包提交。

```bash
cd /Users/chou/code/FastapiAdmin/frontend/web
pnpm test -- src/__tests__/control-user-entitlement.spec.ts src/__tests__/control-sso-client.spec.ts src/__tests__/control-entitlement-recheck.spec.ts src/__tests__/control-entitlement-route-guard.spec.ts src/__tests__/control-sso-reload-recovery.spec.ts
pnpm exec vue-tsc --noEmit
```

**门禁**：UI 组件和状态测试通过，无品牌硬编码；本阶段不声称已完成真实浏览器验收。

## P8：回填、对账、协议兼容与整体定向验收

**文件**：吸收中控 `backend/app/scripts/backfill_user_entitlements.py`、`audit_user_entitlements.py`；产品 `backend/app/scripts/export_federated_entitlement_state.py` 与 `backend/app/scripts/reconcile_default_user_roles.py`；新增 `docs/framework/control-operations.md` 和 `docs/framework/control-upgrade-guide.md`；测试移入 `test_control_user_entitlement_backfill.py`、`test_control_user_entitlement_audit.py`。

- [ ] 工具默认只读预览，显式 apply 才写；输出 Site/租户/应用、来源版本、目标计数、异常，不输出密钥和票据。
- [ ] 只回填合法中央授权及租户映射，隔离无对应授权的历史影子账号，不以“有角色”推导 active。
- [ ] 对账比较身份、目标租户、期望/已应用版本、资格和有效权限；仅比较计数或角色名不足以通过。
- [ ] 使用框架新中控 -> 新通用产品、新中控 -> 固定 Food 产品协议、新产品 -> 固定 Food 中控协议三组定向 HTTP 契约验证；不连生产。
- [ ] 运行双 Site/双租户/双通用产品全链路定向测试及 PostgreSQL/Redis 故障注入。未配置集成环境时显式报告缺口。
- [ ] 对本次变更运行 Ruff 和 diff 检查；只对变更 Python 路径检查，不默认跑全仓全量测试。

```bash
cd /Users/chou/code/FastapiAdmin/backend
uv run pytest tests/test_control_user_entitlement_backfill.py tests/test_control_user_entitlement_audit.py tests/test_site_auth_boundaries.py -q
cd /Users/chou/code/FastapiAdmin
git diff --check
```

验收矩阵：

| 编号 | 必须通过的事实 |
|---|---|
| A01 | 独立 SaaS 原登录/角色行为通过，中控接口关闭 |
| A02 | 两 Site 同名企业/员工不串授权；错误 client 与 tenant 映射拒绝 |
| A03 | 新企业创建 owner，普通员工不能获平台管理员 |
| A04 | declared 默认角色可用，manual 明确待授权，人工有效角色保持 |
| A05 | 角色分配不能扩大套餐或跨租户，最后 owner 不能被删除 |
| A06 | 授权/撤权/再授权重复乱序以最高版本收敛 |
| A07 | 收到撤权后请求被拒，Redis 清理失败可补偿 |
| A08 | 刷新/租户切换/流式输出与撤权竞争不绕过资格 |
| A09 | 目标离线和发布丢失可恢复，未同步成功不显示已生效 |
| A10 | 三类核心库迁移保留用户、角色、授权和审计数据 |
| A11 | 回填可预览、幂等、限批；无来源授权不默认放行 |
| A12 | 两个中立产品通过，Food 协议兼容不要求同步升级数据库 |

**门禁**：每项列出代码/测试/集成或未验证证据。只有定向测试通过不自动标记生产完成。

## P9：下游与发布切换（独立执行范围）

- [ ] 框架完成并获准合并后，以一个主线版本交付；原 Control 和旧 SSO 分支不再作为长期功能分叉。
- [ ] 为 Food 中控与产品分别编制 schema 清单、旧 revision 映射和桥接迁移，保持 Food 专属业务链；不复制新核心 graph 后 stamp。
- [ ] 在数据库副本上验证桥接、回填、回滚及其后的正常业务写入，列明记录计数和角色授权保留情况。
- [ ] 获准部署后按接收端 -> 中控 -> 回填对账 -> 强制检查的顺序切换；显示可观测同步时延，不能承诺网络分区下瞬时撤权。
- [ ] 获准后做真实浏览器、Worker 和跨实例验收；这与当前只做定向验证的默认约定分开。
- [ ] 确认下游已接入及备份可恢复后，再考虑归档旧 Control 目录；不自动删除文件、远程或分支。

## 交付与提交边界

每个阶段独立审阅；若获准提交，按能力分组使用中文 Conventional Commit，暂存精确路径。提交、合并、推送、生产部署分别遵循当时授权。本计划不授权发布 GitHub 新仓库，也不修改当前 Git 远程配置。

2026-09-10 用户已授权新工作树实施。执行证据见 `docs/framework/control-validation.md`，来源见 `control-integration-source-map.md`。业务数据库和 Git 远程未修改；当前工作树保留待审阅。下方原清单保留为需求核对，不以来源测试名代表实际通过。
