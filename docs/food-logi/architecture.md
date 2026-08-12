# Food Logi Products 架构

`products` 是一个 Git 仓库，但只产出三个独立 SaaS：`trace`、`agri`、`logistic`。`product_manifest.py` 是产品模块的一等注册表，Assembly 对业务模块显式 allowlist。仓库不提供三系统合集 Assembly。

`module_food_common` 只放 Site/Tenant/Package 上下文、大屏状态与错误契约，不放追溯单、配送单、车辆、温控或轨迹等业务实体。

第一阶段只建立装配、认证、迁移、构建、部署和大屏 Schema 边界，不实现完整业务页面。
