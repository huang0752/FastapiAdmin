# 中控租户自动开通设计

## 1. 目标

在启用中控的部署中，平台管理员通过一个向导创建中控租户、首任管理员，并选择需要开通的产品及各产品套餐。中控随后在后台逐产品调用目标系统完成租户开户；首任管理员自动获得每个成功产品的应用访问权和目标产品本地 `owner` 权限。

本设计必须同时满足：

- 产品数量不固定，每个产品独立数据库和独立部署。
- 单个产品失败不回滚中控租户，也不影响其他产品。
- 目标产品的部门、岗位、角色和菜单仍由目标产品维护。
- 基础框架默认关闭自动开户，现有手工租户创建和普通 SSO 行为保持兼容。
- 自动开户可追踪、可对账，并只进行一次有限自动重试。

## 2. 已确认的产品边界

1. 创建中控租户时直接选择产品和对应套餐；成功后仍可追加产品。
2. 中控租户同步创建，目标产品租户在后台独立开通。
3. 首任租户管理员复用现有自动生成的 `{tenant_code}_admin`，继续一次性展示临时密码。
4. 首任管理员自动获得所有成功产品的中控应用授权，并在目标产品成为本地 `owner`；普通成员仍需目标产品本地授权。
5. 目标租户编码默认等于中控租户编码；发生冲突时允许按产品修改后人工重试。
6. 每个应用在中控维护可选目标套餐目录，向导按产品选择套餐。
7. 临时网络错误在 10 秒后自动重试一次；再次失败后只允许人工重试。
8. 本阶段实现创建、状态、重试和对账，不传播租户暂停或恢复，也不远程删除产品租户。
9. 中控与目标产品之间使用一次性开户码；目标产品使用现有 SSO 客户端凭据回中控兑换开户声明。
10. 租户增加可选的 18 位统一社会信用代码；同一站点内非空值唯一。

## 3. 总体架构

中控与目标产品不共享数据库，也不使用跨库事务。

```text
中控平台管理员
  -> Control 租户创建向导
  -> 中控租户 + 初始管理员 + 产品开通任务
  -> 后台任务逐产品生成一次性开户码
  -> 调用目标产品开户入口
  -> 目标产品回中控兑换开户声明
  -> 目标产品单事务创建租户、套餐、插件和联邦 owner
  -> 中控记录成功并建立应用开通与 owner 授权
```

### 3.1 中控职责

- 提供租户创建向导和产品套餐选择。
- 创建中控租户及首任管理员。
- 维护应用开户配置和目标套餐目录。
- 维护每个租户、每个应用的开通状态账本。
- 生成短期、单次使用的开户码。
- 触发目标产品开户并执行一次有限自动重试。
- 对失败记录提供修改、人工重试和对账。
- 成功后创建现有 `control_tenant_application`，并自动授权首任管理员。

### 3.2 目标产品职责

- 提供默认关闭的通用租户开户入口。
- 使用现有 `CONTROL_SSO_CLIENT_ID/SECRET` 回中控兑换开户声明。
- 校验目标 Site、套餐编码、目标租户编码和幂等映射。
- 在一个本地数据库事务中创建或复用租户、联邦映射、联邦 owner、成员关系、owner 角色和套餐插件。
- 重复或并发请求只能返回同一个本地租户。
- 不创建产品本地密码管理员，不同步中控部门、岗位或普通角色。

### 3.3 明确不做

- 中控不直连产品数据库。
- 不引入分布式事务。
- 不建设通用事件总线。
- 不传播暂停、恢复或删除。
- 不在首次开户接口中处理套餐变更、负责人转移。
- 不把中控角色或菜单复制到产品。

## 4. 数据模型

### 4.1 通用租户企业信息

`platform_tenant` 新增：

```text
unified_social_credit_code VARCHAR(18) NULL
```

规则：

- 输入去除首尾空格并转为大写。
- 非空值必须符合 18 位统一社会信用代码字符集并通过校验位算法，不能只检查长度。
- `(site_id, unified_social_credit_code)` 唯一；多个 `NULL` 合法。
- 仅平台管理员可以创建或修改，租户管理员只读。
- 不作为租户编码、账号或联邦身份主键。

