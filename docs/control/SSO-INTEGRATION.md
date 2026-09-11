# 中控 SSO 双实例接入与验收

本文用于本地验证一套 `control` 中控与一套启用 SSO Client 的 FastapiAdmin 目标系统。脚本只操作随机、显式命名的 E2E 数据库和自身记录的进程；不连接或修改日常开发数据库。

## 已验证基线

| 项目 | 验证值 |
| --- | --- |
| 验证日期 | 2026-08-12 |
| Control 框架合并 commit | `05cb5f31`（同步通用软件使用证明能力） |
| Framework commit | `016a4d54` |
| Control Assembly | `control` |
| Target Assembly | `saas-admin`，并显式设置 `CONTROL_SSO_ENABLED=true` |
| Control migration head | `20260812_02` |
| Target migration head | `20260812_01` |
| 数据库命名 | `^fastapiadmin_(control\|target)_e2e_[0-9a-f]{12}$` |
| 已实际执行 | 双随机 PostgreSQL、真实临时 Redis、双后端、双 Vite 前端、API 闭环、真实浏览器正向闭环 |
| 浏览器结论 | 首登到等待授权页；目标管理员分配本地角色后，从中控应用中心再次点击可直接进入 Target `/home` |

以上只记录版本和验证边界，不记录运行时生成的 Client Secret。

## 启动前提

- 本机 PostgreSQL、真实 Redis 正常运行；示例使用 PostgreSQL `5432`、Redis `6379`。
- 后端已执行 `uv sync`，前端已执行 `pnpm install`。
- 六个显式端口必须互异。四个应用监听端口必须空闲，PostgreSQL 与外部 Redis 端口必须可达。
- PostgreSQL 用户必须能创建、删除数据库。密码通过 `MIGRATION_TEST_DATABASE_PASSWORD` 提供；不要写入命令行或本文。
- 若 Target 来自独立框架工作树，分别传入 `--target-backend-dir` 和 `--target-frontend-dir`。

先生成同一个 12 位十六进制 run id，再构造两个显式数据库名：

```bash
RUN_ID="$(openssl rand -hex 6)"
CONTROL_DB="fastapiadmin_control_e2e_${RUN_ID}"
TARGET_DB="fastapiadmin_target_e2e_${RUN_ID}"
```

脚本会再次严格校验名称。名称不匹配时，在连接 PostgreSQL 前直接拒绝执行。

## 仅验证数据库准备

`--prepare-only` 会创建双库、迁移、Seed、核对 revision，然后在退出时删除两个精确数据库；不会启动四个 Web 实例。

```bash
scripts/control-sso-smoke.sh --prepare-only \
  --control-backend-port 18101 \
  --control-frontend-port 15181 \
  --target-backend-port 18102 \
  --target-frontend-port 15182 \
  --postgres-port 5432 \
  --redis-port 6379 \
  --control-db "$CONTROL_DB" \
  --target-db "$TARGET_DB"
```

预期输出必须同时包含：

```text
control=<显式数据库名> (20260812_02)
target=<显式数据库名> (20260812_01)
prepare-only 验证通过
```

退出后用只读查询确认数据库已清理：

```sql
SELECT datname
FROM pg_database
WHERE datname IN ('<CONTROL_DB>', '<TARGET_DB>');
```

结果应为 0 行。

## 启动双实例

完整启动命令与 `--prepare-only` 参数相同，只把运行方式改成 `start`：

```bash
scripts/control-sso-smoke.sh start \
  --control-backend-port 18101 \
  --control-frontend-port 15181 \
  --target-backend-port 18102 \
  --target-frontend-port 15182 \
  --postgres-port 5432 \
  --redis-port 6379 \
  --control-db "$CONTROL_DB" \
  --target-db "$TARGET_DB"
```

脚本按以下顺序准备环境：

1. 校验数据库名、六端口、依赖和路径；
2. 创建双库，Control 升级到 `head`，Target 只升级到 `20260812_01`；
3. 分别用 `APP_ASSEMBLY=control`、`APP_ASSEMBLY=saas-admin` 初始化 Seed；
4. 启动 Control 后端并轮询 `/api/v1/common/health`；
5. 使用公开 API 登录平台 `super`，把它加入测试租户，创建临时应用、开通到目标租户 `test` 并授权；
6. 把一次性返回的 Client Secret 仅写入 `chmod 600` 的临时 Target 环境文件；
7. 启动 Target 后端和两套 Vite 前端，每套 Vite 都使用独立 mode、显式端口和 `VITE_API_BASE_URL`；
8. 打印四个本地 URL，在前台等待。

脚本不会打印 Client Secret。`Ctrl-C` 或命令结束后，EXIT 清理只会终止脚本记录的 PID，删除两个已验证的精确 E2E 数据库，并清除临时 env、PID、日志和状态文件。它不使用 `pkill`、进程名匹配、数据库通配符或工作区递归删除。

