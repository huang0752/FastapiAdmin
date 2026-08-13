# 食品物流登录数据品牌海报重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 替换第一版中央大 Logo 登录左屏，为 8 个入口交付四类独立业务数据海报、双 Site 视觉语言和 8 套重新设计的克制 Logo。

**Architecture:** 保留现有 Site/Assembly 解析边界，新增配置化 `SiteBrandIdentity`、场景分派器 `LoginBrandScene`、通用动效边界 `BrandMotionLayer`，以及 control/trace/agri/logistic 四个独立 SVG 场景组件。删除第一版中央徽章展示，不在旧组件上叠加。

**Tech Stack:** Vue 3、TypeScript、Vite、Vitest、SVG、SCSS/CSS Animation、现有 Pinia Site 配置。

---

### Task 1: 用失败契约锁定第二版范围

**Files:**
- Create: `products/frontend/web/src/__tests__/login-brand-poster-v2.spec.ts`
- Create: `control/frontend/web/src/__tests__/login-brand-poster-v2.spec.ts`

- [ ] 断言第一版中央徽章容器、环形光环和通用底部文案不再存在。
- [ ] 断言四个系统分别映射到 `ControlBrandScene`、`TraceBrandScene`、`AgriBrandScene`、`LogisticBrandScene`。
- [ ] 断言 8 个身份配置均包含主张、说明、三个关键词和 2–3 个抽象状态。
- [ ] 运行两仓定向 Vitest，确认因 v2 组件不存在而 RED。
- [ ] 分别提交 RED 测试，提交信息：`test: 锁定登录品牌海报重设计边界`。

### Task 2: 重做 8 套 Logo 与 favicon

**Files:**
- Modify: `products/frontend/web/scripts/generateFoodLogiBrandAssets.ts`
- Modify: `products/frontend/web/public/brand/logos/*.png`
- Modify: `products/frontend/web/public/brand/favicons/*.png`
- Modify: `control/frontend/web/public/brand/logos/*.png`
- Modify: `control/frontend/web/public/brand/favicons/*.png`
- Modify: `products/frontend/web/src/__tests__/brand-assets.spec.ts`

- [ ] 先扩展资产测试：拒绝圆形黑底、要求透明角、主图和 favicon 使用不同路径简化结果、8 个哈希唯一。
- [ ] 运行测试确认旧资产 RED。
- [ ] 重写确定性生成器：四个产品线面母题、两个 Site 不同节点与切角、无外圈光环。
- [ ] 生成 512 主图和 64 favicon；重复生成并比对 SHA-256。
- [ ] 运行资产测试、Prettier、ESLint、diff-check。
- [ ] products 与 control 精确提交，不纳入现有无关改动。

### Task 3: 建立配置化身份与场景分派

**Files:**
- Create: `products/frontend/web/src/config/brand/loginBrandPoster.ts`
- Create: `control/frontend/web/src/config/brand/loginBrandPoster.ts`
- Create: `products/frontend/web/src/components/brand/LoginBrandScene.vue`
- Create: `control/frontend/web/src/components/brand/LoginBrandScene.vue`

- [ ] 测试先定义 `LoginBrandPoster` 类型：identity、headline、description、keywords、statuses、scene、siteTone。
- [ ] RED 证明 6 个 products 组合与 2 个 control 组合缺失。
- [ ] 实现 8 套配置；状态只允许抽象状态，不允许订单量、交易量等伪实时数字。
- [ ] 实现场景分派器，未知食品 Assembly fail-closed，不回退通用插画。
- [ ] 运行定向 Vitest 与 type-check 后分别提交。

### Task 4: 实现四个独立业务数据场景

