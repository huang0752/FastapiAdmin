# 统一中控升级与对账

本工作树仅交付框架实现和测试。以下步骤是未来目标环境的操作说明，本次没有执行业务数据库升级、推送、合并或生产部署。

## 可直接升级的框架路径

- 新库 → `20260910_fa_control_access`。
- 原框架 `20260812_01` → 同一 head。
- 原 Control `20260812_02` → 同一 head。

升级前保存数据库备份、当前 revision、用户/租户/角色/成员/授权计数及目标代码版本。先在副本执行 `python -m alembic upgrade head`。不要对未知版本 stamp；已有同名异构表应先查明原因。

Food 两套库存在同名不同内容 revision 与业务迁移，不能直接套用本迁移图。其桥接迁移属于下游任务；不修改已应用 revision。

## 旧库中控菜单

初始化器遇到已有菜单不会自动补全。升级为 provider 后，从 backend 先预览：

```bash
APP_ASSEMBLY_FILE=app/assemblies/control.toml python -m app.scripts.reconcile_control_seed --site-id 1 --package-code basic
```

Site、套餐 code 必须替换为实际明确目标；可重复 `--package-code`。核对预览后追加 `--apply`。工具只补中控菜单、必要目录及指定套餐租户权限，不更改人工角色或原有菜单；冲突整批失败，不按名称盲目覆盖。

中控应用套餐模板停用控制后续开户选择，不代表撤销已开通的产品。关闭已有产品应停用租户应用开通关系，触发资格撤销。修改产品 code 或目标租户映射前，应先完成原目标的撤权同步。

## 分阶段启用

1. 先升级接收端，配置可信中控和 application_code，开启同步，暂不强制检查历史资格。
2. 升级中控，确认独立 Worker 和恢复扫描器就绪，再预览历史 grant 回填。
3. 从 backend 运行 `python -m app.scripts.backfill_user_entitlements --preview --batch-size 100`；记录 `next_after_id`。只有显式 `--enqueue` 或 `--retry-failed` 才准备并发布任务。
4. 产品角色校正工具 `python -m app.scripts.reconcile_default_user_roles --help` 默认预览。写入需 `--apply` 和精确产品/数据库确认；manual 产品保持人工角色治理。
5. 产品运行 `python -m app.scripts.export_federated_entitlement_state --tenant-id <id>` 导出，只读且有范围；中控 `backfill_user_entitlements --status` 导出当前账本。
6. 用 `python -m app.scripts.audit_user_entitlements --help` 查看输入参数，对照身份、版本、资格、角色和实际菜单证据。缺少任何必需产品导出会失败；空中控账本须显式指定 `--required-product`，避免空结果假通过。manual 等待授权单独报告，`access_ready=false`，不误报为业务已可用。
7. 对账无漂移后启用接收端 `CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED`，再验证普通登录、跨租户、撤权、重试与等待授权。

不从“本地用户存在”或“有角色”推导中控资格。已失去中央成员身份的历史影子账号需要明确处置，不能自动恢复为 active。

## 回滚原则

应用回滚前排空或冻结新写入，保留任务与回执证据；关闭 UI 不能恢复已撤资格。迁移降级测试验证保留用户和原授权，并避免备份恢复覆盖升级后撤权，但生产回滚仍须在实际数据副本演练。Redis 故障时保留数据库清理标记，禁止通过绕过资格检查恢复访问。

## 验证边界

本次定向验证包括迁移、API/Worker、授权乱序、角色治理、Redis Lua 和前端状态测试。测试使用独立临时数据。浏览器人工验收、生产网络、容器构建与 Food 下游真实版本混跑不等于这些本地测试，需独立报告。
