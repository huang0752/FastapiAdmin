from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.base_params import BaseQueryParam, TenantByQueryParam, UserByQueryParam


class ChatQuerySchema(BaseModel):
    """WebSocket聊天查询模型"""

    message: str | None = Field("", description="消息内容（停止时可为空）")
    session_id: str | None = Field(None, description="会话ID")
    files: list[dict[str, Any]] | None = Field(None, description="文件信息")
    action: str | None = Field(None, description="动作类型：stop=停止生成 | None=对话")


class ChatSessionCreateSchema(BaseModel):
    """创建会话模型"""

    title: str = Field(..., min_length=1, max_length=200, description="会话标题")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1 or len(v) > 200:
            raise ValueError("会话标题长度必须在1-200个字符之间")
        return v


class ChatSessionUpdateSchema(BaseModel):
    """更新会话模型"""

    title: str = Field(..., min_length=1, max_length=200, description="会话标题")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1 or len(v) > 200:
            raise ValueError("会话标题长度必须在1-200个字符之间")
        return v


class ChatSessionMessageSchema(BaseModel):
    """会话消息模型"""

    id: str = Field(..., description="消息ID")
    role: str = Field(..., description="消息角色")
    content: str = Field(..., description="消息内容")
    created_at: int | None = Field(None, description="创建时间(Unix时间戳)")

    model_config = ConfigDict(from_attributes=True)


@dataclass
class ChatSessionQueryParam(BaseQueryParam, UserByQueryParam, TenantByQueryParam):
    """会话查询参数"""

    title: str | None = Query(None, description="会话标题")


class AiChatRequestSchema(BaseModel):
    """AI 对话请求模型（非流式）"""

    message: str = Field(..., min_length=1, description="用户消息内容")
    session_id: str | None = Field(None, description="会话ID，不传则创建新会话")

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1:
            raise ValueError("用户消息内容不能为空")
        return v


class AiChatResponseSchema(BaseModel):
    """AI 对话响应模型（非流式）"""

    response: str = Field(..., description="AI 回复内容")
    session_id: str = Field(..., description="会话ID")
    function_calls: list[dict[str, Any]] | None = Field(None, description="函数调用信息")
    action: dict[str, Any] | None = Field(None, description="建议执行的操作")


class AiModelConfigBaseSchema(BaseModel):
    """AI 模型配置公共字段"""

    name: str = Field(..., min_length=1, max_length=50, description="配置名称（用户可读）")
    base_url: str = Field(..., min_length=1, max_length=500, description="API Base URL，如 https://api.openai.com/v1")
    model_id: str = Field(..., min_length=1, max_length=100, description="模型 ID")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="温度参数")
    provider_type: Literal["openai_compatible"] = Field(default="openai_compatible", description="模型服务协议")
    timeout_seconds: int = Field(default=60, ge=5, le=300, description="单次模型调用超时秒数")
    max_tokens: int = Field(default=4096, ge=128, le=32768, description="单次调用最大输出 Token")
    allow_business_data: bool = Field(default=False, description="该模型配置是否允许处理业务数据")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
        return v

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("模型 ID 不能为空")
        return v


class AiModelConfigSchema(AiModelConfigBaseSchema):
    """新增 AI 模型配置"""

    api_key: str = Field(..., min_length=1, max_length=500, description="API 密钥")

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("API Key 不能为空")
        return v


class AiModelConfigUpdateSchema(AiModelConfigBaseSchema):
    """更新 AI 模型配置；api_key 为空表示保留已有密钥。"""

    api_key: str | None = Field(None, max_length=500, description="API 密钥；为空表示保留已有密钥")

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class AiModelConfigItemSchema(AiModelConfigBaseSchema):
    """带 ID 的模型配置返回项；不包含明文 API Key。"""

    id: str = Field(..., min_length=1, max_length=64, description="配置项唯一 ID")
    created_time: str | None = Field(None, description="创建时间（ISO 字符串）")
    has_api_key: bool = Field(False, description="是否已配置 API Key")
    api_key_masked: str | None = Field(None, description="脱敏后的 API Key")


class AiModelConfigListResponse(BaseModel):
    """模型配置列表响应"""

    items: list[AiModelConfigItemSchema] = Field(default_factory=list, description="配置项列表")
    active_id: str | None = Field(None, description="当前激活的配置项 ID；为空表示使用系统默认")


class AiFeatureBindingUpdateSchema(BaseModel):
    """AI 功能绑定更新参数；模型密钥始终由服务端解析。"""

    model_config_id: str | None = Field(default=None, max_length=64, description="主模型配置 ID；为空使用当前激活模型")
    fallback_config_id: str | None = Field(default=None, max_length=64, description="备用模型配置 ID")
    prompt_version: str = Field(default="v1", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$", description="提示词版本")
    timeout_seconds: int = Field(default=60, ge=5, le=300, description="功能总超时秒数")
    allow_business_data: bool = Field(default=False, description="该功能是否允许发送经过筛选的业务数据")
    enabled: bool = Field(default=False, description="是否启用该 AI 功能")


class AiFeatureBindingItemSchema(AiFeatureBindingUpdateSchema):
    """AI 功能绑定公开信息。"""

    feature_code: str = Field(..., description="稳定的功能代码")
    feature_name: str = Field(..., description="功能名称")
    description: str = Field(default="", description="功能说明")
    prompt_key: str = Field(..., description="服务端提示词键")