**Files:**
- Create: `products/frontend/web/src/components/brand/scenes/TraceBrandScene.vue`
- Create: `products/frontend/web/src/components/brand/scenes/AgriBrandScene.vue`
- Create: `products/frontend/web/src/components/brand/scenes/LogisticBrandScene.vue`
- Create: `control/frontend/web/src/components/brand/scenes/ControlBrandScene.vue`
- Create: `products/frontend/web/src/components/brand/BrandMotionLayer.vue`
- Create: `control/frontend/web/src/components/brand/BrandMotionLayer.vue`
- Create: `products/frontend/web/src/styles/brand/login-brand-poster.scss`
- Create: `control/frontend/web/src/styles/brand/login-brand-poster.scss`

- [ ] 先写源码/组件契约，断言四组件拥有互不相同的业务 SVG 标记与动画类。
- [ ] RED 后分别实现拓扑、批次链、田块配送、温度车辆四场景。
- [ ] data360 与 znceedi 通过 CSS token 改变构图节点、切角和节奏，而非只换色。
- [ ] 实现 1.2 秒分层进入、8–12 秒业务路径高亮。
- [ ] 实现 reduced-motion、visibility pause、移动端无视差。
- [ ] Vitest、type-check、精确 Lint/Prettier 后分别提交。

### Task 5: 完整替换登录左屏

**Files:**
- Modify: `products/frontend/web/src/components/views/fa-login/backdrops/FaLoginLeftView.vue`
- Modify: `control/frontend/web/src/components/views/fa-login/backdrops/FaLoginLeftView.vue`
- Delete: 第一版仅用于中央徽章展示且无其它消费者的组件与样式

- [ ] 写失败测试，断言左屏包含品牌层、主叙事层、状态层、空间层和文案层。
- [ ] 断言不存在通用“兼具设计美学与高效开发”及框架宣传说明。
- [ ] RED 后接入 `LoginBrandScene`，Logo 只在顶部识别区出现。
- [ ] 保持右侧登录表单 DOM、验证和认证行为不变。
- [ ] 运行登录、验证码、品牌、密码重置相关回归与 type-check。
- [ ] 分别精确提交。

### Task 6: 双 Site light/dark 材质与主题入口复核

**Files:**
- Modify: `products/frontend/web/src/styles/brand/login-brand-poster.scss`
- Modify: `control/frontend/web/src/styles/brand/login-brand-poster.scss`
- Modify: 两仓 `site-theme-lock.spec.ts`

- [ ] 扩展测试，断言 data360 light/dark 为冷蓝纸面/深海蓝，znceedi 为青灰纸面/深翡翠。
- [ ] 断言 preset 与调色板入口隐藏、亮暗按钮保留。
- [ ] RED 后实现纸面噪点、局部光域、网格/等高线 token，禁止纯白/纯黑大底。
- [ ] 运行主题定向测试并提交。

### Task 7: 非浏览器最终验收

**Files:**
- Modify: `products/frontend/web/scripts/verifyBuildOutput.ts`
- Modify: `control/frontend/web/scripts/verifyBrandBuildOutput.ts`
- Modify: `products/docs/food-logi/branding.md`

- [ ] 扩展扫描器：四个产物必须包含正确场景标记、系统文案、两 Site 资产与 reduced-motion CSS。
- [ ] 扫描器必须拒绝第一版中央徽章结构、通用框架文案和其它系统场景主标记。
- [ ] 运行两仓全量 Vitest 与 type-check。
- [ ] 串行运行 control、trace、agri、logistic 四套正式构建和产物扫描。
- [ ] 对计划相关源码运行 ESLint、Prettier；运行两仓 diff-check。
- [ ] 明确记录未运行浏览器/Playwright，且不声称完成人工视觉验收。
- [ ] 分别提交构建验收和品牌文档。

## 完成定义

- 8 套 Logo 全部替换第一版，并通过透明度、唯一性和 favicon 简化检查。
- 8 个左屏均为完整五层品牌海报。
- 四系统使用独立场景，不复用一张主图换标题。
- 两 Site 除色彩外还在节点、切角、构图和节奏上不同。
- 右侧认证行为不变，主题锁定和亮暗切换符合既定规则。
- 四套构建和非浏览器自动化验收全部通过。
