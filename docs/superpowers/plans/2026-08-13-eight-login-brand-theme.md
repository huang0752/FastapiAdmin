# 食品物流八套登录品牌与双 Site 主题 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为中控与三套产品实现 8 枚独立品牌 Logo、两套锁定 Site 全局主题和克制的登录页动态品牌效果。

**Architecture:** 在 products 与 control 中分别建立同构的 `siteBrandTheme` 单一解析层，以 Assembly 与标准 Host/Site code 输出 Logo 资产、favicon、主题 token 和动效类型。登录页与登录后布局只消费该解析结果；Site 主题锁定，用户仅能切换亮暗模式。动效使用 SVG/CSS 并遵守 reduced-motion，不增加运行时依赖。

**Tech Stack:** Vue 3、TypeScript、Pinia、Vite、Vitest、SCSS、SVG/CSS Animation、Python/Pillow 或 ImageMagick 静态资产校验。

---

### Task 1: 锁定八套品牌与 Site 主题契约

**Files:**
- Create: `frontend/web/src/config/brand/siteBrandTheme.ts`
- Create: `frontend/web/src/__tests__/site-brand-theme.spec.ts`
- Modify: `frontend/web/src/config/assembly/foodLogiBrand.ts`

- [ ] **Step 1: 写 products 失败测试**

测试须遍历 `food-traceability/agricultural-delivery/cold-chain-vehicle × data360/znceedi`，断言每个组合返回唯一 `logo`、`favicon`、`motion`，并断言 data360/znceedi 分别锁定为 `data360/ocean` 与 `znceedi/energy`。

```ts
expect(resolveSiteBrandTheme("food-traceability", "trace.znceedi.org.cn", "/web/")).toMatchObject({
  site: "znceedi",
  system: "trace",
  preset: "energy",
  logo: "/web/brand/logos/znceedi-trace.png",
  favicon: "/web/brand/favicons/znceedi-trace.png",
  motion: "trace-scan",
});
expect(new Set(matrix.map((item) => item.logo)).size).toBe(6);
```

- [ ] **Step 2: 验证 RED**

Run: `cd products/frontend/web && pnpm vitest run src/__tests__/site-brand-theme.spec.ts`

Expected: FAIL，提示 `siteBrandTheme` 模块不存在。

- [ ] **Step 3: 实现最小解析层**

定义 `SiteCode = "data360" | "znceedi"`、`FoodSystem = "trace" | "agri" | "logistic"` 与 `SiteBrandTheme`。统一规范化 `BASE_URL`，Host 包含 `.znceedi.` 或 Site code 等于 `znceedi` 时选择中能，其余本地 Host 选择华夏。

- [ ] **Step 4: GREEN 并提交 products 契约**

Run: `cd products/frontend/web && pnpm vitest run src/__tests__/site-brand-theme.spec.ts src/__tests__/food-logi-brand.spec.ts`

Expected: PASS。

```bash
git -C products add frontend/web/src/config/brand/siteBrandTheme.ts frontend/web/src/config/assembly/foodLogiBrand.ts frontend/web/src/__tests__/site-brand-theme.spec.ts
git -C products commit -m "feat: 建立食品产品双站点品牌契约"
```

### Task 2: 建立中控双 Site 同构品牌契约

**Files:**
- Create: `control/frontend/web/src/config/brand/siteBrandTheme.ts`
- Modify: `control/frontend/web/src/config/controlProductBrand.ts`
- Create: `control/frontend/web/src/__tests__/site-brand-theme.spec.ts`

- [ ] **Step 1: 写 control 失败测试**

断言 `control.data360.org.cn` 与 `control.znceedi.org.cn` 输出不同 Logo/favicon、相同 `system: "control"`、不同锁定 preset，并断言 localhost 默认 data360。

- [ ] **Step 2: 验证 RED**

Run: `cd control/frontend/web && pnpm vitest run src/__tests__/site-brand-theme.spec.ts`

Expected: FAIL，提示模块不存在。

- [ ] **Step 3: 实现 control 解析层并复用现有标题**

输出 `data360-control.png`、`znceedi-control.png`、各自 favicon、`control-pulse` 动效和 Site token；`CONTROL_PRODUCT_TITLE` 保持“食品物流统一中控”。

