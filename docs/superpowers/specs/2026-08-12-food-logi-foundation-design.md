# Food Logi 第一阶段工程基线设计

## 目标

在一个非 Git 父目录中维护两个独立仓库：`control` 继承本地中控产品框架，`products` 继承本地通用框架。`products` 单仓维护三个可独立部署的食品物流 SaaS，不提供三系统合集装配。

## 已确认边界

- 产品系统分别为 `trace`、`agri`、`logistic`。
- 产品模块分别为 `module_food_traceability`、`module_agricultural_delivery`、`module_cold_chain_vehicle`。
- Assembly 分别为 `food-traceability`、`agricultural-delivery`、`cold-chain-vehicle`。
- 品牌 Site code 为 `data360` 与 `znceedi`，中文简称为“华夏电投”与“中能电投”。
- 生产域名为 `control|trace|agri|logistic` 分别位于 `data360.org.cn` 与 `znceedi.org.cn`；本地使用同构的 `.localhost` 域名。
- 每个产品的两个品牌 Host 共用该产品的一份前端产物、一个后端实例、一个数据库和一套 Redis 配置。
- 三个产品之间使用独立后端、数据库、Redis、构建产物、中控凭据和故障边界。
- 同一企业可同时购买两个品牌，但两个 Site 下建立独立租户，编码带 Site 前缀，数据、套餐、授权与 SSO 不互通。
- JWT 声明、Redis 会话、Host Site、Tenant Site 必须四方一致。

## 中控设计

中控运行一个部署单元，通过两个 `control` Host 解析两个 Site。当前 `ControlApplication` 是单 Site、单 callback 模型，因此保留六条应用记录：三产品乘两个品牌。目标产品必须先解析 Host Site，再选择该 Site 的服务端中控 Client 凭据兑换 SSO 或开户票据；不把凭据返回前端。

每个目标系统开户任务独立执行，一个失败不回滚其他系统成功结果。可重试临时错误首次失败十秒后自动重试一次，之后进入人工修改、重试或对账。开户结果必须校验目标租户编码与期望值一致。

## 产品装配设计

产品注册表统一声明每个业务模块的后端路由、模型、菜单根、权限前缀、前端组件前缀、Seed Pack、迁移范围和大屏入口。每个 Assembly 使用显式 allowlist，并对另外两个产品 fail closed。

`module_food_common` 只提供 Site/Tenant/Package 上下文、品牌公共配置、大屏状态与新鲜度、跨系统 HTTP/Event envelope 等稳定契约，不放置任何产品完整业务模型或 Service。

## 迁移设计

框架公共迁移与三个产品迁移分别管理。每个部署只执行 `core + 当前产品`，数据库不运行、不记录另外两个产品的业务迁移。禁止通过一个共享迁移链中的运行时 Assembly 分支跳过迁移。

## 大屏设计

每个系统提供一套认证后的聚合 API 和共享响应 Schema。服务端只从认证上下文解析 Site、Tenant、Package，并映射到品牌 variant；未知组合拒绝。两品牌页面可以有不同视觉布局，但共享指标口径、状态定义和聚合数据。状态固定为 `loading`、`normal`、`partial`、`stale`、`empty`、`error`，不使用随机数或伪实时数据。

## 第一阶段验收

第一阶段只交付工程装配、品牌契约、认证隔离、迁移边界、大屏契约、部署示例及自动化测试，不实现完整食品追溯、农配或冷链车辆业务页面。真实品牌全称、版权、客服和生产密钥保持未配置状态。