如果本机没有外部 Redis，可增加 `--managed-redis`。脚本会在显式 `--redis-port` 启动一个真实临时 `redis-server`，并把它的 PID 纳入同一清理范围。

## 给自动化验收执行命令

`--command` 在四个实例全部健康后运行一条命令，并向它暴露以下非敏感环境变量：

- `CONTROL_FRONTEND_URL`
- `CONTROL_API_URL`
- `TARGET_FRONTEND_URL`
- `TARGET_API_URL`
- `CONTROL_SSO_STATE_FILE`（不含 Client Secret）

示例：

```bash
scripts/control-sso-smoke.sh --command 'pnpm exec playwright test tests/control-sso.spec.ts' \
  --control-backend-port 18101 \
  --control-frontend-port 15181 \
  --target-backend-port 18102 \
  --target-frontend-port 15182 \
  --postgres-port 5432 \
  --redis-port 6379 \
  --control-db "$CONTROL_DB" \
  --target-db "$TARGET_DB"
```

命令返回码会成为脚本返回码，随后仍执行相同的窄范围清理。

## 浏览器正向验收

脚本已通过 API 预置应用、租户开通和 `super` 用户授权，因此标准浏览器流程从登录开始：

1. 打开脚本打印的中控前端 URL，使用本地 Seed 的平台 `super` 测试账号登录。
2. 在右上角租户切换中选择测试租户 `test`。必须确认当前租户已变化，不能只看默认平台租户。
3. 打开“应用中心”，应只看到脚本创建的“本地 SSO 验收目标系统”。
4. 点击进入。浏览器应跳到精确回调地址 `http://127.0.0.1:<target-frontend-port>/#/auth/control/callback?code=...`。`code` 必须位于 `#` 后的 Vue 路由 query；错误的 `...?code=...#/auth/control/callback` 不会被回调页接受。
5. 回调页先显示“正在完成统一登录”，随后进入等待页。
6. 等待页必须显示“账号已创建，等待管理员授权”，并说明本地角色或菜单权限尚未分配。

这一步证明的是：中控访问授权、60 秒一次性码、Target Client 兑换、JIT 影子账号、目标租户成员关系和本地会话均已建立。它不代表中控为目标账号分配了业务角色；角色仍归目标系统管理。

## 分配目标本地角色后重登

1. 新开无痕窗口访问 Target，使用测试租户的本地管理员 `test_admin` 登录。
2. 在目标系统“系统管理 / 用户管理”找到刚创建的联邦影子用户。其用户名为系统生成值；以姓名、联邦来源或刚才的登录时间核对身份。
3. 为它分配一个具有首页/菜单权限的目标系统本地角色。不要修改其密码，也不要把它改成本地密码账号。
4. 返回中控，仍保持 `test` 租户上下文，再次从“应用中心”点击同一应用。
5. 新的一次性码应兑换成功，浏览器应进入 Target 首页或该角色允许的菜单，不再停留在等待页。

只刷新旧等待页不足以证明重新签发；必须从中控再次点击，走一遍新的 launch code。

## 2026-08-11 真实浏览器验收记录

本次从空数据库启动完整双实例，Control revision 为 `20260811_03`，Target revision 为 `20260811_01`。浏览器和只读数据库/API 交叉验证结果如下：

1. 使用 Control 本地 Seed 账号登录，切换到测试租户；侧栏只出现租户可用的“用户授权”和“应用中心”。
2. 应用中心显示脚本临时创建的“本地 SSO 验收目标系统”，状态为“已授权”。
3. 首次进入应用完成 launch、exchange 和 JIT 自动开户，Target 到达 `/auth/control/waiting`，页面明确显示“账号已创建，等待管理员授权”。
4. Target 数据库只生成一条联邦身份、一名影子用户和一条测试租户成员关系；初始 `sys_user_roles` 为 0。
5. 使用 Target 租户管理员通过正式用户更新 API 分配本地 `owner` 角色，接口返回“修改用户成功”。影子用户只注册系统生成用户名作为本地登录标识；从 Control 同步来的邮箱、手机号仅作为资料，不抢占 Target 本地密码登录标识。
6. 返回 Control 应用中心再次点击同一应用，浏览器签发并兑换新的 launch code，最终到达 Target `/home`；页面显示系统生成的 `control_...` 当前账号及目标系统菜单，不再进入等待页。

验收过程中未记录或保留 Client Secret、JWT、数据库密码和完整一次性 code。临时端口、数据库和运行目录必须在脚本退出后按本文清理检查确认归零。

## 从零手工配置时的 UI 顺序

不使用脚本的 API 预置时，平台管理员按以下顺序操作：