- [ ] **Step 4: GREEN 并提交**

Run: `cd control/frontend/web && pnpm vitest run src/__tests__/site-brand-theme.spec.ts src/__tests__/control-product-brand.spec.ts`

Expected: PASS。

```bash
git -C control add frontend/web/src/config/brand/siteBrandTheme.ts frontend/web/src/config/controlProductBrand.ts frontend/web/src/__tests__/site-brand-theme.spec.ts
git -C control commit -m "feat: 建立食品中控双站点品牌契约"
```

### Task 3: 生成与校验 8 套静态品牌资产

**Files:**
- Create: `products/frontend/web/scripts/generateFoodLogiBrandAssets.ts`
- Create: `products/frontend/web/public/brand/logos/{data360,znceedi}-{trace,agri,logistic}.png`
- Create: `products/frontend/web/public/brand/favicons/{data360,znceedi}-{trace,agri,logistic}.png`
- Create: `products/frontend/web/public/brand/motion/{data360,znceedi}-{trace,agri,logistic}.svg`
- Create: `control/frontend/web/public/brand/logos/{data360,znceedi}-control.png`
- Create: `control/frontend/web/public/brand/favicons/{data360,znceedi}-control.png`
- Create: `control/frontend/web/public/brand/motion/{data360,znceedi}-control.svg`
- Create: `products/frontend/web/src/__tests__/brand-assets.spec.ts`

- [ ] **Step 1: 写资产失败测试**

测试读取 24 个权威文件，断言主图为透明 PNG、favicon 为正方形 PNG、SVG 包含 `viewBox` 且不含 `<text>`、外链、脚本或栅格 data URL，并对所有文件计算 SHA-256，确保 8 个组合不重复。

- [ ] **Step 2: 验证 RED**

Run: `cd products/frontend/web && pnpm vitest run src/__tests__/brand-assets.spec.ts`

Expected: FAIL，提示新 favicon/motion 文件缺失或旧 Logo 哈希重复。

- [ ] **Step 3: 生成精密几何资产**

使用可重复的路径参数生成四个母题；Site 变体必须改变连接端点/切角，而非只改颜色。PNG 主图建议 512×512，favicon 输出 64×64；所有 SVG 仅包含本地 path/circle/gradient，文字由页面渲染。

- [ ] **Step 4: GREEN 并人工检查资产清单**

Run: `cd products/frontend/web && pnpm vitest run src/__tests__/brand-assets.spec.ts`

Expected: PASS，24 个组合资产均可解析且 8 个品牌哈希唯一。

- [ ] **Step 5: 精确提交两个仓库资产**

```bash
git -C products add frontend/web/scripts/generateFoodLogiBrandAssets.ts frontend/web/public/brand frontend/web/src/__tests__/brand-assets.spec.ts
git -C products commit -m "feat: 设计食品产品六套品牌图标"
git -C control add frontend/web/public/brand
git -C control commit -m "feat: 设计食品中控双站点图标"
```

### Task 4: 接入 Site 锁定主题并隐藏 preset 入口

**Files:**
- Modify: `products/frontend/web/src/config/themePresets.ts`
- Modify: `products/frontend/web/src/store/modules/setting.store.ts`
- Modify: `products/frontend/web/src/components/views/fa-login/widgets/FaAuthTopBar.vue`
- Modify: `control/frontend/web/src/config/themePresets.ts`
- Modify: `control/frontend/web/src/store/modules/setting.store.ts`
- Modify: `control/frontend/web/src/components/views/fa-login/widgets/FaAuthTopBar.vue`
- Create: `products/frontend/web/src/__tests__/site-theme-lock.spec.ts`
- Create: `control/frontend/web/src/__tests__/site-theme-lock.spec.ts`

- [ ] **Step 1: 写主题锁定失败测试**

断言 data360 和 znceedi 分别应用专属 light/dark token；`setThemePreset()` 在食品产品装配中只能接受当前 Site preset；登录顶栏不存在调色板按钮，但仍存在调用 `themeAnimation` 的亮暗模式按钮。

- [ ] **Step 2: 验证 RED**