### 4.2 中控应用开户配置

扩展 `control_application`：

```text
provisioning_url VARCHAR(500) NULL
provisioning_enabled BOOLEAN NOT NULL DEFAULT FALSE
provisioning_timeout_seconds INTEGER NOT NULL DEFAULT 10
```

开户地址必须显式配置，不能从前端 `base_url` 或 SSO `callback_url` 推导。

### 4.3 中控应用套餐目录

新增 `control_application_package`：

```text
site_id
application_id
code
name
description
target_package_code
is_default
status
sort
```

唯一约束：

```text
(application_id, code)
(application_id, target_package_code)
```

这里只保存中控展示套餐与目标产品本地套餐编码的映射，不保存目标数据库 ID。

### 4.4 中控开通状态账本

新增 `control_tenant_provision`：

```text
site_id
tenant_id
application_id
application_package_id
owner_user_id
provision_request_uuid
desired_target_tenant_code
target_tenant_code
target_tenant_uuid
status
attempt_count
max_attempts
last_error_code
last_error_message
next_retry_at
started_at
completed_at
```

状态只允许：

```text
pending
processing
succeeded
failed
```

唯一约束：

```text
(tenant_id, application_id)
provision_request_uuid
```

`control_tenant_provision` 是产品开通状态的唯一事实源。现有 `control_tenant_application` 只表示已经成功开通、可以进行用户授权和 SSO 启动的应用。

### 4.5 中控一次性开户票据

新增 `control_tenant_provision_ticket`：

```text
code_hash
provision_id
status
issued_at
expires_at
redeemed_at
```

只存储开户码哈希，不持久化明文开户码。

### 4.6 目标联邦租户映射

目标产品新增 `platform_federated_tenant`：

```text
site_id
issuer
central_tenant_uuid
central_tenant_code
local_tenant_id
provision_request_uuid
target_package_code
owner_central_user_uuid
created_at
```

唯一约束：

```text
(site_id, issuer, central_tenant_uuid)
(site_id, issuer, local_tenant_id)
provision_request_uuid
```

中控租户 UUID 是稳定幂等身份；可修改的租户编码不能替代它。

## 5. 接口

### 5.1 中控创建租户并申请产品开通

```text
POST /control/tenants/provision
```

请求：

```json
{
  "tenant": {
    "name": "示例企业",
    "code": "ACME01",
    "site_id": 1,
    "package_id": 2,
    "unified_social_credit_code": "91310000XXXXXXXXXX"
  },
  "applications": [
    {"application_id": 10, "application_package_id": 101},
    {"application_id": 11, "application_package_id": 205}
  ]
}
```

同一个中控数据库事务完成：

- 复用现有租户创建能力创建中控租户和 `{tenant_code}_admin`。
- 建立中控 owner。
- 校验所选应用和套餐属于同一 Site 且已启用。
- 为每个应用创建一条 `pending` 记录。
- 返回中控租户、一次性管理员凭据和初始产品状态。

该接口不等待目标产品开户。

### 5.2 目标开户入口

```text
POST /system/auth/control/tenant/provision
```

请求只包含：

```json
{"code": "short-lived-one-time-code"}
```

该入口仅在以下配置同时成立时启用：

```text
CONTROL_TENANT_PROVISIONING_ENABLED=true
CONTROL_SSO_ENABLED=true
CONTROL_SSO_ISSUER 非空
CONTROL_SSO_CLIENT_ID 非空
CONTROL_SSO_CLIENT_SECRET 非空
```

关闭时返回 `404`，不访问网络、不写数据库，也不暴露前端入口。

### 5.3 中控开户声明兑换

```text
POST /control/provisioning/exchange
```

目标产品使用现有 SSO BasicAuth 客户端凭据调用。兑换成功原子消费开户票据，并返回：

