# 中控产品基线

## 仓库定位

本仓库是基于 FastapiAdmin 的独立中控产品，不是 FastapiAdmin 的第二个框架版本。

- FastapiAdmin 继续维护通用认证、用户、租户、角色、菜单、权限和插件底座。
- 本仓库维护应用目录、应用访问资格、系统接入、数据汇总和统一入口等中控产品能力。
- 不需要中控的业务系统继续直接使用 FastapiAdmin，不依赖本仓库。

## 固定框架基线

| 项目 | 值 |
| --- | --- |
| Framework remote | `https://github.com/huang0752/FastapiAdmin.git` |
| Framework tag | `v3.1.6-foundation` |
| Framework commit | `d7f5067cd2b60351a6233979553428f69d8bdf74` |
| Framework SSO Client commit | `b03bf06ba71c4e8048a722557db8266482ec00bf` |
| Database migration head | `20260810_01` |
| Product assembly | `control` |
| Push state | 本地已合入，尚未推送 |

中控首次启动应显式设置：

```dotenv
APP_ASSEMBLY=control
```

数据库、Redis、JWT 密钥、域名和部署产物必须与其他业务系统独立配置。

## 框架升级规则

框架更新不通过重新 fork 或覆盖文件完成。每次升级使用独立分支并记录目标 tag：

```bash
git fetch framework --tags --prune
git switch -c codex/sync-framework-<version>
git merge --no-ff <framework-tag>
```

升级后至少执行：

```bash
cd backend
uv run ruff check app tests
uv run pytest tests -q

cd ../frontend/web
pnpm type-check
pnpm test
pnpm build
```

验证通过后再合并到产品主分支，并同步更新本文件中的 tag 和 commit。

## 权限与数据边界

- 中控用户管理复用框架的用户、租户和本地 RBAC。
- 中控只管理用户是否可进入某个应用，以及进入哪个目标租户。
- 各业务系统继续管理自己的角色、菜单、按钮、API 和数据范围权限。
- 中控汇总数据通过版本化服务端 API 或异步快照获取，浏览器不直接拼接各系统数据库。

## 当前能力状态

当前产品仓库已从固定框架基线 `d7f5067c` 合入截至 `b03bf06b` 的通用 SSO Client：包含联邦身份模型、影子用户 JIT 开户、本地密码限制、一次性启动码兑换、前端回调页与默认关闭的 Assembly 开关。数据库迁移头为 `20260810_01`。

`application_portal` 与 `sso_client` 仍默认关闭；中控 Provider、应用注册、访问资格和启动码签发属于后续产品模块，在 Provider 完成并通过双实例验收前不得仅通过配置开启。业务数据汇总不属于本阶段范围。

以上合入和文档更新当前只存在于本地 `codex/control-sso-foundation` 分支，尚未推送远端。
