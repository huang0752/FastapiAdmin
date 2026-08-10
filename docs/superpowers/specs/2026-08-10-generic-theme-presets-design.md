# FastapiAdmin 通用主题预设设计

## 1. 目标

在保留现有浅色、深色、跟随系统、主题色、菜单布局和菜单风格能力的前提下，增加一组有名称、可预览、可一键选择的通用主题预设。

主题预设属于现有主题引擎的附加配置层，不创建第二套主题引擎，不依赖具体产品、Site 品牌或 Package 套餐，也不改变权限、菜单和业务数据。

## 2. 方案选择

评估过三种方案：

1. 只把现有主色圆点改成有名称的卡片。改动最小，但无法表达菜单配色、语义色和图表色板，扩展空间不足。
2. 在现有主题系统上增加通用预设层。复用现有 CSS 变量、Pinia 和设置面板，能够组合主色、菜单、语义色和图表色板，同时保持用户可继续单独调整各项设置。
3. 新建完整皮肤引擎。可以覆盖页面、卡片、表格和排版，但会与现有 Light/Dark/System 形成两套状态系统，风险和维护成本过高。

采用方案 2。

## 3. 范围

第一版预设覆盖：

- Element Plus 主色及其明暗变体；
- success、warning、danger、error、info 语义色及其明暗变体；
- 侧栏菜单背景、文字、图标、标题和选中态；
- 通用图表色板 `--fa-chart-1` 至 `--fa-chart-8`；
- Light 和 Dark 两套模式 token。

第一版不覆盖：

- 页面背景、卡片、边框、表格、字号和间距等大面积皮肤；
- Site、Package 或产品代码到主题的自动映射；
- 后端主题配置和跨设备云同步；
- 权限、路由、菜单、品牌资料或业务数据。

## 4. 通用预设

内置以下预设：

- `default`：FastapiAdmin 默认外观；
- `ocean`：海洋蓝；
- `forest`：森林绿；
- `violet`：优雅紫；
- `sunset`：活力橙。

注册表保存预设编码、国际化名称、Light/Dark 主色、语义色、推荐菜单风格、菜单 token 和 8 色图表色板。色值只能在注册表中定义，SCSS 变量层必须由测试校验与注册表一致。

## 5. 状态模型

### 5.1 单一主题状态源

现有设置 Store 同时存在：

- `systemThemeType`、`systemThemeMode`、`systemThemeColor`；
- `theme`、`themeColor`。

前一组已经被设置面板、登录页和启动初始化使用，本次将其确定为主题状态真源。水印等仍读取后一组的消费者改为读取 `systemThemeType/systemThemeColor`。

兼容期保留 `theme/themeColor` 的既有存储键，但不再作为主题应用入口；初始化时只在新状态没有有效历史值时读取旧值迁移，避免已有用户偏好丢失。不得保留两个相互写入的主题 watcher。

### 5.2 预设状态

Store 增加运行时字段 `themePreset`，默认值为 `default`。该字段不进入全局 `setting` 持久化，避免一个站点、租户或用户的选择污染另一个上下文。

用户选择只写版本化隔离键：

```text
sys-v{version}-theme-preset-{siteCode}-{tenantId}-{userId}
```

三个上下文任一缺失时不落盘，但允许当前页面临时应用。

## 6. 应用与恢复规则

### 6.1 主动选择预设

用户点击预设时：

1. 设置 `data-theme-preset`；
2. 应用当前明暗模式对应的主色和语义色；
3. 应用推荐菜单风格；
4. 更新运行时 `themePreset`；
5. 在上下文完整时写隔离存储键；
6. 刷新依赖主题 token 的图表视图。

### 6.2 默认主题

选择或恢复已保存的 `default` 必须走同一条恢复函数，完整执行：

- 删除 `data-theme-preset`；
- 删除语义色内联覆盖；
- 恢复 FastapiAdmin 出厂主色；
- 恢复出厂菜单风格；
- 保留 Light/Dark/System 模式不变。

不得出现“主动选择 default 能恢复、冷启动读取 default 不能恢复”的分叉。

### 6.3 保护用户自定义

系统需要区分“上一个预设自动应用的值”和“用户手动修改的值”：

- 记录最近一次由预设自动应用的主色和菜单风格；
- 上下文重解析时，只有当前值仍等于最近自动值或出厂值，才允许新预设替换；
- 用户手动修改后保留其选择；
- 用户主动点击另一个预设时强制应用新预设。

该规则能够正确处理推荐 dark 菜单的预设切换到推荐 design 菜单的预设，同时不会覆盖真正的用户自定义。

## 7. 组件与文件边界

- `src/config/themePresets.ts`：通用主题注册表、类型和查询函数；
- `src/hooks/core/useThemePreset.ts`：上下文、隔离存储、应用、恢复和模式监听；
- `src/components/layouts/fa-settings-panel/widgets/FaThemePresetSettings.vue`：预设选择区；
- `src/components/layouts/fa-settings-panel/widgets/FaThemePresetPreview.vue`：真实 HTML/CSS 缩略预览；
- `src/styles/theme-presets/index.scss`：图表变量和必要的侧栏作用域覆盖；
- `src/__tests__/theme-preset.spec.ts`：注册表、状态迁移、持久化和视觉约束测试。

设置面板顺序为：主题风格、主题预设、菜单布局、菜单风格、系统主题色。现有单项设置全部保留；用户手动改变主色或菜单风格后，界面显示为“自定义”。

## 8. 初始化时序

1. Pinia 初始化并恢复兼容状态；
2. 现有 `initializeTheme()` 应用 Light/Dark/System 和主色；
3. 应用启动编排加载 Site 配置及登录用户上下文；
4. 主题预设按隔离键解析并应用；
5. 登录、登出和切换租户后重新解析当前上下文。

无 Site、租户或用户上下文时，不猜测其他用户的选择，也不写隔离键。

## 9. 测试与验收

测试必须覆盖：

- 预设注册表编码唯一、Light/Dark token 完整；
- 设置面板只显示通用主题，不包含产品或套餐判断；
- 上下文不完整时不写存储；
- 按 Site、租户、用户隔离；
- `themePreset` 不进入全局 Pinia 持久化；
- 主动选择与冷启动读取 `default` 都完整恢复出厂外观；
- 推荐 dark 预设切换到推荐 design 预设时正确迁移；
- 用户自定义主色和菜单风格在自动重解析时保留；
- Light/Dark/System 切换时预设主色和语义色同步；
- 默认主题不改变 Light/Dark/System 状态；
- 图表色板包含 8 色并具有足够色相分散；
- 关键文字与背景组合达到 WCAG AA 4.5:1；
- SCSS 色值与 TypeScript 注册表同步；
- 现有主题、设置面板和前端完整测试无回归。

完成后执行：

```bash
pnpm test -- src/__tests__/theme-preset.spec.ts
pnpm type-check
pnpm test
pnpm build
```

## 10. 提交边界

功能提交只包含通用主题预设、主题状态收敛、测试和必要文档，不包含 `.understand-anything/` 或其他现有未跟踪内容，不修改 energy-carbon-platform。