```json
{
  "provision_request_uuid": "...",
  "central_tenant_uuid": "...",
  "central_tenant_code": "ACME01",
  "tenant_name": "示例企业",
  "unified_social_credit_code": "91310000XXXXXXXXXX",
  "site_code": "default",
  "target_tenant_code": "ACME01",
  "target_package_code": "wms_pro",
  "owner": {
    "central_user_uuid": "...",
    "username": "ACME01_admin",
    "name": "示例企业管理员",
    "mobile": null,
    "email": null,
    "status": 0
  }
}
```

### 5.4 状态、重试与对账

```text
GET  /control/tenant-provisions?tenant_id={tenant_id}
PUT  /control/tenant-provisions/{id}
POST /control/tenant-provisions/{id}/retry
POST /control/tenant-provisions/{id}/reconcile
```

- `PUT` 只允许修改失败记录的目标租户编码或套餐。
- `retry` 只执行一次，不再触发自动重试链。
- `reconcile` 使用新的开户码调用相同幂等入口，核对目标事实并修复中控状态，不强制覆盖目标数据。
- 成功记录不能通过这些接口修改套餐、目标编码或 owner。

## 6. 目标产品事务与 owner 建立

目标产品兑换声明后，在一个本地事务中：

1. 按请求 Host 解析 Site，并与 `site_code` 比较。
2. 按 `target_package_code` 查找同 Site 下启用套餐。
3. 按 `(site_id, issuer, central_tenant_uuid)` 查询联邦租户映射。
4. 已存在且请求一致时返回 `already_exists`。
5. 已存在但套餐、信用代码或 owner 中央用户 UUID 不一致时返回漂移错误，不静默覆盖；姓名、手机、邮箱等身份资料仍由后续普通 SSO 更新。
6. 不存在时创建本地租户及 `platform_federated_tenant`。
7. 创建或复用联邦 owner 影子账号，建立 `FederatedIdentityModel` 和租户成员关系。
8. 调用现有 `ensure_tenant_owner()` 建立本地 owner 角色及有效菜单。
9. 调用现有套餐插件同步能力。
10. 由请求外层事务统一提交；任一步失败整单回滚。

需要把现有 SSO JIT 中“联邦身份和成员关系 upsert”抽成无内部提交的共享 primitive，由普通 SSO 登录和租户开户共同调用。普通 SSO 仍不自动分配角色；只有开户声明中的首任 owner 走 `ensure_tenant_owner()`。

## 7. 状态与失败处理

初次开通的执行规则：

```text
首次执行
  成功 -> succeeded
  临时失败 -> 10 秒后自动重试一次
    成功 -> succeeded
    再次失败 -> failed，等待人工重试
```

- `max_attempts=2`，包含首次执行和一次自动重试。
- 人工重试只执行一次，失败后保持 `failed`。
- 连接失败、超时、HTTP 429、502、503、504 可自动重试。
- Site/套餐/凭据/配置错误、编码冲突、请求无效和数据漂移不可自动重试。
- 每次尝试签发新的开户码，`provision_request_uuid` 保持不变。
- 使用数据库条件更新或行锁原子认领 `pending/failed` 记录，避免并发 Worker 重复执行。
- `processing` 超过配置的开户超时后，由下一次后台任务扫描或人工操作前检查标记为失败；只读状态查询本身不修改数据，也不引入租约系统。
- 错误表只记录稳定错误码和安全摘要，不记录开户码、密钥或完整目标响应。

若目标已经提交但中控没有收到响应，人工重试或对账会通过联邦租户映射返回同一个本地租户，中控再补写成功状态、应用开通和 owner 授权。

## 8. 前端设计

Control Assembly 下，现有租户创建改为三步向导：

1. **租户资料**：现有字段，并增加“企业信息”分组和统一社会信用代码。
2. **产品与套餐**：勾选已启用应用，为每个应用选择目标套餐；目标租户编码默认使用中控租户编码。
3. **确认创建**：汇总租户、管理员账号、产品和套餐。

提交成功后一次性展示管理员临时密码，并展示每个产品的状态。

现有“租户应用开通”页改为高级运维页：

- 查询产品开通状态、尝试次数和安全错误摘要。
- 修改失败记录的套餐或目标租户编码。
- 人工重试和对账。
- 成功记录只读。
- 撤销只影响中控应用访问，本阶段不删除或暂停目标租户。

