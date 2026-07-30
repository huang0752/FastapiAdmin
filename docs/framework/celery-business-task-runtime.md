# Celery + Redis 通用业务任务运行时

`module_task` 提供可选的 Celery 业务后台任务能力，适合导入、导出、报告和批量计算等长任务。它不包含具体产品处理器，也不改变 APScheduler 的现有能力。

## 职责边界

| 组件 | 职责 |
|---|---|
| APScheduler | Cron、Interval、Date、手工定时触发；每 30 秒触发一次待发布恢复扫描 |
| Celery | 独立 Worker 排队、消费、重投、队列路由和有限退避 |
| PostgreSQL | 任务状态、结果摘要、幂等、租约、心跳、重试次数和审计的唯一事实来源 |
| Redis | 仅作为 Celery Broker；不保存业务结果 |

定时长任务应由 APScheduler 到点调用 `BusinessTaskDispatcher`，再交给 Celery Worker，不在 APScheduler 进程内执行长业务。

## 安装和启用

仓库锁定 `celery[redis]==5.6.3`、Kombu 5.6.2 和 redis-py 6.4.0。Kombu 5.6.2 的官方 Redis extra 要求 redis-py `<6.5`，因此这里的 6.4.0 是 Python 客户端版本；它不要求降低 Redis Server 7.x/8.x。

从 `backend/` 安装：

```bash
uv sync --frozen
# 或生产镜像
pip install -r requirements.txt
```

同时满足以下条件才会启用运行时：

1. 当前 Assembly 启用 `module_task`；
2. 环境变量 `CELERY_ENABLED=true`。

禁用 `module_task` 时，FastAPI 不构造 Celery app、不连接 Broker、不加载插件任务模块，也不要求 Worker 存活。只关闭 `CELERY_ENABLED` 时，APScheduler 和原有任务管理功能仍然可用。

## 环境变量

```dotenv
CELERY_ENABLED=true
CELERY_BROKER_URL=redis://localhost:6379/2
CELERY_DEFAULT_QUEUE=business_tasks
CELERY_BROKER_KEY_PREFIX=fastapiadmin:celery:
CELERY_TASK_SOFT_TIME_LIMIT=1800
CELERY_TASK_TIME_LIMIT=2100
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_WORKER_CONCURRENCY=2
CELERY_TASK_ACKS_LATE=true
CELERY_TASK_REJECT_ON_WORKER_LOST=true
CELERY_MAX_RETRIES=3
CELERY_RETRY_BACKOFF=30
CELERY_HEARTBEAT_INTERVAL=15
CELERY_LEASE_SECONDS=60
CELERY_BROKER_VISIBILITY_TIMEOUT=3600
```

默认关闭 Result Backend，并强制 `json` serializer、`accept_content=["json"]`、预取 1、延迟确认和 Worker 丢失重投。配置必须满足：心跳小于租约，soft time limit 小于 hard time limit，hard time limit 小于 Redis visibility timeout。

每套部署应使用独立 Redis DB，或至少配置唯一的 `CELERY_BROKER_KEY_PREFIX` 和队列。Redis Cluster 只能使用 DB 0 时必须保留唯一前缀。生产远程 Broker 应启用 ACL、网络隔离和 `rediss://`；健康输出和 Worker 日志不会打印密码或完整 URL。

## 独立 Worker

开发环境：

```bash
cd backend
CELERY_ENABLED=true uv run celery \
  -A app.plugin.module_task.runtime.worker:celery_app worker \
  --loglevel=INFO \
  --queues=business_tasks
```

macOS 本地可在验证 Broker/Worker 通路时追加 `--pool=solo --concurrency=1`。`solo` 不能证明 Linux 生产的 soft time limit 和 prefork 行为。

Linux 生产建议使用默认 prefork，并通过环境变量设置保守并发：

```bash
CELERY_ENABLED=true CELERY_WORKER_CONCURRENCY=4 \
uv run celery -A app.plugin.module_task.runtime.worker:celery_app worker \
  --loglevel=INFO --queues=business_tasks
```

Web 和 Worker 必须使用同一代码版本、Assembly、数据库与配置来源。Worker 使用独立 `NullPool` AsyncEngine，每个消息创建自己的 `AsyncSession` 和事件循环，不继承 Web 请求 Session 或连接池。

## 插件注册处理器

插件在自己的 `plugin.toml` 声明显式扩展点，路径必须位于该插件包内：

```toml
[runtime]
business_task_modules = ["app.plugin.module_reports.business_tasks"]
```

注册模块使用服务端装饰器；编码必须全局唯一，冲突会在 Worker 启动时直接失败：

```python
from pydantic import BaseModel

from app.plugin.module_task.runtime.registry import register_business_task


class ReportPayload(BaseModel):
    report_id: int


@register_business_task(
    handler_code="reports.generate",
    module="reports",
    payload_schema=ReportPayload,
    default_queue="business_tasks",
    max_retries=2,
    supports_cancel=True,
    required_permissions=("module_reports:report:generate",),
)
async def generate_report(context, payload: ReportPayload) -> dict:
    await context.check_cancelled()
    await context.update_progress(20)
    # 使用 context.auth/context.db 调用现有租户隔离 CRUD。
    await context.heartbeat()
    await context.check_cancelled()
    return {"report_id": payload.report_id, "state": "generated"}
```

处理器只允许服务端注册，不接受客户端提供 Python 路径、函数名、shell、动态代码或 pickle。`required_permissions` 会在每次执行前按当前 actor 权限重新验证，并与在线请求共用角色状态、菜单状态和租户套餐可用菜单解析逻辑。菜单停用、套餐停用或套餐移除权限后，尚未开始的任务会拒绝执行。

