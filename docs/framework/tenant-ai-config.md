# 租户共享 AI 配置

入口：右上角头像 → 配置中心 → 租户 AI。

接口位于 `/api/v1/system/ai-config`，是系统基础配置，不要求安装 AI 聊天插件。聊天接口仍保留插件检查和原有权限。

## 配置范围与权限

每个实例独立保存配置；实例内按当前登录租户隔离。中控配置不会自动分发到产品。所有接口从服务端认证上下文读取租户，不接受客户端选择其他租户。

真实超管（已选择租户）、当前租户启用的系统 owner/admin 角色可以管理；也支持显式权限 `module_ai:config:manage`，实例需要在菜单/套餐中登记该权限后才能委派。普通自建同名角色无效。普通用户不能查看配置列表、改模型、改绑定或发起连通性检测。

AI 配置不会授予业务权限。产品业务接口必须保留自己的 `AuthPermission`、租户隔离和数据范围检查，再调用 AI 服务；不向前端传模型密钥。

## 两种使用方式

个人聊天（REST 与 WebSocket）依次选择：

1. 当前用户激活的个人模型；
2. 当前租户激活的共享模型；
3. 部署环境 `OPENAI_BASE_URL`、`OPENAI_MODEL`、`OPENAI_API_KEY`。

因此个人配置不会被租户管理员覆盖。个人标签的“使用租户 / 系统默认”清除个人激活项后使用共享默认。

业务功能必须明确选择共享范围：

```python
from app.plugin.module_ai.chat.service import AiRuntimeService

# auth 来自已经通过业务权限和租户验证的请求。
result = await AiRuntimeService(auth, redis, scope="tenant").structured_generate(
    feature_code="demo_data.blueprint",
    prompt=filtered_prompt,
    response_model=YourResultSchema,
    contains_business_data=True,
    business_id=batch_id,
)
```

`YourResultSchema` 为实例定义的 Pydantic 返回结构；`filtered_prompt` 为业务模块筛选后的内容，`batch_id` 为业务审计标识。新业务功能在 `chat/registry.py` 的注册机制中注册自己的功能和提示词，不把产品数据写入框架默认配置。

共享功能绑定独立于个人绑定：支持启用开关、主模型、备用模型、Prompt 版本、超时，以及业务数据许可。模型与功能两层都允许时才能发送业务数据。共享功能没有可用模型时明确失败，不回退到其他用户的私人模型。已有框架演示注册项并不代表自动开放某个产品功能。

“清除共享默认”只清除激活模型，保留配置与显式功能绑定。被功能显式绑定的模型不能直接删除，需先解除主/备用绑定。

## 密钥与保存

租户共享密钥使用 Fernet 加密，密钥由实例 `SECRET_KEY` 派生。接口只返回已配置状态和掩码，编辑留空保留原密钥。固定消息连接检测不发送业务数据，供应商异常正文不回传。

模型配置、激活状态和功能绑定写入 Redis 时不设置 TTL；租户写操作使用同一把分布式锁，避免并发覆盖。存储失败不能当作保存成功。旧的个人配置保持兼容；本次不迁移已有个人密钥的存储格式，也不恢复历史上已过期的数据。

部署应给每个实例独立的 Redis 数据库或 Redis 服务，启用 AOF/RDB、持久卷和备份，并保护 `SECRET_KEY`。清空 Redis 会丢失配置；直接更换 `SECRET_KEY` 会导致共享密钥无法解密，应先规划密钥迁移或重新录入。配置不是仅靠浏览器本地缓存保存。

服务地址支持 OpenAI 兼容协议（含内网 Ollama）；配置管理员属于可信角色，实例部署应按自身网络边界限制模型服务出站访问。

## 验证边界

定向测试覆盖权限、租户隔离、加密、编辑保留、并发和结构化调用。浏览器验收使用本地 OpenAI 兼容协议服务，证明实际 HTTP 调用链路，不等于真实云厂商密钥或计费账户验收。
