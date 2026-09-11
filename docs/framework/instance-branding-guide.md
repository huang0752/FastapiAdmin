# 实例品牌开发指南

本指南适用于从小柿 SaaS 框架创建产品实例。小柿是缺省品牌，实例使用自己的名称与图片；通常无需逐页修改组件。

## 1. 配置覆盖顺序

通用装配的配置按以下顺序合并，后者覆盖同名字段：

1. 前端环境变量和内置资源提供缺省值。
2. 系统初始化配置提供系统级配置。
3. 当前请求 Host 对应的 Site 提供登录前站点品牌。
4. 登录后加载有权访问的当前租户配置，提供租户品牌覆盖。

并非所有空值都表示“继承”：前端忽略非字符串值，但空字符串会进入配置合并；Logo 组件在地址为空或加载失败时回退到内置图。应分别验证登录前和登录后的效果，不要只修改环境变量后就认为站点数据库配置也变了。

历史食品产品装配还带有专用品牌映射和租户字段限制，见 `frontend/web/src/config/brand/siteBrandTheme.ts` 与 `frontend/web/src/config/assembly/foodLogiBrand.ts`。新产品不要借用食品装配编码，否则可能触发该映射；采用自己的装配配置。

## 2. 新实例的最短操作流程

以下文件路径均相对于产品实例仓库根目录。

### 设置实例默认名称和图标

开发环境在 `frontend/web/.env.development.local` 中配置，生产环境在 `frontend/web/.env.production.local` 或构建环境中配置：

```dotenv
VITE_APP_TITLE=我的产品
VITE_BRAND_FAVICON=/web/brand/my-product/favicon.png
```

将图片放入：

```text
frontend/web/public/brand/my-product/logo.png
frontend/web/public/brand/my-product/favicon.png
```

默认部署前缀为 `/web/`，因此上述资源 URL 包含 `/web/`。若实例修改部署前缀，品牌 URL 也应匹配。也可配置浏览器能直接访问的 HTTPS 图片地址。

**Logo 当前没有单独的通用 `VITE_BRAND_LOGO` 环境变量。** 在 Site 的 `logo_url` 中填写 `/web/brand/my-product/logo.png`；若希望资源缺失时也不出现小柿图标，再将实例中的 `frontend/web/public/logo.png` 替换为自己的默认 Logo。此文件是 Logo 组件的最终回退资源。

后端接口文档名称独立配置，在实例的 `backend/env/.env.dev` / `.env.prod` 设置：

```dotenv
TITLE="我的产品接口文档"
```

前端环境变量在启动或构建时读取：开发服务需要重启，生产环境需要重新构建和发布静态文件。后端 TITLE 修改后重启对应后端服务。

### 设置站点品牌

使用有站点管理权限的账号，进入“平台管理 → 站点管理”，编辑实例对应站点：

- 基础信息：设置站点名称。
- 域名配置：登记实际访问域名，并指定一个主域名。
- 品牌配置：填写 Logo、favicon、登录背景及需要的页脚链接。

常用字段：

| 字段 | 用途 | 示例 |
| --- | --- | --- |
| `name` | 站点显示名称 | 我的产品 |
| `logo_url` | 登录页和导航 Logo | /web/brand/my-product/logo.png |
| `favicon` | 浏览器图标 | /web/brand/my-product/favicon.png |
| `login_bg` | 登录背景 | /web/brand/my-product/login-bg.jpg |
| `copyright` | 版权展示文案 | 实例自己的文案 |
| `keep_record` | 备案展示 | 按实际备案填写 |
| `help_doc`、`privacy`、`clause` | 帮助、隐私与条款地址 | 实例自己的链接 |

后端依据请求 Host 解析 Site。多 Site 部署应分别登记域名；反向代理需要正确传递项目使用的 Host 信息。中控和产品使用独立数据库时，分别维护各自的站点品牌。

### 检查租户覆盖

站点品牌决定登录前外观，登录后还可能受到租户品牌配置覆盖。若登录后名称或 Logo 变化，检查该租户的品牌字段与当前产品装配规则。`name` / `logo_url` 在前端还有 `tenant_name` / `tenant_logo` 别名，避免将别名误认为两套独立品牌。

已有数据库应通过管理界面或经过审核的数据迁移更新。修改 `backend/app/scripts/data/platform_site.json`、`platform_tenant.json` 等种子文件不会自动改写现有数据库；不要为换品牌重建数据库。

## 3. 实例验收

1. 未登录打开实际域名，检查登录名称、Logo、背景和浏览器图标。
2. 登录平台管理员与普通租户用户，检查顶部、侧栏和浏览器标题。
3. 多 Site 切换域名，确认各自品牌；退出登录后确认没有残留上一租户品牌。
4. 直接打开 Logo/favicon 地址，确认不是 404、403 或登录页；自定义 Logo 加载失败会显示默认 Logo。
5. 修改数据库配置后刷新页面重新加载配置；若仍旧，排查浏览器配置缓存、图片缓存和是否访问了正确实例。
6. 检查实例自有的欢迎语、邮件模板与产品 README。这些内容不会全部随 `VITE_APP_TITLE` 自动替换。

## 4. 开发边界与代码入口

不要为换品牌批量替换数据库名、Redis 前缀、权限码、API 路径、组件名或迁移历史。保留 LICENSE 与原始作者署名。实例品牌资源和产品文案由实例维护，通用组件继续读取配置。

排查入口：

- `frontend/web/src/store/modules/config.store.ts`：system → site → tenant 合并；登录态使用 `/platform/tenant/current/brand-config`，仅取当前会话租户品牌，不使用平台管理的租户 ID 接口。
- `frontend/web/src/components/base/fa-logo/index.vue`：Logo 地址与错误回退。
- `frontend/web/src/hooks/core/useSiteConfig.ts`：标题和 favicon 同步。
- `frontend/web/src/config/assembly/default.ts`：默认名称与装配信息。
- `frontend/web/src/views/module_platform/site/index.vue`：站点配置界面。

修改品牌相关逻辑时，可运行定向检查：

```bash
cd frontend/web
pnpm exec vitest run src/__tests__/site-public-config.spec.ts src/__tests__/food-logi-brand.spec.ts
pnpm type-check
```

仅改本文档无需启动服务或运行浏览器测试；创建实例时按上述验收清单检查实际部署结果。
