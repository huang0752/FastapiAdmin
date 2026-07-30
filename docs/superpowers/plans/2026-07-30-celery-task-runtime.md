# Celery 业务任务运行时实施计划

> **执行要求：** 使用 `executing-plans` 分阶段实施，每个阶段先补失败测试，再完成最小实现与聚焦验证。

**目标：** 为 `module_task` 增加可选的 Celery + Redis 长任务运行时，使用 PostgreSQL 保存任务事实状态，并保持 APScheduler 的现有定时调度职责不变。

**架构：** Web 进程通过内部 `BusinessTaskDispatcher` 先持久化任务，再向 Redis Broker 发布仅含 `business_task_id` 的 Celery 消息；独立 Worker 从当前 Assembly 的已启用插件加载服务端注册处理器，通过数据库原子租约抢占、心跳、幂等、有限重试和协作取消完成执行闭环。Celery Result Backend 关闭，数据库是状态和结果的唯一事实来源。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy Async、PostgreSQL、Celery 5.6.3、Kombu 5.6.2、redis-py 6.4.0、Redis Broker、Alembic、pytest。

---

## 阶段一：依赖、配置和可选装配

**文件：**

- 修改：`backend/pyproject.toml`
- 修改：`backend/uv.lock`
- 修改：`backend/app/config/setting.py`
- 修改：`backend/app/core/assembly.py`
- 新增：`backend/app/plugin/module_task/runtime/celery_app.py`
- 新增测试：`backend/tests/test_business_task_runtime.py`

1. 写入 Celery JSON 序列化、关闭结果后端和 `module_task` 禁用态测试。
2. 锁定 `celery[redis]==5.6.3` 与官方兼容的 `redis==6.4.0`，更新 uv 锁文件。
3. 增加通用 Celery 配置和 Broker 键前缀，配置默认保守的预取、延迟确认、丢失重投、超时、重试、心跳和租约。
4. 实现惰性 Celery app 工厂，确保 Web 启动不连接 Broker、不启动 Worker；禁用态不加载任务模块。
5. 运行配置和禁用态聚焦测试、Ruff。

## 阶段二：任务事实模型、状态机和迁移

**文件：**

- 修改：`backend/app/plugin/module_task/business/task/model.py`
- 修改：`backend/app/plugin/module_task/business/task/schema.py`
- 修改：`backend/app/plugin/module_task/business/task/service.py`
- 新增：`backend/app/plugin/module_task/runtime/state.py`
- 新增迁移：`backend/app/alembic/versions/20260730_01_add_business_task_runtime.py`
- 修改测试：`backend/tests/test_business_task_runtime.py`

1. 先写合法/非法状态转换、进度单调、终态不可回退、租约字段和租户幂等测试。
2. 在现有 `task_business_task` 上增加 handler、队列、外部任务 ID、幂等键、attempt、租约/心跳、取消、错误码、trace 和时序字段。
3. 增加 `(tenant_id, idempotency_key)` 唯一约束和恢复扫描所需索引，不修改已发布迁移。
4. 实现显式状态机与安全错误摘要。
5. 运行模型/服务聚焦测试以及 SQLite 迁移静态校验。

## 阶段三：注册表、投递、租约执行和恢复

**文件：**

- 新增：`backend/app/plugin/module_task/runtime/registry.py`
- 新增：`backend/app/plugin/module_task/runtime/context.py`
- 新增：`backend/app/plugin/module_task/runtime/dispatcher.py`
- 新增：`backend/app/plugin/module_task/runtime/executor.py`
- 新增：`backend/app/plugin/module_task/runtime/worker.py`
- 新增：`backend/app/plugin/module_task/runtime/loader.py`
- 新增：`backend/app/plugin/module_task/runtime/exceptions.py`
- 修改：`backend/app/plugin/module_task/plugin.toml`
- 修改测试：`backend/tests/test_business_task_runtime.py`

1. 先写注册成功、重复冲突、未知处理器、payload 校验和只发送任务 ID 的测试。
2. 实现显式注册描述符和 Assembly 启用插件的任务模块扩展点；冲突在 Worker 启动阶段失败。
3. 实现数据库先落盘、发布后 queued、失败后 enqueue_failed、同 ID 安全重投与租户幂等。
4. 实现数据库条件更新的原子租约抢占、重复消费 no-op、过期租约恢复、心跳和进度更新。
5. 实现 actor 安全重建、显式可重试异常、指数退避、达到上限失败和协作式取消。
6. 运行注册/投递/执行聚焦测试和 Ruff。

## 阶段四：安全 API、权限和健康诊断

**文件：**

- 修改：`backend/app/plugin/module_task/business/task/controller.py`
- 修改：`backend/app/plugin/module_task/business/task/schema.py`
- 修改：`backend/app/plugin/module_task/business/task/service.py`
- 新增：`backend/app/plugin/module_task/runtime/health.py`
- 修改：`backend/tests/test_api_module_task.py`
- 修改：`backend/tests/test_business_task_runtime.py`

1. 先写无任意 handler 创建接口、cancel/retry/monitor 权限、跨租户拒绝和诊断脱敏测试。
2. 移除对外任意创建及任意状态写入口，保留内部兼容创建服务。
3. 增加安全取消、受限重试、Broker/Worker 分层健康和管理员诊断接口。
4. 沿用 `AuthSchema` 和现有 CRUD 租户边界，不新增超级管理员判断。
5. 运行 API、权限和租户隔离聚焦测试。

## 阶段五：Worker 入口、文档和最终验证

**文件：**

- 新增：`backend/app/plugin/module_task/runtime/worker.py`
- 新增：`docs/backend/celery-business-tasks.md`
- 修改：`backend/README.md`
- 修改：相关测试与部署示例（仅通用框架内容）

1. 提供独立 Worker app/启动入口，启动日志只输出 Assembly、启用插件、处理器、脱敏 Broker 和队列。
2. 文档说明 APScheduler/Celery 边界、配置、注册/投递协议、状态机、幂等/租约/心跳/重试/取消、多租户和恢复流程。
3. 运行全部聚焦测试、`uv run ruff check app tests`、后端完整 pytest。
4. 使用真实 PostgreSQL 完成空库 upgrade、旧 head 升级、downgrade/upgrade 往返。
5. 使用真实 Redis 和独立 Celery Worker完成通用处理器端到端烟雾验证。
6. 精确暂存，检查 staged diff 和 `.understand-anything/` 排除情况，按独立问题提交中文 Conventional Commit。