Run: `cd products/frontend/web && pnpm vitest run src/__tests__/site-theme-lock.spec.ts`

Run: `cd control/frontend/web && pnpm vitest run src/__tests__/site-theme-lock.spec.ts`

Expected: 两仓测试均 FAIL，现有 preset 仍可自由选择。

- [ ] **Step 3: 增加 Site token 并锁定 store**

data360 基于 Ocean，使用 `#2563EB/#07172E/#38BDF8`；znceedi 新增 `energy` preset，使用 `#078C72/#052E2B/#2DD4BF`。Site 配置加载后重新应用锁定 preset；忽略历史 `themePreset` 缓存，保留 `theme` 的 light/dark 值。

- [ ] **Step 4: 隐藏 preset 入口并保留明暗按钮**

仅在食品产品/control Assembly 中隐藏 palette 与 color-dots；默认 Assembly 保持原功能。设置抽屉内的主题 preset 区域也使用相同特性开关隐藏，防止旁路切换。

- [ ] **Step 5: GREEN 并分别提交**

Run: 两仓各执行 `pnpm vitest run src/__tests__/site-theme-lock.spec.ts`。

Expected: PASS。

```bash
git -C products add frontend/web/src/config/themePresets.ts frontend/web/src/store/modules/setting.store.ts frontend/web/src/components/views/fa-login/widgets/FaAuthTopBar.vue frontend/web/src/__tests__/site-theme-lock.spec.ts
git -C products commit -m "feat: 锁定食品产品站点主题"
git -C control add frontend/web/src/config/themePresets.ts frontend/web/src/store/modules/setting.store.ts frontend/web/src/components/views/fa-login/widgets/FaAuthTopBar.vue frontend/web/src/__tests__/site-theme-lock.spec.ts
git -C control commit -m "feat: 锁定食品中控站点主题"
```

### Task 5: 接入登录页克制动效与降级规则

**Files:**
- Create: `products/frontend/web/src/components/brand/FaSiteBrandMotion.vue`
- Create: `products/frontend/web/src/styles/brand/site-brand-motion.scss`
- Modify: `products/frontend/web/src/components/views/fa-login/backdrops/FaLoginLeftView.vue`
- Create: `control/frontend/web/src/components/brand/FaSiteBrandMotion.vue`
- Create: `control/frontend/web/src/styles/brand/site-brand-motion.scss`
- Modify: `control/frontend/web/src/components/views/fa-login/backdrops/FaLoginLeftView.vue`
- Create: `products/frontend/web/src/__tests__/site-brand-motion.spec.ts`
- Create: `control/frontend/web/src/__tests__/site-brand-motion.spec.ts`

- [ ] **Step 1: 写动效组件失败测试**

断言组件根据 `motion` 输出 `control-pulse/trace-scan/agri-route/logistic-cold` 类名，动画时长大于 6 秒，并存在 `prefers-reduced-motion: reduce`、`visibilitychange` 暂停与移动端无视差规则。

- [ ] **Step 2: 验证 RED**

Run: 两仓分别执行对应 `site-brand-motion.spec.ts`，Expected: FAIL，组件不存在。

- [ ] **Step 3: 实现共享结构的 SVG/CSS 动效组件**

组件只接收 `brand` 配置，不自行解析 Host。首次组装 600ms；循环 6–10 秒；不可见时添加 `is-paused`；reduced-motion 下禁用 animation/transform；背景元素使用 `pointer-events: none` 与 `aria-hidden="true"`。

- [ ] **Step 4: 接入登录左视图并移除通用插画依赖**

品牌组件占据现有红框主视觉区域，登录输入面板 DOM 与交互不变；移动端继续隐藏大背景，仅保留静态 Logo。

- [ ] **Step 5: GREEN、Lint 并提交**

Run: 两仓分别执行动效测试、`pnpm exec eslint`、`pnpm exec prettier --check`。

Expected: 全部 PASS。

### Task 6: 统一 favicon、布局 Logo 与用户表面