“应用管理”增加开户接口、启用开关、超时时间和目标套餐目录管理。

基础版以及 Control runtime capability 关闭时：

- 现有租户创建 DOM、请求和响应保持不变。
- 不出现产品选择步骤或开通管理菜单。
- 不发起开通相关请求。

## 9. 权限

新增平台权限：

```text
module_control:application_package:query
module_control:application_package:create
module_control:application_package:update
module_control:application_package:delete
module_control:tenant_provision:query
module_control:tenant_provision:create
module_control:tenant_provision:update
module_control:tenant_provision:retry
module_control:tenant_provision:reconcile
```

只有中控平台管理员可以维护应用套餐和执行租户开通。租户管理员保留现有用户应用授权与应用中心权限，不获得平台开户权限。

首任管理员在产品成功开通后自动获得：

- 中控侧该应用的访问授权。
- 目标产品本地 owner。
- 目标产品 owner 模板当前允许的用户、部门、岗位、角色和菜单管理权限。

## 10. 迁移策略

基础框架先增加基于当前框架 head 的迁移：

- `platform_tenant.unified_social_credit_code`
- `platform_federated_tenant`

Control 当前拥有自己的 Provider migration head。合并框架迁移后，Control 使用 Alembic merge revision 合并两个 head，再在线性后续迁移中增加：

- `control_application` 开户字段
- `control_application_package`
- `control_tenant_provision`
- `control_tenant_provision_ticket`

迁移必须验证既有数据库和随机空 PostgreSQL 数据库的 upgrade、downgrade、re-upgrade，不能手工修改 `alembic_version`。

## 11. 测试与验收

### 11.1 基础框架

- 默认关闭返回 404、无网络和数据库副作用。
- 配置不完整时 fail-fast。
- 原手工创建租户仍生成本地初始管理员和一次性密码。
- 原普通 SSO JIT 仍创建无本地角色的联邦用户。
- 自动开户创建租户、企业信用代码、套餐、插件、联邦 owner 和有效菜单。
- 首任 owner 后续 SSO 复用同一用户并保留 owner。
- 重复和并发请求只产生一个租户及映射。
- Site、套餐、信用代码、编码冲突和 owner 失败整单回滚。
- 不产生目标产品本地密码管理员。

### 11.2 中控

- 一次请求创建中控租户、首任管理员和多条 `pending` 记录。
- 多个应用独立成功或失败。
- 临时错误 10 秒后只自动重试一次。
- 第二次失败停止；业务错误不自动重试。
- 人工重试只执行一次。
- 成功后创建 `control_tenant_application` 并授予首任管理员。
- 失败产品不影响中控租户和其他成功产品。
- 修改失败记录后可重试。
- 对账可修复“目标已成功、中控未记账”。
- 开户码单次兑换、过期和并发消费安全。
- 基础版前端不出现新增步骤、菜单或请求。

### 11.3 真实闭环

使用中控数据库、两个目标产品数据库、Redis 和真实 HTTP 进程验证：

```text
创建中控租户
-> WMS 自动开通成功
-> MES 第一次模拟超时
-> 10 秒后 MES 自动重试成功
-> 首任管理员获得两个应用授权
-> 首次进入两个产品都直接拥有本地 owner 菜单
-> 普通成员进入后仍为待授权
-> 重复开户不产生重复租户
```

最终门禁包括 Ruff、全量 pytest、Alembic heads、随机 PostgreSQL 迁移往返、前端 type-check、Vitest、生产构建和浏览器人工验收。

## 12. 提交与发布边界

- 框架侧通用租户字段、联邦租户映射、默认关闭的目标开户适配器先独立提交。
- Control 侧迁移、编排服务、任务执行、权限 Seed 和前端向导按领域分组提交。
- 每项行为严格执行 RED -> GREEN；跨仓库真实集成最后收口。
- 不纳入本地日志、构建产物、临时数据库信息或用户未跟踪文件。
- 未经明确要求不 push。