1. 平台租户上下文下进入“应用管理”，登记一个部署实例；`base_url` 指向 Target 前端根地址，`callback_url` 精确到 `/#/auth/control/callback`。
2. 只在创建或“重置密钥”弹窗中接收一次 Client Secret，立即写入 Target 的安全配置；不要截图、复制到工单或日志。
3. 在“租户应用开通”为中控租户选择该应用，并填写 Target 已存在的 `tenant.code`。
4. 切换到对应中控租户，进入“用户应用授权”，选择已开通应用，再逐个授权租户成员。
5. Target 后端配置 `CONTROL_SSO_ISSUER=<Control API 根地址>`、Client ID、Client Secret 和 `CONTROL_SSO_ENABLED=true` 后重启。
6. 用户从中控“应用中心”发起登录；Target 的角色和数据权限仍由 Target 管理员配置。

## 异常场景与预期结果

每个失败场景都应使用一个尚未兑换的新用户或新的可识别测试账号，并在 Target 以只读查询确认没有意外新增 `sys_federated_identity`、`sys_user`、`platform_user_tenant`、`sys_user_roles` 或 Redis 会话。不要用已有影子用户判断“未开户”。

| 场景 | 操作 | HTTP 结果 | UI 结果 | 数据结果 |
| --- | --- | --- | --- | --- |
| 撤销用户授权 | 在“用户应用授权”撤销后再点击应用 | Control launch `403`，消息“应用不存在或当前用户无权访问” | 应用从当前用户应用中心消失或点击被拒绝 | 不签发 code，不创建 Target 用户/会话 |
| 停用应用 | 平台管理员在“应用管理”停用后再 launch | Control launch `403` | 应用中心不可进入 | 不签发 code，不创建 Target 用户/会话 |
| 已签发后撤权/停用 | 先取得 callback URL，再撤权或停用，然后打开 callback | Provider exchange `403`；Target exchange 对外统一为 `401` | 提示“统一登录失败，请返回中控台重试”并回到登录页 | code 已进入兑换判定，但 Target 不创建用户/会话 |
| 错误 Client Secret | 用明确错误值重启独立 Target，再用新 code 回调 | Provider exchange `401`“Client 认证失败”；Target exchange `401` | 同上 | ticket 不被错误 Client 消费，Target 不创建用户/会话 |
| code 过期 | 取得 callback URL 后等待超过 60 秒再打开 | Provider exchange `400`“启动码无效、已过期或已使用”；Target exchange `401` | 同上 | 不创建 Target 用户/会话 |
| code 重放 | 正常兑换成功后再次请求相同 callback URL | Provider exchange `400`；Target exchange `401` | 同上 | 不新增第二个身份、用户或会话 |
| 目标租户不存在 | 将开通记录的 `target_tenant_code` 改为 Target 不存在的值，再用新用户 launch | Provider exchange `200`，Target exchange `400`“目标租户不存在或已停用” | 回调显示统一登录失败并回到登录页 | code 被正常兑换，但 Target 不创建用户、成员关系或会话 |

注意：Target 会把 Provider 的 HTTP/校验错误统一包装为 `401 中控启动码兑换失败`；排障时同时看 Control Provider 日志和 Target 日志，不能只看浏览器提示。

## 故障定位

| 现象 | 优先检查 |
| --- | --- |
| 脚本启动前报端口错误 | 六端口是否互异；四个监听端口是否已被占用；PostgreSQL/Redis 端口是否可达 |
| Control migration 正确，Target 意外包含 Control Provider 表 | Target 必须执行 `alembic upgrade 20260812_01`，并使用 `APP_ASSEMBLY=saas-admin` |
| 应用中心为空 | 当前是否已切到 `test`；应用、开通、用户授权、租户、站点是否均启用 |
| callback 直接到 404 | Target 是否启用 `CONTROL_SSO_ENABLED=true`；前端 Assembly 配置接口是否返回 `authFeatures.controlSso=true` |
| callback 报兑换失败 | 核对 issuer 是 Control API 根地址（包含 `/api/v1`）、Client ID/Secret 是否属于同一个应用、code 是否过期/重放 |
| 报站点不一致 | Control 签发的 `site_code` 与 Target 当前请求解析到的站点是否一致 |
| 报目标租户不存在 | 开通记录的 `target_tenant_code` 必须精确匹配 Target 的 `platform_tenant.code`，且该租户启用 |
| JIT 后停留等待页 | 这是无本地角色的预期结果；由 Target 管理员分配本地角色后从中控重新 launch |
| 退出后仍有资源 | 查脚本打印的日志；只检查当次精确数据库名和 PID。不得用 `pkill`、通配数据库删除或工作区递归删除补救 |

## 验收证据清单

- 四个本地 URL 和六个端口；
- 两个随机数据库名及 migration revision（不含密码）；
- Control 当前租户为 `test` 的截图；
- 应用中心、callback 处理中、等待授权页截图；
- Target 本地角色分配前后两次 launch 的页面结果；
- 异常场景的 HTTP 状态、业务消息和 Target 只读计数；
- 脚本退出后的 0 行数据库清理查询及无遗留 PID/临时目录检查；
- 不包含 Client Secret、JWT、数据库密码或完整一次性 code。
