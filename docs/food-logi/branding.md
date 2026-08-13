# 食品物流产品品牌规范

食品安全质量追溯、农产品配送、冷链物流配送车辆三套产品，分别面向 `data360` 与
`znceedi` Site 提供独立的 Logo、favicon 和登录动效资产。

- Host/Site 是品牌解析权威来源；本地与未知 Host 默认 `data360`。
- `data360` 锁定 Ocean 色板，`znceedi` 锁定 Energy 色板。
- 食品产品隐藏 preset 与主题色入口，仅保留亮色/暗色切换。
- Logo 不读取租户 Logo 或用户头像覆盖；用户菜单与个人中心显示当前产品 Site Logo。
- 登录动效使用本地 SVG/CSS，页面隐藏时暂停，并遵守 `prefers-reduced-motion`。

静态验收执行单元测试、类型检查、三套产品构建、产物扫描、ESLint 与 Prettier；不以浏览器
视觉验收作为完成证据。
