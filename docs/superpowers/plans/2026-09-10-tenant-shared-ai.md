# 租户共享 AI 配置 Implementation Plan

> 使用 executing-plans 在当前工作树逐项执行，完成后统一汇报。

**Goal:** 管理员配置一次模型，当前产品的同租户成员在已有业务授权内使用。

**Architecture:** 抽取 Food 的 tenant scope；模型密钥加密保存，公共响应仅掩码。租户功能绑定显式使用 tenant scope，不混用个人模型；普通聊天个人激活模型优先，其次租户模型，最后部署默认。配置权限由当前租户系统 owner/admin、真实超管或专门管理权限授予，使用权限仍由业务接口负责。

**Tech Stack:** FastAPI、Redis、Fernet、Vue3、pytest、Playwright。

- [x] 服务定向测试先覆盖共享读取、跨租户拒绝、密钥不泄露、空密钥保留、个人兼容、功能运行时。
- [x] service.py 移植 Food tenant scope，补充聊天租户默认和删除已绑定模型保护；保留已有工作树修改。
- [x] tenant_config.py 提供租户模型与功能配置接口、当前能力接口、固定消息连通性检查；module_system/__init__.py 将共享接口注册为系统基础能力（/system/ai-config），不依赖聊天插件安装。
- [x] 复用 FaAiModelConfigPanel，通过 scope 切换 API；配置中心加入租户 AI 标签，非管理者不加载管理接口。
- [x] 定向 pytest、前端类型检查及修改文件静态检查。
- [x] 重启两个本地实例，浏览器操作模型新增、编辑、激活、检测和清理；API 验证普通用户拒绝管理、跨租户隔离。
- [x] README 和 docs/framework/tenant-ai-config.md 写配置优先级、业务接入、Redis持久化和密钥备份；验收报告区分本地兼容协议实测与真实云模型。

本次不提交、不推送，不修改 Food。连通性使用本地 OpenAI 兼容测试服务，不获取或暴露现有供应商密钥。

验收结果：18 项后端测试、2 项前端测试通过；类型、静态检查通过。浏览器和真实 Redis/HTTP 证据见 `docs/verification/2026-09-10-tenant-shared-ai.md`。
