# 双品牌站点契约

- `data360`：中文简称“华夏电投”。
- `znceedi`：中文简称“中能电投”。
- 六个生产 Host 为 `trace|agri|logistic` 与 `data360.org.cn|znceedi.org.cn` 的组合。
- 本地 Host 使用对应的 `<product>.<site>.localhost`。

同一产品的两个 Host 共用前端产物、后端、数据库和 Redis，但请求必须同时校验 JWT `site_id`、Redis 会话 `site_id`、Host Site 和 Tenant Site。任一不一致都拒绝。

只保存已确认的中文简称；品牌全称、版权、客服、备案号和帮助地址均为空，不伪造生产内容。
