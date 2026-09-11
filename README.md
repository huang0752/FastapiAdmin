# 小柿 SaaS

<img src="frontend/web/public/logo.png" alt="小柿 SaaS" width="112" />

基于 FastAPI、Vue 3 和 TypeScript 的多租户应用框架。支持独立 SaaS 部署，也支持一个中控连接多个独立产品实例。默认品牌为“小柿 SaaS”，产品与租户可覆盖名称、Logo、登录页和相关链接。

## 能力与边界

- 用户、角色、菜单和接口权限，租户数据隔离。
- 站点与租户品牌配置、套餐与插件装配。
- 中控企业开户、产品开通、员工应用授权、单点登录与撤权同步。
- 产品独立数据库、独立登录凭证，本地角色由各产品管理。
- 任务、审计、通知和可选扩展模块。

应用授权决定能否进入产品；产品本地角色决定可以使用哪些功能。中控授权不会将员工自动变成产品超级管理员。默认联邦产品采用人工角色分配。

当前跨实例联动覆盖身份、开户及访问授权；订单、库存等业务数据同步需要在具体产品中实现。

## 项目结构

```text
backend/
  app/api/v1/       接口、服务与业务模块
  app/assemblies/   能力装配配置
  app/scripts/      初始化和种子数据
  tests/           后端定向测试
frontend/web/
  src/             管理界面、路由、状态与品牌配置
  public/          默认品牌及静态资源
docs/              架构、部署和验收记录
```

## 本地开发

准备 Python 3.12+、Node.js 20+、uv、pnpm，以及 PostgreSQL 和 Redis。先配置连接信息和独立密钥，再启动服务。

```bash
cd backend
cp env/.env.dev.example env/.env.dev
uv sync
# 编辑 env/.env.dev：数据库、Redis、密钥与装配配置
uv run alembic upgrade head
uv run python main.py run --env=dev
```

新数据库需按项目初始化流程加载装配对应的菜单、套餐和基础数据。不要把已有环境的连接信息或初始化数据直接用于生产。

```bash
cd frontend/web
pnpm install
# 按 .env.example 配置 .env.development.local，连接当前后端
pnpm dev
```

常用定向检查：

```bash
# backend/
uv run pytest tests/test_role_governance_visibility.py -q
uv run ruff check app tests

# frontend/web/
pnpm exec vue-tsc --noEmit
pnpm exec vitest run src/__tests__/menu-permission-selection.spec.ts
```

## 部署方式

- **独立 SaaS**：选择合适的业务装配，自行管理用户、租户和权限。
- **中控 + 产品**：中控使用 `control` 装配；产品使用 `federated-saas` 或自定义装配，每个产品配置唯一 `application_code`。
- 中控与产品分别配置数据库、Redis 命名空间、密钥和服务地址。登记产品回调、开户与授权同步地址后再开通企业。
- 多个实例可以共用代码；不要共用产品登录密钥或把数据库当作跨产品通信接口。

装配入口为后端 `APP_ASSEMBLY` / `APP_ASSEMBLY_FILE` 和前端 `VITE_APP_ASSEMBLY`。具体参数见对应环境模板与 [文档目录](docs/)。

## 实例品牌

创建产品实例请按 [实例品牌开发指南](docs/framework/instance-branding-guide.md) 操作，包含配置示例、覆盖顺序、多 Site、存量数据库和验收步骤。

- `VITE_APP_TITLE`：实例默认标题。
- `VITE_BRAND_FAVICON`：浏览器图标；框架默认 `/web/logo.png`。
- 站点及租户品牌配置：覆盖名称、Logo、登录背景、页脚和帮助链接。
- 未配置的官网、社区链接默认不展示。无需修改框架名称来定制实例。

不在通用页面注入口号或行业文案。业务品牌和业务模块由具体实例维护。

## 开发约定

权限码沿用模块命名空间；业务数据保持租户隔离；接口授权与前端菜单、按钮保持一致。密钥、日志、数据库备份及私有文件不提交到仓库。

## 许可

许可证及原始版权声明见 [LICENSE](LICENSE)。品牌替换不改变现有开源许可，源码中的历史署名与兼容标识保留。


## 普通员工的最小访问能力

有效登录且具有产品访问资格的员工，无业务角色时也可进入个人工作区（`/workspace/personal`），查看本人资料、刷新可用功能并退出登录；本地账号可修改自己的姓名，统一登录身份资料由中控维护。

这层基础访问不授予用户列表、租户管理或业务数据权限。管理员分配产品角色后，业务菜单按角色开放；撤销业务角色后回到个人工作区。账号禁用、套餐/租户停用和产品访问资格撤销仍按后端规则拒绝访问。`manual` 指业务权限由管理员配置，不表示禁止基础登录。

### 租户共享 AI

右上角头像 → 配置中心 → **租户 AI**：租户管理员配置一次，同租户已获业务授权的成员共用。支持模型切换、连接检测、功能绑定和加密密钥。个人聊天按“个人模型 → 租户模型 → 部署默认”选择；中控与产品各自管理配置。

业务实例接入方式、管理权限、Redis 持久化及密钥备份见 [租户共享 AI 配置](docs/framework/tenant-ai-config.md)。
