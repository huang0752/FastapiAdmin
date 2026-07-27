# 行业词库与样例包框架

该目录提供行业词库/样例包的最小后端框架，用于后续模块按 `module`、`industry`、`code` 扩展样例数据。

- `task_industry_sample_pack.json`: 样例包种子入口，框架默认留空，产品仓库可提供 `code/name/module/industry/examples` 数据。
- `task_industry_terms.json`: 行业词条种子入口，框架默认留空，产品仓库可提供 `term/module/industry/aliases/description` 数据。
- 当前只提供按通用 `module` 参数查询的接口，不直接生成业务数据；具体产品数据生成应接入 demo batch 处理器。
