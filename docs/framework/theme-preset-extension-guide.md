# 通用主题预设扩展指南

本文面向后续维护者和 Agent，说明在 FastapiAdmin 框架中新增、修改或删除全局主题预设时应遵循的边界、文件关系、实施步骤和验收要求。

## 文档位置与使用方式

主题规范以本 Markdown 文件为准，并由仓库根目录 `AGENTS.md` 强制指向。不要把 DOCX、聊天记录、截图或个人笔记作为唯一规范来源：这些载体不利于代码检索、差异评审和版本追踪。需要对外汇报时可以另行导出 DOCX，但必须同步维护本文件。

开始主题工作前，先阅读：

1. 本指南；
2. `frontend/web/src/config/themePresets.ts` 当前注册表；
3. `frontend/web/src/hooks/core/useThemePreset.ts` 应用与持久化逻辑；
4. `frontend/web/src/__tests__/theme-preset.spec.ts` 现有契约；
5. 与本次需求直接相关的设计文档，不要把历史设计稿当成高于当前代码的事实。

## 核心边界

主题预设是框架能力，不是产品模板。预设只能表达通用视觉语言，例如海洋、森林、石墨、极夜；不得包含具体行业、客户或产品信息。

必须遵守：

- 使用产品中立的编码、名称、颜色和预览内容；
- 不引入能源、碳管理、MES、WMS 等业务菜单、业务字段、指标、Logo 或示例数据；
- 不复制其他产品仓库的页面、导航结构或品牌资产；
- 不修改后端菜单种子、权限、路由或租户数据来“展示主题”；
- 新增主题不得引入新的路由重载、业务页面重建或浏览器刷新逻辑；
- 用户手动设置的主色和菜单样式仍是独立能力，自动恢复预设时不得无条件覆盖用户自定义值。

## 当前架构与数据契约

主题的 TypeScript 数据模型以 `THEME_PRESETS` 注册表为主源。设置面板通过注册表自动展示预设，不应为每个主题增加条件分支。图表 CSS 变量目前仍在 SCSS 中手工维护，是注册表色板的受约束镜像，不是真正自动生成的数据；新增主题时必须用测试保证两者逐项一致。

| 职责                                          | 文件                                                                                      | 修改要求                     |
| --------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------- |
| 编码、主色、语义色、侧栏令牌、图表色板        | `frontend/web/src/config/themePresets.ts`                                                 | 新主题必须添加完整记录       |
| Element Plus 主色与语义色、DOM 属性、恢复逻辑 | `frontend/web/src/hooks/core/useThemePreset.ts`                                           | 通常不应因新增一套主题而修改 |
| 设置面板预览与选择                            | `frontend/web/src/components/layouts/fa-settings-panel/widgets/FaThemePresetSettings.vue` | 注册表驱动，通常无需修改     |
| 设置面板初始化校验                            | `frontend/web/src/components/layouts/fa-settings-panel/composables/useSettingsPanel.ts`   | 必须认可注册表内全部明暗主色 |
| 图表 CSS 变量镜像                             | `frontend/web/src/styles/theme-presets/index.scss`                                        | 亮暗各 8 色且与注册表一致    |
| 中文名称                                      | `frontend/web/src/locales/langs/zh.json`                                                  | 添加同编码文案               |
| 英文名称                                      | `frontend/web/src/locales/langs/en.json`                                                  | 添加同编码文案               |
| 契约和回归测试                                | `frontend/web/src/__tests__/theme-preset.spec.ts`                                         | 先改测试，再改实现           |

不要在 Store、路由、应用入口或页面组件中再维护一份主题颜色表。SCSS 图表变量是当前唯一允许的手工镜像；若发现同一主题值还要在其他文件重复声明，应先考虑从注册表派生。

`default` 是恢复出厂状态的哨兵值，不是活动预设。它存在于 `ThemePresetCode`，但不进入 `ActiveThemePresetCode`、`THEME_PRESETS` 或新增主题记录；默认预览由设置组件单独维护，新增普通主题时不要修改它。

## 新增主题的标准步骤

### 1. 先定义视觉身份

为主题确定：

- 稳定、简短、产品中立的英文 `code`；
- 中英文显示名称；
- 亮色和暗色主色；
- `success`、`warning`、`danger`、`error`、`info` 语义色；
- 推荐菜单风格；
- 亮暗模式下各自的侧栏背景、正文、图标、标题、激活背景和激活文字；
- 亮暗模式下各 8 个图表颜色。