**Files:**
- Modify: `products/frontend/web/index.html`
- Modify: `products/frontend/web/src/store/modules/config.store.ts`
- Modify: `products/frontend/web/src/components/layouts/fa-header-bar/widgets/FaUserMenu.vue`
- Modify: `products/frontend/web/src/views/fastlink/current/profile.vue`
- Modify: `control/frontend/web/index.html`
- Modify: `control/frontend/web/src/hooks/core/useSiteConfig.ts`
- Modify: `control/frontend/web/src/components/layouts/fa-header-bar/widgets/FaUserMenu.vue`
- Modify: `control/frontend/web/src/views/fastlink/current/profile.vue`
- Modify: `tests/test_brand_avatar_surfaces.py`

- [ ] **Step 1: 扩展失败契约**

根契约测试断言 favicon 使用 `brand/favicons` 而非大图；登录、侧栏、顶栏、用户菜单和个人中心均消费同一解析结果；食品装配隐藏头像上传，default Assembly 保留。

- [ ] **Step 2: 验证 RED**

Run: `cd /Users/chou/code/food‑logi && pytest -q tests/test_brand_avatar_surfaces.py`

Expected: FAIL，favicon 仍指向 logos 或部分页面未消费统一解析层。

- [ ] **Step 3: 统一品牌消费链路**

移除页面内重复 Host 判断；首屏使用 Host 推断，Site API 返回后由 config store 校正；所有权威品牌表面均使用解析器输出，不读取租户 Logo/用户 avatar/FastapiAdmin 默认资产。

- [ ] **Step 4: GREEN 并提交相关文件**

Run: 根契约测试与两仓品牌单测，Expected: PASS。

### Task 7: 四套构建与非浏览器验收

**Files:**
- Modify: `products/frontend/web/scripts/verifyBuildOutput.ts`
- Create: `control/frontend/web/scripts/verifyBrandBuildOutput.ts`
- Modify: `products/docs/food-logi/branding.md`

- [ ] **Step 1: 扩展产物扫描失败测试**

扫描器必须根据 Assembly 断言产物只引用当前系统的两 Site 资产，包含 favicon、主题 token、reduced-motion CSS，且不包含旧 `@imgs/user/avatar.webp`、FastapiAdmin Logo 或其它产品主视觉入口。

- [ ] **Step 2: 验证 RED**

Run: products 现有 verifier 单测与 control 新 verifier 单测。

Expected: FAIL，扫描器尚无新品牌规则。

- [ ] **Step 3: 实现扫描规则并完成静态验证**

Run:

```bash
cd products/frontend/web
pnpm vitest run
pnpm type-check
pnpm build:trace
pnpm build:agri
pnpm build:logistic
pnpm exec eslint .
pnpm exec prettier --check .

cd ../../../control/frontend/web
pnpm vitest run
pnpm type-check
pnpm build
pnpm exec eslint .
pnpm exec prettier --check .
```

Expected: 所有单测、类型检查与四套构建成功；四个产物扫描器通过。明确不启动浏览器、不运行 Playwright、不做人工浏览器验收。

- [ ] **Step 4: 最终工作树与差异检查**

Run:

```bash
git -C products diff --check
git -C control diff --check
git -C products status --short
git -C control status --short
```

Expected: diff-check 无输出；仅保留已知用户现场或本计划明确文件。

- [ ] **Step 5: 提交验证与文档**

```bash
git -C products add frontend/web/scripts/verifyBuildOutput.ts docs/food-logi/branding.md
git -C products commit -m "test: 完成食品产品品牌构建验收"
git -C control add frontend/web/scripts/verifyBrandBuildOutput.ts
git -C control commit -m "test: 完成食品中控品牌构建验收"
```

## 完成定义

- 8 枚 Logo 在造型与哈希上均唯一，并保持四个产品家族关系；
- 两个 Site 的 light/dark 主题从登录前延续到后台；
- preset 选择入口全部隐藏，亮暗切换保留；
- 动效满足 600ms 首次组装、6–10 秒克制循环、reduced-motion 与页面不可见暂停；
- favicon、侧栏、顶栏、用户菜单与个人中心使用正确系统/Site Logo；
- products 三构建与 control 构建、单测、类型、Lint、格式化、产物扫描全部通过；
- 不以浏览器测试作为完成证据，也不声称已完成浏览器视觉验收。
