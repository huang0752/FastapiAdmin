# 统一中控集成来源与范围

实施目录：`/Users/chou/code/FastapiAdmin-unified-control`。分支：`codex/unified-control-framework`。原始工作树与远程配置保持不变。

## 固定来源

| 来源 | 提交 | 用途 |
|---|---|---|
| FastapiAdmin | `9690bdd40ab4bfaa1ea3b216fea3f53a00460eab` | 当前框架基线，保留框架已有改进 |
| FastapiAdmin-Control | `109ba804d2ce27bea00ba1667ca43ed41ba11c4a` | 中控基础、既有迁移、通用种子和文档 |
| Food control | `d19ec0b1c44380a7b269e4888b086c886015642f` | 开户、员工授权、任务可靠性、前端操作闭环 |
| Food products | `593e8e7cb5edf9ad0db5a7e7118820cb6a82a47d` | 接收端资格、成员角色治理、SSO及会话撤权 |

Food 的实际路径使用不换行连字符：`/Users/chou/code/food‑logi/control` 与 `products`。来源不是本次发布目标，不修改其历史或数据库。

## 吸收与重写

| 目标文件组 | 来源及适配 | 验证入口 |
|---|---|---|
| `module_control/`、`module_control_provision/` | Food Control 通用服务；补生命周期撤权、manual 回执兼容、细能力路由裁剪，以及原 API 遗漏的授权同步配置字段 | `test_control_provider`、`test_control_user_entitlement*`、`test_control_lifecycle_revocation` |
| `module_system/federated_access/` | Food 产品资格与回执；替换产品目录映射为 assembly.application_code 与显式角色策略 | `test_federated_access_entitlement`、`test_default_federated_user_role` |
| `auth/session_registry.py`、auth/role/user/tenant/package 共享调用点 | Food 会话 fencing、角色边界与成员锁，保留主线能力；补 manual 不被隐式 USER 覆盖 | `test_federated_session_revocation`、`test_tenant_user_write_concurrency`、真实 Redis 测试 |
| `module_task/runtime/`、业务任务重试服务 | domain closure、执行代际、先前失败收口；生命周期专用上下文不扩大通用任务权限 | `test_control_user_entitlement_worker`、`test_business_task_runtime` |
| 前端中控视图与用户创建 | Food Control 页面，按能力显式开放 | `control-provider-portal`、`control-user-entitlement`、`control-capabilities` Vitest |
| 前端 SSO/会话/HTTP | Food 产品精确补丁，补退出、迟到响应、目标租户三项竞态 | `control-sso-*`、`control-entitlement-*` Vitest |
| 角色校正、授权导出与审计工具 | Food 工具通用化；不限三个产品，manual 待授权单独报告 | `test_reconcile_default_user_roles`、`test_control_user_entitlement_audit` |
| 种子升级工具 | 新增，旧数据库只补中控菜单及明确范围套餐关联 | `test_reconcile_control_seed` |

## 迁移

原 Control 四个 revision 保持文件和父节点不变：`20260810_02`、`20260811_02`、`20260811_03`、`20260812_02`。新的统一 head 为 `20260910_fa_control_access`，父节点是 `20260910_fa_access` → `20260812_02`。

Food 中控与产品各有内容不同的 `20260831_01`。本次不复制这两个 ID，不接 Food 业务链，不 stamp。测试验证新库、原框架、原 Control；Food 历史库桥接另做下游升级。

## 明确未带入

未加入 Food 域名、品牌、食品业务模块或业务种子；框架基线已存在的相关文件未作全面清理。`frontend/web/build/foodLogiViewBoundary.ts` 仅修复原有索引类型错误。没有引入 RabbitMQ，没有新增远程仓库。

配置修正：同一产品实例支持一个 issuer 下多 Site 的独立 client 凭据；不是“一个实例只能一组 client”。产品标识 application_code 与 OAuth 客户端标识 client_id 分开。