新增主题之间不能只是把主色做轻微色相偏移。最低要求是亮暗主色不与已有主题重复，侧栏背景或推荐菜单风格至少一项明显不同，激活态可辨认，并在浏览器中人工确认图表色板不是已有主题的近似复制。需求若已指定颜色令牌，以确认后的令牌表为准；否则 Agent 必须明确记录自己的颜色选择依据。

### 2. 先写失败测试

在 `theme-preset.spec.ts` 中先加入预期：

- 注册表编码顺序和唯一性；
- 新编码能被 `isThemePresetCode` 识别；
- `selectPreset` 能正确设置 DOM 属性和主色；
- 中英文名称存在；
- 所有主色、语义色、侧栏令牌和图表颜色都是合法的六位十六进制颜色；
- SCSS 存在对应亮暗选择器，且 16 个图表变量与注册表逐项相等；
- 按钮实际前景色与主色、侧栏正文与背景、标题与背景、图标与背景、激活文字与激活背景满足约定的 WCAG 对比度；
- 每个模式恰好有 8 个图表颜色。

如果现有测试尚未覆盖上述某项，不得因为“当前测试能通过”而跳过。新增或修改的主题必须补齐自己的契约；若加强全局测试会暴露“已知基线限制”中的历史问题，应如实记录并单独安排修复，不能为了让本次新增主题通过而降低断言。涉及真实按钮前景色时，以 Element Plus 当前实际 Token 或渲染样式为准，不要仅凭亮暗模式猜测文字颜色。

运行聚焦测试并确认它因缺少新主题而失败：

```bash
cd frontend/web
pnpm exec vitest run src/__tests__/theme-preset.spec.ts --reporter=dot
```

### 3. 扩展注册表

在 `ThemePresetCode` 联合类型和 `THEME_PRESETS` 中加入新主题。只添加活动主题编码，不要把新记录写成 `default`。记录必须完整，颜色统一使用六位十六进制格式。示意结构：

```ts
{
  code: "example",
  nameKey: "example",
  primary: { light: "#123456", dark: "#ABCDEF" },
  semantics: { ...COMMON_SEMANTICS },
  recommendedMenuTheme: MenuThemeEnum.DESIGN,
  modes: {
    light: {
      sidebarBackground: "#......",
      sidebarText: "#......",
      sidebarIcon: "#......",
      sidebarTitle: "#......",
      sidebarActiveBackground: "#......",
      sidebarActiveText: "#......",
      chartPalette: [/* 恰好 8 色 */],
    },
    dark: {
      // 同样提供完整令牌和 8 色图表色板
    },
  },
}
```

侧栏颜色由注册表通过 Store 的 `getMenuTheme` 消费；不要再在 SCSS 中复制一套侧栏令牌。

### 4. 添加图表变量和文案

在 `theme-presets/index.scss` 中为亮暗模式分别声明 `--fa-chart-1` 至 `--fa-chart-8`。变量值应与注册表对应模式的 `chartPalette` 一致。

同时在中英文语言文件的 `setting.themePreset.presets` 下添加与 `code` 同名的键。不要只添加中文，英文界面必须能够正常显示。

### 5. 验证聚焦测试

再次运行主题测试，要求全部通过。若为了让测试通过而需要在 `useThemePreset.ts`、Store 或设置面板中加入新主题名称的硬编码分支，说明注册表抽象可能被破坏，应先检查设计。

## 持久化与生命周期注意事项

主题预设使用站点、租户和用户隔离的本地存储键。维护时必须保留以下行为：

- 普通租户用户按 `site + tenant + user` 隔离；
- 无业务租户的平台管理员使用独立的 `platform` 作用域；
- 站点配置尚未加载时等待上下文，不要提前永久回落到默认主题；
- 配置加载完成但当前 Host 未匹配站点时使用当前 Origin 隔离作用域；
- 租户切换和登录完成后重新解析当前上下文的主题；
- 登出时移除当前预设的 DOM 和语义色状态，不得误删其他用户的隔离选择；
- 刷新后恢复预设的主色和菜单所有权标记；
- 明暗模式切换时使用同一预设对应模式的主色和令牌。

`themePreset`、`presetAppliedPrimary` 和 `presetAppliedMenuTheme` 是运行时状态，不应直接混入全局 `setting` 持久化对象。预设编码由隔离键保存，避免站点、租户或用户之间串色。

修改持久化或生命周期逻辑时，验收应使用行为测试，而不是只搜索源码中是否出现函数名。至少覆盖两个租户或两个用户连续切换、平台作用域、未知编码回落，以及登出不删除其他作用域选择。

## 已知基线限制

以下问题在编写本指南时仍属于现有实现债务。新增普通主题时不得隐瞒或恶化它们，也不要在没有授权时把主题数据扩展扩大成全局重构：

