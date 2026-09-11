# 双产品实例联动验收

本地中控 3100/API 8110，产品 A 3101/API 8111，产品 B 3102/API 8112。
两个产品来自同一框架工作树。B 使用独立 assembly（application_code=example-product-b）、独立 SSO 客户端及密钥、独立数据库 fastapiadmin_product_b_preview 和 Redis DB 2；A 使用 fastapiadmin_product_preview 和 Redis DB 1。共享本机 PostgreSQL/Redis 服务进程，数据库逻辑隔离，并非不同物理主机。

实际 HTTP/API + Worker + 数据库验证通过：

- 中控为新企业 dual350b74 同时开通 A、B，两个异步开户任务成功。
- 中控创建同一个员工并向 A、B 授权，两个实例分别落地为 federated 用户，均不是超管。
- A 租户/员工本地 ID 为 4/13；B 为 3/7，按独立产品维护本地身份映射。
- 员工分别通过 SSO 登录 A、B。
- A 令牌不能访问 B；B 令牌不能访问 A。
- 仅撤销 A 授权：A 原会话失效且中控不再允许启动 A，B 原会话继续可用。
- 重新授权 A：A 保留原用户身份，旧令牌仍失效；B 不受影响。

共记录 23 个成功检查节点。运行脚本、私有测试账号及报告保存在本机预览运行目录，未写入仓库凭证。没有测试业务订单/库存等跨产品数据传递，也未验证实例断网重试或生产部署。本轮是接口联调，不冒充浏览器点击验收。
