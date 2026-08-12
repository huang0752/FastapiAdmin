# 部署矩阵

| 产品 | Assembly | 端口 | 数据库 | Redis namespace | 前端产物 |
|---|---|---:|---|---|---|
| trace | `food-traceability` | 8101 | `food_logi_traceability` | `traceability` | `dist-trace` |
| agri | `agricultural-delivery` | 8102 | `food_logi_agricultural_delivery` | `agricultural-delivery` | `dist-agri` |
| logistic | `cold-chain-vehicle` | 8103 | `food_logi_cold_chain_vehicle` | `cold-chain-vehicle` | `dist-logistic` |

生产建议三个产品使用独立 Redis endpoint。Nginx 必须原样传递 Host，不得使私有文件、上传目录或 WebSocket 绕过认证。

当前迁移目录是工程边界骨架；原框架 Alembic 链尚未物理拆分，在正式数据库上执行前必须完成基线迁移的归档和三空库升级验证。
