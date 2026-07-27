# PostgreSQL 租户行级安全（RLS）启用指南

应用层的 `tenant_id` 过滤仍是第一道边界；PostgreSQL RLS 用于防御手写 SQL、后台任务遗漏过滤等错误，不替代应用权限和数据范围规则。

## 启用前提

1. 先完成并验证应用层租户隔离，列出所有真正属于租户的数据表。
2. 平台全局表不要套用租户策略；混合全局/租户数据表需要单独设计策略。
3. Web 请求、定时任务、队列任务和维护脚本必须在每个事务开始后执行：

   ```sql
   SET LOCAL app.tenant_id = '<active tenant id>';
   ```

4. 缺少租户上下文时不设置变量，让策略返回零行并拒绝写入，禁止回退到租户 1。
5. 应用数据库角色不能拥有 `BYPASSRLS`，也不应作为表所有者运行；迁移角色和紧急维护角色应与应用角色分离。

## 连接池要求

必须使用 `SET LOCAL`，并放在显式事务内。它会在提交或回滚时自动清除，避免连接返回池后把上一个租户的设置带给下一个请求。禁止使用不带 `LOCAL` 的会话级 `SET app.tenant_id`。

后台任务必须显式携带 `tenant_id`，取得连接并开启事务后再设置变量。平台级任务需要逐租户执行，不能通过设置一个默认租户绕开上下文。

## 灰度顺序

1. 在预发布库创建独立应用角色并确认没有 `BYPASSRLS`。
2. 选择一张低风险租户业务表，使用 [`backend/docs/postgresql-tenant-rls.sql`](../../backend/docs/postgresql-tenant-rls.sql) 作为模板。
3. 先启用 `ENABLE ROW LEVEL SECURITY`，验证请求、任务和运维脚本。
4. 再启用 `FORCE ROW LEVEL SECURITY`，防止表所有者意外绕过策略。
5. 验证跨租户读、写、更新、删除全部失败后，再逐表扩大。
6. 每张表都通过正式迁移部署；不要在应用启动时动态创建策略。

## 验证清单

```sql
SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = current_user;

SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE tablename = 'sys_ticket';

SELECT schemaname, tablename, policyname, qual, with_check
FROM pg_policies
WHERE tablename = 'sys_ticket';
```

测试时分别执行 `SET LOCAL app.tenant_id = '1'` 和另一个租户 ID，确认只能读取本租户数据；不设置变量时应看不到数据且写入失败。迁移回滚必须删除策略并关闭 RLS，不能只删除策略后留下启用但无策略的表。