1. 当前对比度测试对按钮文字色使用了简化假设。Element Plus 主按钮实际通常使用白字，既有部分暗色主色与白字未达到 WCAG AA。新增或修改主题应按实际渲染验证自己的按钮组合；全量调整既有主题主色或按钮文字策略应单独设计和验收。
2. 登出当前会通过 `applyPreset("default", { restoreFactory: false })` 移除预设编码、DOM 属性和语义色，但未显式清空 `presetAppliedPrimary` 与 `presetAppliedMenuTheme`。若任务涉及登出或用户切换生命周期，应先补行为测试并修复；仅新增主题时至少确认隔离键不会跨用户串用。

报告验收结论时，应把“新增主题已通过的检查”和“上述全局基线是否已修复”分开表述，禁止用局部测试通过暗示历史问题已经解决。

## 必须避免的做法

- 为了预览效果，把其他产品的菜单或首页复制进框架；
- 在主题配置中写入客户名、行业名、业务指标或品牌 Logo；
- 为新主题增加 `location.reload()`、Router 重定向或额外的页面级 `reload()`；当前 `FaThemePresetSettings.vue` 仍有一次用于布局重绘的 `store.reload()`，这是既有兼容行为，不要复制或扩散，后续若调整需单独验证页面状态不丢失；
- 只修改设置卡片预览，不验证真实侧栏、主色、语义色和图表；
- 只支持亮色模式，或让暗色模式沿用低对比度颜色；
- 在多个文件重复维护主题主色列表；
- 无条件覆盖用户手动选择的主色或菜单风格；
- 使用随机颜色、运行时随机数据或无法稳定复现的视觉效果；
- 未经验证就宣称刷新恢复、租户隔离或无刷新切换已经可用；
- 把 `dist/`、截图、日志、`.understand-anything/` 等无关生成物加入主题提交。

## 完整验收清单

代码检查：

- [ ] 编码唯一，类型和注册表同步；
- [ ] 中英文文案完整；
- [ ] 每种模式包含完整侧栏令牌和 8 色图表色板；
- [ ] 新增或修改主题的实际按钮文字、侧栏正文、标题、图标和激活态对比度达到约定标准；
- [ ] 没有产品菜单、业务文案、品牌或数据混入；
- [ ] 没有为单个主题新增分支式应用逻辑；
- [ ] 用户自定义主色和菜单样式仍可保留。

自动验证：

```bash
cd frontend/web
pnpm exec vitest run src/__tests__/theme-preset.spec.ts --reporter=dot
pnpm type-check
pnpm test
pnpm build
```

这些命令会执行 `typegen`，可能产生或更新声明文件。运行前后都要执行 `git status --short`，逐项确认生成变化是否真实属于本次主题工作，不能因为它是自动生成的就默认提交。

浏览器验证：

- [ ] 在出厂主色和菜单状态下打开设置面板，新主题可见且可选；
- [ ] 出厂状态选择后，主色、侧栏、激活态和图表色板均发生预期变化；
- [ ] 手动自定义主色或菜单后重新解析预设时，自定义值按所有权规则保留，不误判为主题失效；
- [ ] 主题切换前后 URL 和当前业务页面保持不变；
- [ ] 亮色、暗色模式都经过检查；
- [ ] 刷新后恢复同一主题；
- [ ] 平台管理员和普通租户用户都验证隔离恢复；
- [ ] 浏览器控制台没有新增错误。

提交前：

```bash
git diff --check
git status --short
git diff -- frontend/web/src/config/themePresets.ts \
  frontend/web/src/styles/theme-presets/index.scss \
  frontend/web/src/locales/langs/zh.json \
  frontend/web/src/locales/langs/en.json \
  frontend/web/src/__tests__/theme-preset.spec.ts \
  docs/framework/theme-preset-extension-guide.md \
  AGENTS.md
```

定向 diff 只是重点复核，不能代替检查完整的 `git status --short` 和 `git diff`。只暂存本次主题文件和直接相关文档，使用清晰的 Conventional Commit 中文摘要；除非用户明确要求，不推送远端。

## 维护既有主题

修改现有主题与新增主题使用同一验收标准。若修改 `code`，应先设计旧存储值的兼容或迁移策略；不要直接改名导致用户选择静默失效。删除主题时，未知或已删除编码必须安全回落到 `default`，并验证不会残留旧 DOM 属性、语义色或菜单所有权。

当实现与本文不一致时，先确认当前代码是否已经演进。若代码的新契约经过测试和评审，应在同一个提交中更新本文，避免规范长期失真。