## 内部投递

HTTP 不提供任意 handler 创建接口。业务服务通过可信 `AuthSchema` 投递：

```python
from app.plugin.module_task.runtime.dispatcher import BusinessTaskDispatcher, DispatchRequest

task = await BusinessTaskDispatcher().dispatch(
    auth=auth,
    request=DispatchRequest(
        handler_code="reports.generate",
        module="reports",
        biz_type="report",
        biz_id=str(report.id),
        title="生成报告",
        payload={"report_id": report.id},
        idempotency_key=f"report:{report.id}:v1",
        max_retries=2,
    ),
)
```

`tenant_id` 和 `actor_user_id` 只取自服务端 `AuthSchema`，不会从 payload 接受。Celery 消息正文只传 `business_task_id`；不要放入 JWT、Cookie、密钥、SMTP/AI 凭证、ORM 对象、Session、Redis 连接、文件二进制或大型数据。文件任务应传租户隔离的对象存储键。

## 状态机与可靠投递

```text
pending ──发布成功──> queued ──租约抢占──> running ──成功──> success
   │                     │                    ├─临时错误──> retrying ──> running
   ├─发布失败──> enqueue_failed ──重投──> queued
   └─取消──────────────> canceled <──────────┘
running ──不可重试错误/重试耗尽──> failed
```

`success`、`failed`、`canceled` 是终态，普通状态更新不能回退；人工 retry 是受权限、租户、handler 和次数约束的专用动作。进度必须在 0–100 内单调增加，成功固定为 100。

投递流程是：

1. 专用短事务插入 `pending` 并提交；
2. 使用确定性 `business-task-<id>` 发布消息；
3. 发布成功条件更新为 `queued`；失败记录为 `enqueue_failed`；
4. APScheduler 恢复扫描安全重投 `pending`、`enqueue_failed` 和租约过期的 `running`；
5. 重投始终复用原业务任务 ID、Celery task ID 和租户幂等约束。

即使 Broker 成功但状态回写失败，Worker 也能从 `pending/enqueue_failed/queued/retrying` 原子抢占；queued 回写不会覆盖已经进入 `running/success` 的记录。

## 至少一次、幂等和租约

Celery + Redis 是至少一次投递。数据库唯一约束 `(tenant_id, idempotency_key)` 防止同一租户重复创建；不同租户可复用相同键。Worker 用条件 `UPDATE` 原子写入 `execution_token`、租约、心跳和递增 attempt，重复消息或旧 Worker 不能覆盖新租约所有者。

Worker 崩溃后，late ack/reject-on-lost 会重投消息；如果重投早于租约过期，会延迟再试。租约过期后才能重新抢占。框架只能防止同一任务记录被并发执行，处理器仍必须让外部写入、文件生成和第三方调用具备业务幂等键。

## 重试与取消

只有 `RetryableBusinessTaskError` 或注册项 `retryable_exceptions` 中明确声明的异常自动重试，使用有限次数和指数退避。payload 校验、未知 handler、actor/权限失效和普通业务错误不自动重试；耗尽后进入 `failed` 并保存安全错误摘要。

`pending/enqueue_failed/queued/retrying` 可直接取消。`running` 只有在处理器声明 `supports_cancel=True` 时接受协作取消，写入 `cancel_requested_at`；处理器须在事务安全点调用 `context.check_cancelled()`。框架默认不使用 `revoke(terminate=True)` 强杀正在执行数据库事务的 Worker。

## 多租户和后台 actor

Worker 根据任务表可信的 `tenant_id`、`created_id`、task ID 和 trace ID 重建上下文。执行前重新检查用户是否存在、是否启用、租户是否有效、普通用户是否仍属于该租户，以及 handler 所需权限。后台上下文强制租户范围，即使实际 actor 是平台管理员也不会借任务获得跨租户写权限；当前协议不允许缺少 actor 的隐式“系统超级管理员”任务。

普通 query/detail/cancel/retry 都经过现有 `AuthSchema`、数据权限和租户过滤。monitor 权限才能查看脱敏 handler、queue、Celery task ID、租约和 trace；普通响应不返回原始 payload、execution token 或完整内部错误。

## 健康检查和恢复

`GET /api/v1/task/business/task/monitor/health` 需要 `module_task:business_task:monitor`，状态为：

- `module_task_disabled`
- `celery_disabled`
- `broker_unreachable`
- `broker_reachable_no_worker`
- `worker_available`

该诊断不影响 FastAPI 基础 readiness。故障恢复顺序：先恢复 PostgreSQL，再恢复 Redis Broker，再启动同版本 Worker；待发布扫描会补发数据库中的可恢复任务。不要手工删除 Broker 未确认键，也不要把 Celery Result Backend 当作恢复依据。

## 权限

初始化会在 `module_task` 启用时幂等补齐：

- `module_task:business_task:query`
- `module_task:business_task:detail`
- `module_task:business_task:cancel`
- `module_task:business_task:retry`
- `module_task:business_task:monitor`

角色仍需由管理员显式授予这些权限。monitor 是平台运行诊断权限，不应授予普通租户角色。

## 迁移和回滚

迁移 `20260730_01` 接在 `20260729_f01` 之后，扩展现有 `task_business_task`，不会创建重复主表。旧记录的 handler 保持 `NULL`，升级后可查询但不会执行。降级会先把 `queued/retrying/enqueue_failed` 映射为旧版本可识别的 `pending`。

若历史环境由 `create_all()` 建库且没有 `alembic_version`，不要盲目执行 upgrade 或 stamp。先备份并核对 Schema 与 `20260729_f01` 完全一致，再 `alembic stamp 20260729_f01`，最后升级；不一致时应先制定专门迁移方案。
