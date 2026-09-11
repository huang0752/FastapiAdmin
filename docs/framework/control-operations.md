# 统一框架中控运行说明

## 装配与数据库

一个框架代码版本可以部署为三个形态：

| 形态 | 装配 | 数据与依赖 |
|---|---|---|
| 独立 SaaS | `saas-admin` 或兼容 `default` | 自己的业务数据库；中控开关默认关闭 |
| 中控 | `control` | 自己的数据库，Redis、Celery Worker、待投递扫描器 |
| 接入中控的产品 | `federated-saas` 模板或产品自有装配 | 自己的数据库与会话 Redis；按产品声明身份和业务权限 |

同一数据库服务可以承载多个独立数据库，数据库账号权限分别配置。产品不读取中控库，也不直接读取其他产品库。业务产品可保留自己的仓库；框架能力只维护一条主线。

`APP_ASSEMBLY` 选择内置名称，`APP_ASSEMBLY_FILE` 可指定自定义 TOML。中控角色按 `sso_provider` 能力判断，不依赖名称。`app_portal`、`control_user_entitlements`、`tenant_auto_provisioning` 同时控制对应前端入口和后端 API。不要在仍有待同步任务时关闭交换端点。

## 身份与权限

中控负责中央身份、企业成员关系、应用开通和用户产品资格。产品负责本地业务角色、套餐边界和数据范围；中控普通员工不自动获得产品管理员权限。

中控创建员工：一个事务写中央用户、门户普通角色、所选产品 grant 和任务 outbox。返回成功表示已提交，只有目标回执成功才表示同步完成。

产品默认是 `manual`：资格可以同步成功，但没有本地角色的用户进入等待授权页。`declared` 会按产品声明、套餐、装配和有效菜单求交集，只在没有有效本地角色时补 `USER`，数据范围默认本人。声明不能包含管理命名空间或通配符。

```toml
[assembly]
name = "my-product"
application_code = "my-product"

[federation.default_role]
mode = "declared"
permission_codes = ["module_orders:order:query"]
# 也可用 permission_prefixes = ["module_orders"]，但显式权限更易审计。
```

声明中的权限必须由产品真实菜单种子定义并纳入套餐。模板不会凭空创建业务菜单。不要用人工清空角色表示永久撤权；永久取消产品访问应撤销中控资格。

## 通信与恢复

中控数据库任务账本 → Celery/Redis → 产品 HTTP。短期不引入 RabbitMQ。数据库事务先提交任务，发布失败由每 30 秒待投递扫描恢复；Worker 按版本与事件号处理，重复消息不能重复开户或覆盖较新撤权。

产品取得短时单次票据后回中控兑换声明，核对 issuer、client、Site、application_code、目标租户和身份。产品先提交 inactive 资格，再清理 Redis 会话；清理失败留 `session_cleanup_pending`，后台恢复继续处理。请求、刷新、切租户、已实现的 AI WebSocket 流均检查对应资格与会话代际。

网络分区时不能承诺瞬时远程撤权。目标收到撤权前仍可能使用其最后已知资格；需监控待同步数量、最老等待时长、失败原因和恢复积压。源用户/租户/成员/应用开通的生命周期撤权持久化，不因被撤对象已停用而放弃执行。

## 客户端配置

从 `backend/env/.env.*.example` 复制到自己的安全运行配置。产品启用 `CONTROL_SSO_ENABLED` 后必须配置可信 issuer 和客户端凭据。多 Site 可设置：

```dotenv
CONTROL_SSO_SITE_CLIENTS={"site-a":{"client_id":"client-a","client_secret":"replace-me"},"site-b":{"client_id":"client-b","client_secret":"replace-me"}}
```

示例凭据不能用于部署。显式映射开启后，未知 Site 不回退默认凭据。issuer 在生产使用 HTTPS；多 issuer 不在本期能力内。

分别控制：
- `CONTROL_TENANT_PROVISIONING_ENABLED`：接收租户开户。
- `CONTROL_USER_ACCESS_SYNC_ENABLED`：接收资格同步。
- `CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED`：请求强制校验已同步资格；完成回填与对账后启用。

## 运行模板

`deploy/docker-compose.control.yml` 配合 `docker/backend/Dockerfile.runtime` 提供 API 与 Worker 同版本模板，数据库与 Redis 外置；所需密码、数据库名、SECRET_KEY 通过环境提供。`deploy/nginx/control.conf` 是需填写域名和证书的边缘代理示例，覆盖外来 Forwarded IP 头，后端仅暴露本机端口。模板尚未执行容器构建或真实部署。

开发/测试中控需同一套 assembly、数据库、Redis 前缀和队列运行 API 与 Worker。不同产品的 Redis/任务队列使用独立数据库或独立前缀，避免互相消费。
