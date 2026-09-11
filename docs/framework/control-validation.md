# 新工作树交付与验证记录

执行日期：2026-09-10。

- 工作树：`/Users/chou/code/FastapiAdmin-unified-control`
- 分支：`codex/unified-control-framework`
- 基线：`9690bdd40ab4bfaa1ea3b216fea3f53a00460eab`
- 状态：框架改动保留在新工作树，未提交、合并、推送或部署。

## 实现范围

一套代码支持独立 SaaS、可选中控和联邦产品。新增产品授权账本、开户/员工创建界面、持久化任务、版本回执与去重、撤权与会话补偿。中控和各产品运行自己的数据库。

从 Food 回收通用修复，并补充集成时发现的缺口：

- 后端实际路由和前端细能力开关一致。
- 应用管理 API/UI 能保存授权同步地址、开关和超时，避免字段被静默忽略。
- manual 产品允许资格同步成功后等待人工角色。
- 用户、租户、成员、应用/开通停用持久化撤权任务；共享身份跨 Site 精确处理。
- 停用对象仍能完成 inactive 票据兑换，active 则拒绝；不允许迁移尚有生效资格的目标标识。
- 撤权先阻断资格；在有界历史会话扫描及 Redis 清理完成后才更新完成标记。扫描未完成或失败继续恢复，不能漏掉未建索引的旧会话。
- 旧库有菜单时提供显式、幂等的中控种子同步。

## 定向验证

以下验证只针对本次影响范围，不是全仓测试或浏览器验收。

| 类别 | 命令/范围 | 证据 |
|---|---|---|
| 后端 | 本文下方列出的定向 pytest 文件 | **692 passed**，119.70 秒；32 个相关文件（含固定协议） |
| 实际数据库迁移 | `pytest --noconftest tests/test_control_upgrade_paths.py tests/test_migration_baseline.py` | 新库、原框架、原 Control；真实 PostgreSQL 临时集群，9 项通过；与 Redis 合并最终复验 **13 passed**，56.90 秒 |
| 真实 Redis | `pytest --noconftest tests/test_federated_session_redis.py` | 4 个场景；临时随机端口，真实 Lua/CAS/fencing/恢复水位 |
| 真实跨实例 | `pytest --noconftest tests/test_control_sso_integration.py` | **4 passed**，97.01 秒；同树 3 个独立库、3 个 API、真实 Celery/Redis |
| 固定 Food 协议 | `pytest tests/test_control_legacy_protocol.py` | 25 项；24 个样例在固定来源 schema 中实际验证，记录源 SHA 与 schema digest |
| 前端 | 13 组修改/新增 Vitest 文件 | 86 项通过；另有阶段性的相关基线回归 |
| 类型/静态 | `pnpm exec vue-tsc --noEmit`、变更 Python Ruff、变更 TS/Vue ESLint、`git diff --check` | 通过；112 个变更 Python 文件 Ruff 通过 |

测试使用私有临时 PostgreSQL/Redis，并验证资源归属，退出后清理。普通后端单测使用 SQLite/Redis 模拟；不可把单测通过单独当作真实并发证明。


真实双产品联调逐项验证：A 开通成功；B 无监听端口两次失败不影响 A，B 上线后走 HTTP 重试成功；同一中央员工在两产品各有唯一影子身份与普通角色；撤 A 后清理标记为 false、旧 token 被拒，B 的 token 继续有效；重新授权 A 后新 token 可用、旧 token 不复活。

除测试临时资源外未操作任何业务数据库。原 FastapiAdmin 主工作树仍只有规划阶段的两个未跟踪文档，原 Control 与 Food 来源代码未修改。

## 重现后端定向测试

从此工作树 `backend/` 运行下列文件。执行环境复用了本机原框架的 Python 虚拟环境，代码来自新工作树。迁移/真实服务测试单独运行，以免普通 conftest 的 SQLite/Redis 模拟介入。

```bash
python -m pytest \
  tests/test_assembly.py \
  tests/test_auth_feature_config.py \
  tests/test_business_task_runtime.py \
  tests/test_control_application_package.py \
  tests/test_control_capabilities.py \
  tests/test_control_lifecycle_revocation.py \
  tests/test_control_product_foundation.py \
  tests/test_control_provider.py \
  tests/test_control_provision_site_admin.py \
  tests/test_control_provision_ticket.py \
  tests/test_control_provision_worker.py \
  tests/test_control_sso_client.py \
  tests/test_control_tenant_provision.py \
  tests/test_control_tenant_provision_models.py \
  tests/test_control_tenant_provisioning.py \
  tests/test_control_user_access_sync.py \
  tests/test_control_user_entitlement.py \
  tests/test_control_user_entitlement_audit.py \
  tests/test_control_user_entitlement_backfill.py \
  tests/test_control_user_entitlement_model.py \
  tests/test_control_user_entitlement_ticket.py \
  tests/test_control_user_entitlement_worker.py \
  tests/test_default_federated_user_role.py \
  tests/test_federated_access_entitlement.py \
  tests/test_federated_session_revocation.py \
  tests/test_federated_user_authorization.py \
  tests/test_package_transition_closure.py \
  tests/test_reconcile_control_seed.py \
  tests/test_reconcile_default_user_roles.py \
  tests/test_site_auth_boundaries.py \
  tests/test_tenant_user_write_concurrency.py \
  tests/test_control_legacy_protocol.py \
  -q
```

## 未包含的验收

- 不包含 Food 历史业务库桥接、业务数据回填或生产迁移。
- 固定来源 schema/payload 兼容不代表旧服务真实混跑；旧中控 Worker 的业务规则不一定支持新版 manual 空角色回执。
- 未启动浏览器验收或执行完整构建。初期 `pnpm type-check` 内部 typegen 触发过 `vite.build(write:false)`，因缺组件提前失败；之后一律直接 `vue-tsc`，没有 dist/生成声明改动。
- Docker/Nginx 是通用部署模板，未执行容器构建或真实 TLS 部署。
- 网络分区下撤权存在传播窗口；离线恢复测试不等于生产网络时延保证。

后续使用说明见 [运行说明](control-operations.md)、[升级说明](control-upgrade-guide.md)、[来源清单](control-integration-source-map.md)。
