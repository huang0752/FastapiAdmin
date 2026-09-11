import asyncio
import base64
import hashlib
import inspect
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime
from functools import wraps
from time import perf_counter
from typing import Any, Literal

from agno.run.team import TeamRunOutput
from agno.session.team import TeamSession
from agno.team.team import Team
from cryptography.fernet import Fernet, InvalidToken
from openai import AsyncOpenAI
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import LockError

from app.api.v1.module_system.dept.service import DeptService
from app.common.enums import RedisInitKeyConfig
from app.common.request import PaginationService
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.core.logger import logger

from .audit import AiCallAuditRecord, record_ai_call_audit
from .crud import ChatSessionCRUD
from .registry import default_ai_registry
from .schema import (
    AiFeatureBindingUpdateSchema,
    AiModelConfigSchema,
    AiModelConfigUpdateSchema,
    ChatQuerySchema,
    ChatSessionCreateSchema,
    ChatSessionQueryParam,
    ChatSessionUpdateSchema,
)
from .utils import AgnoFactory


async def _format_session_data(session: TeamSession, auth: AuthSchema | None = None) -> dict[str, Any]:
    """格式化会话数据，添加前端需要的字段"""
    if hasattr(session, "to_dict"):
        session_dict = session.to_dict()
    else:
        session_dict = {
            "session_id": getattr(session, "session_id", ""),
            "agent_id": getattr(session, "agent_id", None),
            "team_id": getattr(session, "team_id", None),
            "workflow_id": getattr(session, "workflow_id", None),
            "user_id": getattr(session, "user_id", None),
            "session_data": getattr(session, "session_data", None),
            "agent_data": getattr(session, "agent_data", None),
            "team_data": getattr(session, "team_data", None),
            "workflow_data": getattr(session, "workflow_data", None),
            "metadata": getattr(session, "metadata", None),
            "runs": getattr(session, "runs", []),
            "summary": getattr(session, "summary", None),
            "created_at": getattr(session, "created_at", None),
            "updated_at": getattr(session, "updated_at", None),
        }

    session_data = session_dict.get("session_data") or {}
    runs = session_dict.get("runs") or []
    messages = _extract_messages(runs)

    # 从 session_data 中获取 session_name 作为标题
    session_name = session_data.get("session_name") if session_data else None

    result = {
        **session_dict,
        "id": session_dict.get("session_id"),
        "title": session_name or session_dict.get("session_id", "")[:8] or "未命名会话",
        "created_time": _unix_to_datetime(session_dict.get("created_at")),
        "updated_time": _unix_to_datetime(session_dict.get("updated_at")),
        "message_count": len(messages),
        "messages": messages,
    }

    # 如果有 auth，查询部门名称
    if auth and session_dict.get("team_id"):
        try:
            team_id = session_dict.get("team_id")
            if isinstance(team_id, str):
                dept_name = await DeptService(auth).detail(id=int(team_id))
                result["team_name"] = dept_name.get("name")
            elif isinstance(team_id, int):
                dept_name = await DeptService(auth).detail(id=team_id)
                result["team_name"] = dept_name.get("name")
            else:
                result["team_name"] = None
        except Exception:
            result["team_name"] = None
    else:
        result["team_name"] = None

    # 如果 summary 是 SessionSummary 对象，提取 summary 字段
    summary = session_dict.get("summary")
    if summary:
        if isinstance(summary, dict):
            result["summary"] = summary.get("summary")
        else:
            result["summary"] = str(summary)

    return result


def _unix_to_datetime(timestamp: int | None) -> str | None:
    """将Unix时间戳转换为日期时间字符串"""
    if timestamp is None:
        return None
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return None


def _extract_messages(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 runs 中提取消息"""
    messages = []
    if not runs:
        return messages
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_messages = run.get("messages", [])
        if run_messages and isinstance(run_messages, list):
            for msg in run_messages:
                if isinstance(msg, dict):
                    role = msg.get("role")
                    if role in ("user", "assistant"):
                        messages.append(
                            {
                                "id": msg.get("id"),
                                "role": role,
                                "content": msg.get("content", ""),
                                "created_at": msg.get("created_at"),
                            }
                        )
    return messages


class ChatService:
    """聊天会话管理模块服务层"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def chat_query(
        self,
        query: ChatQuerySchema,
        stop_event: asyncio.Event | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式 AI 对话"""
        try:
            crud = ChatSessionCRUD(self.auth)

            session_id = query.session_id
            if not session_id:
                import uuid

                session_id = str(uuid.uuid4())
                session: TeamSession | None = await crud.create_crud(data=ChatSessionCreateSchema(title="新对话"))
                if not session:
                    raise CustomException(msg="创建会话失败")
                session_id = session.session_id

            agno_factory = AgnoFactory()
            dept_id = str(self.auth.user.dept_id) if self.auth and self.auth.user and hasattr(self.auth.user, "dept_id") and self.auth.user.dept_id else "default"
            agent = agno_factory.create_agent(
                user_id=self.auth.user.username if self.auth and self.auth.user else "user",
                dept_id=dept_id,
                session_id=session_id,
                db=crud.db,
                model_config=model_config,
            )

            message = (query.message or "").strip()
            if not message:
                yield "请输入消息内容"
                return

            logger.info("开始流式生成: session_id={} message={!r}", session_id, message[:80])
            chunk_count = 0
            stream = None
            try:
                stream = agent.arun(input=message, stream=True)
                logger.info("agent.arun 返回对象类型: {}", type(stream).__name__)
                if hasattr(stream, "__aiter__"):
                    async for chunk in stream:
                        if stop_event is not None and stop_event.is_set():
                            logger.info("用户主动停止生成: session_id={}", session_id)
                            return
                        if chunk and getattr(chunk, "content", None):
                            chunk_count += 1
                            yield chunk.content
                        else:
                            logger.debug("空 chunk 跳过: {}", type(chunk).__name__ if chunk else None)
                else:
                    # 兼容非流式直接返回结果的场景
                    logger.warning("agent.arun 未返回异步迭代器，尝试按单次结果处理")
                    if stream and getattr(stream, "content", None):
                        chunk_count += 1
                        yield stream.content
            except asyncio.CancelledError:
                logger.info("生成任务被取消: session_id={}", session_id)
                raise
            finally:
                close_stream = getattr(stream, "aclose", None)
                if close_stream is None:
                    close_stream = getattr(stream, "cancel", None)
                if callable(close_stream):
                    close_result = close_stream()
                    if inspect.isawaitable(close_result):
                        await close_result

            logger.info("流式生成结束: session_id={} chunk_count={}", session_id, chunk_count)

        except Exception as e:
            logger.error(f"聊天查询失败: {e}", exc_info=True)
            yield f"抱歉，处理您的请求时出现错误：{str(e)}"

    async def chat_non_stream(
        self,
        message: str,
        session_id: str | None,
        redis: Redis | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """非流式 AI 对话"""
        effective_model_config: dict[str, Any] | None = None
        try:
            crud = ChatSessionCRUD(self.auth)

            if not session_id:
                import uuid

                session_id = str(uuid.uuid4())
                session: TeamSession | None = await crud.create_crud(data=ChatSessionCreateSchema(title="新对话"))
                if not session:
                    raise CustomException(msg="创建会话失败")
                session_id = session.session_id

            agno_factory = AgnoFactory()
            dept_id = str(self.auth.user.dept_id) if self.auth and self.auth.user and hasattr(self.auth.user, "dept_id") and self.auth.user.dept_id else "default"
            effective_model_config = model_config
            if effective_model_config is None and redis is not None:
                effective_model_config = await resolve_effective_model_config(redis, self.auth)
            agent: Team = agno_factory.create_agent(
                user_id=self.auth.user.username if self.auth and self.auth.user else "user",
                dept_id=dept_id,
                session_id=session_id,
                db=crud.db,
                model_config=effective_model_config,
            )

            response: TeamRunOutput = await agent.arun(input=message)

            response_text = ""
            action = None

            if response and response.content:
                response_text = response.content
                try:
                    if response_text.strip().startswith("{") and response_text.strip().endswith("}"):
                        action = json.loads(response_text)
                    elif "```json" in response_text:
                        json_start = response_text.find("```json") + 7
                        json_end = response_text.find("```", json_start)
                        if json_end > json_start:
                            json_str = response_text[json_start:json_end].strip()
                            action = json.loads(json_str)
                except (json.JSONDecodeError, Exception):
                    pass

                if not action:
                    action = self._parse_action_from_response(response_text)

            result = {
                "response": response_text,
                "session_id": session_id,
                "function_calls": None,
                "action": action,
            }
            await record_ai_call_audit(
                redis,
                AiCallAuditRecord(
                    user_id=getattr(self.auth.user, "id", None) if self.auth and self.auth.user else None,
                    tenant_id=self.auth.tenant_id if self.auth else None,
                    session_id=session_id,
                    message=message,
                    model_config=effective_model_config,
                    status="success",
                ),
            )
            return result

        except Exception as e:
            logger.error(f"聊天查询失败: {e}")
            await record_ai_call_audit(
                redis,
                AiCallAuditRecord(
                    user_id=getattr(self.auth.user, "id", None) if self.auth and self.auth.user else None,
                    tenant_id=self.auth.tenant_id if self.auth else None,
                    session_id=session_id,
                    message=message,
                    model_config=effective_model_config,
                    status="error",
                    error=str(e),
                ),
            )
            return {
                "response": f"抱歉，处理您的请求时出现错误：{str(e)}",
                "session_id": session_id,
                "function_calls": None,
                "action": None,
            }

    @staticmethod
    def _parse_action_from_response(response_text: str) -> dict[str, Any] | None:
        """从响应文本中解析操作建议"""

        route_config = {
            "用户管理": {"path": "/system/user", "name": "用户管理"},
            "角色管理": {"path": "/system/role", "name": "角色管理"},
            "菜单管理": {"path": "/system/menu", "name": "菜单管理"},
            "部门管理": {"path": "/system/dept", "name": "部门管理"},
            "字典管理": {"path": "/system/dict", "name": "字典管理"},
            "系统日志": {"path": "/system/log", "name": "系统日志"},
        }

        navigation_keywords = ["跳转", "打开", "进入", "前往", "去", "浏览", "查看"]
        has_navigation = any(keyword in response_text for keyword in navigation_keywords)

        if not has_navigation:
            return None

        for page_name, route_info in route_config.items():
            if page_name in response_text:
                return {
                    "type": "navigate",
                    "path": route_info["path"],
                    "name": route_info["name"],
                }

        keyword_mapping = {
            "用户": {"path": "/system/user", "name": "用户管理"},
            "角色": {"path": "/system/role", "name": "角色管理"},
            "菜单": {"path": "/system/menu", "name": "菜单管理"},
            "部门": {"path": "/system/dept", "name": "部门管理"},
            "字典": {"path": "/system/dict", "name": "字典管理"},
            "日志": {"path": "/system/log", "name": "系统日志"},
        }

        for keyword, route_info in keyword_mapping.items():
            if keyword in response_text:
                return {
                    "type": "navigate",
                    "path": route_info["path"],
                    "name": route_info["name"],
                }

        return None

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        crud = ChatSessionCRUD(self.auth)
        session: TeamSession | None = await crud.get_by_id_crud(session_id=session_id)
        if session:
            return await _format_session_data(session, self.auth)
        return None

    async def create(self, data: ChatSessionCreateSchema) -> dict[str, Any] | None:
        crud = ChatSessionCRUD(self.auth)
        session = await crud.create_crud(data=data)
        if session:
            return await _format_session_data(session, self.auth)
        return None

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ChatSessionQueryParam,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        crud = ChatSessionCRUD(self.auth)
        sessions = await crud.list_crud()
        items = [await _format_session_data(s, self.auth) for s in sessions]
        return await PaginationService.paginate(
            data_list=items,
            page_no=page_no,
            page_size=page_size,
        )

    async def update(self, session_id: str, data: ChatSessionUpdateSchema) -> bool:
        crud = ChatSessionCRUD(self.auth)
        return await crud.update_crud(session_id=session_id, data=data)

    async def delete(self, session_ids: list[str]) -> None:
        await ChatSessionCRUD(self.auth).delete_crud(session_ids=session_ids)


# ================================================= #
# ******************* AI 模型配置 ****************** #
# ================================================= #

async def _save_ai_config(redis: Redis, key: str, value: str) -> None:
    # 配置不是短期缓存。使用直接写入让存储故障传播，不能报告虚假的保存成功。
    if not await redis.set(name=key, value=value.encode("utf-8")):
        raise CustomException(msg="模型配置保存失败", code=10500, status_code=503)


AiConfigScope = Literal["user", "tenant"]


def _ai_model_items_key(user_id: int) -> str:
    return f"{RedisInitKeyConfig.AI_MODEL_CONFIG.key}:items:{user_id}"


def _ai_model_active_key(user_id: int) -> str:
    return f"{RedisInitKeyConfig.AI_MODEL_CONFIG.key}:active:{user_id}"


def _tenant_ai_model_items_key(tenant_id: int) -> str:
    return f"{RedisInitKeyConfig.AI_MODEL_CONFIG.key}:tenant:items:{tenant_id}"


def _tenant_ai_model_active_key(tenant_id: int) -> str:
    return f"{RedisInitKeyConfig.AI_MODEL_CONFIG.key}:tenant:active:{tenant_id}"


def _decode_redis_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 4:
        return "****"
    return f"****{api_key[-4:]}"


def _api_key_fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypted_api_key(api_key: str) -> dict[str, Any]:
    return {
        "version": 1,
        "ciphertext": _api_key_fernet().encrypt(api_key.encode("utf-8")).decode("ascii"),
        "last4": api_key[-4:] if len(api_key) > 4 else "",
    }


def _decrypt_api_key(envelope: Any) -> str:
    if not isinstance(envelope, dict) or envelope.get("version") != 1:
        raise CustomException(msg="模型配置密钥不可用", code=10400, status_code=400)
    try:
        return _api_key_fernet().decrypt(str(envelope["ciphertext"]).encode("ascii")).decode("utf-8")
    except (InvalidToken, KeyError, TypeError, ValueError, UnicodeDecodeError):
        logger.warning("租户 AI 模型配置密钥解密失败")
        raise CustomException(msg="模型配置密钥不可用", code=10400, status_code=400) from None


def _public_model_config(item: dict[str, Any]) -> dict[str, Any]:
    encrypted = item.get("api_key_encrypted")
    raw_api_key = item.get("api_key")
    last4 = encrypted.get("last4") if isinstance(encrypted, dict) else None
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "base_url": item.get("base_url"),
        "model_id": item.get("model_id"),
        "temperature": item.get("temperature"),
        "provider_type": item.get("provider_type", "openai_compatible"),
        "timeout_seconds": item.get("timeout_seconds", 60),
        "max_tokens": item.get("max_tokens", 4096),
        "allow_business_data": bool(item.get("allow_business_data", False)),
        "created_time": item.get("created_time"),
        "has_api_key": bool(raw_api_key or encrypted),
        "api_key_masked": (f"****{last4}" if last4 else "****") if encrypted else _mask_api_key(raw_api_key),
    }


def _runtime_model_config(item: dict[str, Any], *, auth: AuthSchema | None = None, source: str = "user_active") -> dict[str, Any]:
    return {
        "base_url": item.get("base_url"),
        "api_key": item.get("api_key"),
        "model_id": item.get("model_id"),
        "temperature": item.get("temperature"),
        "provider_type": item.get("provider_type", "openai_compatible"),
        "timeout_seconds": item.get("timeout_seconds", 60),
        "max_tokens": item.get("max_tokens", 4096),
        "allow_business_data": bool(item.get("allow_business_data", False)),
        "config_id": item.get("id"),
        "source": source,
        "tenant_id": auth.tenant_id if auth else None,
        "user_id": getattr(auth.user, "id", None) if auth and auth.user else None,
    }


async def get_user_model_config(redis: Redis, user_id: int) -> dict[str, Any] | None:
    """读取当前激活的 AI 模型配置；不存在或未激活返回 None。"""
    active_id = _decode_redis_value(await redis.get(_ai_model_active_key(user_id)))
    if not active_id:
        return None
    items = await list_user_model_configs(redis, user_id)
    for item in items:
        if item.get("id") == active_id:
            return item
    return None


async def list_user_model_configs(redis: Redis, user_id: int) -> list[dict[str, Any]]:
    """列出用户的所有模型配置项。"""
    raw = _decode_redis_value(await redis.get(_ai_model_items_key(user_id)))
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, TypeError):
        logger.warning("AI 模型配置列表 JSON 解析失败: user_id={}", user_id)
        return []


async def get_active_model_id(redis: Redis, user_id: int) -> str | None:
    """读取当前激活的模型配置 ID；为空表示使用系统默认。"""
    return _decode_redis_value(await redis.get(_ai_model_active_key(user_id)))


async def list_tenant_model_configs(redis: Redis, tenant_id: int) -> list[dict[str, Any]]:
    raw = _decode_redis_value(await redis.get(_tenant_ai_model_items_key(tenant_id)))
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.warning("租户 AI 模型配置列表 JSON 解析失败: tenant_id={}", tenant_id)
        return []


async def get_tenant_active_model_id(redis: Redis, tenant_id: int) -> str | None:
    return _decode_redis_value(await redis.get(_tenant_ai_model_active_key(tenant_id)))


async def _get_tenant_model_config_by_id(redis: Redis, tenant_id: int, config_id: str) -> dict[str, Any] | None:
    return next((item for item in await list_tenant_model_configs(redis, tenant_id) if item.get("id") == config_id), None)


async def get_tenant_model_config(redis: Redis, tenant_id: int) -> dict[str, Any] | None:
    active_id = await get_tenant_active_model_id(redis, tenant_id)
    if not active_id:
        return None
    return await _get_tenant_model_config_by_id(redis, tenant_id, active_id)


async def resolve_effective_model_config(redis: Redis, auth: AuthSchema) -> dict[str, Any]:
    """解析当前请求最终生效的模型配置。"""
    user_id = getattr(auth.user, "id", None) if auth and auth.user else None
    if user_id is not None:
        active_config = await get_user_model_config(redis, user_id)
        if active_config:
            return _runtime_model_config(active_config, auth=auth, source="user_active")
    if auth and auth.tenant_id is not None:
        shared = await get_tenant_model_config(redis, auth.tenant_id)
        if shared:
            return _runtime_model_config({**shared, "api_key": _decrypt_api_key(shared.get("api_key_encrypted"))}, auth=auth, source="tenant_active")
    return {
        "base_url": settings.OPENAI_BASE_URL,
        "api_key": settings.OPENAI_API_KEY,
        "model_id": settings.OPENAI_MODEL,
        "temperature": AgnoFactory.AGENT_TEMPERATURE,
        "provider_type": "openai_compatible",
        "timeout_seconds": AgnoFactory.REQUEST_TIMEOUT,
        "max_tokens": 4096,
        "allow_business_data": False,
        "config_id": None,
        "source": "system_default",
        "tenant_id": auth.tenant_id if auth else None,
        "user_id": user_id,
    }


async def create_user_model_config(
    redis: Redis,
    user_id: int,
    config: AiModelConfigSchema,
) -> dict[str, Any]:
    """新增一个模型配置项。"""
    import uuid
    from datetime import datetime

    items = await list_user_model_configs(redis, user_id)
    item = {
        **config.model_dump(),
        "id": uuid.uuid4().hex,
        "created_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    items.append(item)
    await _save_ai_config(redis,
        _ai_model_items_key(user_id),
        json.dumps(items, ensure_ascii=False),
    )

    # 若用户尚未激活任何配置，自动激活新增的
    if not await get_active_model_id(redis, user_id):
        await _save_ai_config(redis, _ai_model_active_key(user_id), item["id"])

    logger.info("已新增 AI 模型配置: user_id={} name={} id={}", user_id, config.name, item["id"])
    return item


async def update_user_model_config(
    redis: Redis,
    user_id: int,
    config_id: str,
    config: AiModelConfigUpdateSchema,
) -> dict[str, Any] | None:
    """更新指定 ID 的模型配置项；不存在返回 None。"""
    items = await list_user_model_configs(redis, user_id)
    target = next((it for it in items if it.get("id") == config_id), None)
    if not target:
        return None
    data = config.model_dump(exclude_none=True)
    target.update(data)
    await _save_ai_config(redis,
        _ai_model_items_key(user_id),
        json.dumps(items, ensure_ascii=False),
    )
    logger.info("已更新 AI 模型配置: user_id={} id={}", user_id, config_id)
    return target


async def delete_user_model_config(redis: Redis, user_id: int, config_id: str) -> bool:
    """删除指定 ID 的模型配置项；若该 ID 是当前激活则清空激活。"""
    items = await list_user_model_configs(redis, user_id)
    new_items = [it for it in items if it.get("id") != config_id]
    if len(new_items) == len(items):
        return False
    await _save_ai_config(redis,
        _ai_model_items_key(user_id),
        json.dumps(new_items, ensure_ascii=False),
    )
    active_id = await get_active_model_id(redis, user_id)
    if active_id == config_id:
        await redis.delete(_ai_model_active_key(user_id))
    logger.info("已删除 AI 模型配置: user_id={} id={}", user_id, config_id)
    return True


async def set_active_model_config(redis: Redis, user_id: int, config_id: str) -> bool:
    """设置当前激活的模型配置项；id 为空字符串或 "__default__" 表示使用系统默认。"""
    if config_id in ("", "__default__"):
        await redis.delete(_ai_model_active_key(user_id))
        logger.info("已切换到系统默认模型: user_id={}", user_id)
        return True
    items = await list_user_model_configs(redis, user_id)
    if not any(it.get("id") == config_id for it in items):
        return False
    await _save_ai_config(redis, _ai_model_active_key(user_id), config_id)
    logger.info("已切换 AI 模型: user_id={} id={}", user_id, config_id)
    return True


def _serialize_tenant_config_write(method):
    """同一租户的模型和绑定共同串行写入，避免读改写丢失与删除竞态。"""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        if self.scope != "tenant":
            return await method(self, *args, **kwargs)
        try:
            async with self.redis.lock(f"ai_config:tenant:lock:{self._tenant_id}", timeout=15, blocking_timeout=5):
                return await method(self, *args, **kwargs)
        except LockError:
            raise CustomException(msg="配置正在更新，请稍后重试", code=10400, status_code=409) from None
    return wrapped


class AiModelConfigService:
    """AI 模型配置业务服务（多配置 + 激活切换）"""

    def __init__(self, auth: AuthSchema, redis: Redis, *, scope: AiConfigScope = "user") -> None:
        self.auth = auth
        self.redis = redis
        self.scope = scope

    @property
    def _user_id(self) -> int:
        if not self.auth or not self.auth.user:
            raise CustomException(msg="未登录", code=10401, status_code=401)
        return self.auth.user.id

    @property
    def _tenant_id(self) -> int:
        if self.auth.tenant_id is None:
            raise CustomException(msg="缺少租户上下文", code=10403, status_code=403)
        return self.auth.tenant_id

    async def _tenant_save_items(self, items: list[dict[str, Any]]) -> None:
        await _save_ai_config(self.redis,
            _tenant_ai_model_items_key(self._tenant_id),
            json.dumps(items, ensure_ascii=False),
        )

    async def list(self) -> dict[str, Any]:
        """获取配置列表 + 当前激活 ID。"""
        if self.scope == "tenant":
            items = await list_tenant_model_configs(self.redis, self._tenant_id)
            active_id = await get_tenant_active_model_id(self.redis, self._tenant_id)
            return {"items": [_public_model_config(item) for item in items], "active_id": active_id}
        items = await list_user_model_configs(self.redis, self._user_id)
        active_id = await get_active_model_id(self.redis, self._user_id)
        return {"items": [_public_model_config(item) for item in items], "active_id": active_id}

    async def get_active(self) -> dict[str, Any] | None:
        if self.scope == "tenant":
            return await get_tenant_model_config(self.redis, self._tenant_id)
        return await get_user_model_config(self.redis, self._user_id)

    @_serialize_tenant_config_write
    async def create(self, config: AiModelConfigSchema) -> dict[str, Any]:
        if self.scope == "tenant":
            import uuid

            items = await list_tenant_model_configs(self.redis, self._tenant_id)
            data = config.model_dump()
            api_key = data.pop("api_key")
            item = {
                **data,
                "api_key_encrypted": _encrypted_api_key(api_key),
                "id": uuid.uuid4().hex,
                "created_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            items.append(item)
            await self._tenant_save_items(items)
            if not await get_tenant_active_model_id(self.redis, self._tenant_id):
                await _save_ai_config(self.redis, _tenant_ai_model_active_key(self._tenant_id), item["id"])
            logger.info("已新增租户 AI 模型配置: tenant_id={} name={} id={}", self._tenant_id, config.name, item["id"])
            return _public_model_config(item)
        return _public_model_config(await create_user_model_config(self.redis, self._user_id, config))

    @_serialize_tenant_config_write
    async def update(self, config_id: str, config: AiModelConfigUpdateSchema) -> dict[str, Any] | None:
        if self.scope == "tenant":
            items = await list_tenant_model_configs(self.redis, self._tenant_id)
            target = next((item for item in items if item.get("id") == config_id), None)
            if target is None:
                raise CustomException(msg="模型配置不存在", code=10404, status_code=404)
            data = config.model_dump(exclude_none=True)
            api_key = data.pop("api_key", None)
            if api_key:
                target["api_key_encrypted"] = _encrypted_api_key(api_key)
            target.update(data)
            await self._tenant_save_items(items)
            logger.info("已更新租户 AI 模型配置: tenant_id={} id={}", self._tenant_id, config_id)
            return _public_model_config(target)
        result = await update_user_model_config(self.redis, self._user_id, config_id, config)
        if result is None:
            raise CustomException(msg="模型配置不存在", code=10404, status_code=404)
        return _public_model_config(result)

    @_serialize_tenant_config_write
    async def delete(self, config_id: str) -> None:
        if self.scope == "tenant":
            bindings = await AiFeatureBindingService(self.auth, self.redis, scope="tenant").list()
            if any(config_id in (item.get("model_config_id"), item.get("fallback_config_id")) for item in bindings):
                raise CustomException(msg="模型仍被功能绑定，请先解除绑定", code=10400, status_code=400)
            items = await list_tenant_model_configs(self.redis, self._tenant_id)
            new_items = [item for item in items if item.get("id") != config_id]
            if len(new_items) == len(items):
                raise CustomException(msg="模型配置不存在", code=10404, status_code=404)
            await self._tenant_save_items(new_items)
            if await get_tenant_active_model_id(self.redis, self._tenant_id) == config_id:
                await self.redis.delete(_tenant_ai_model_active_key(self._tenant_id))
            logger.info("已删除租户 AI 模型配置: tenant_id={} id={}", self._tenant_id, config_id)
            return
        ok = await delete_user_model_config(self.redis, self._user_id, config_id)
        if not ok:
            raise CustomException(msg="模型配置不存在", code=10404, status_code=404)

    @_serialize_tenant_config_write
    async def set_active(self, config_id: str) -> None:
        if self.scope == "tenant":
            if config_id in ("", "__default__"):
                await self.redis.delete(_tenant_ai_model_active_key(self._tenant_id))
                return
            if await _get_tenant_model_config_by_id(self.redis, self._tenant_id, config_id) is None:
                raise CustomException(msg="模型配置不存在", code=10404, status_code=404)
            await _save_ai_config(self.redis, _tenant_ai_model_active_key(self._tenant_id), config_id)
            return
        ok = await set_active_model_config(self.redis, self._user_id, config_id)
        if not ok:
            raise CustomException(msg="模型配置不存在", code=10404, status_code=404)


# ================================================= #
# *************** AI 功能绑定与运行时 ************** #
# ================================================= #


def _ai_feature_bindings_key(tenant_id: int | None, user_id: int) -> str:
    return f"ai_feature_binding:{tenant_id or 0}:{user_id}"


def _tenant_ai_feature_bindings_key(tenant_id: int) -> str:
    return f"ai_feature_binding:tenant:{tenant_id}"


async def _get_user_model_config_by_id(redis: Redis, user_id: int, config_id: str) -> dict[str, Any] | None:
    return next((item for item in await list_user_model_configs(redis, user_id) if item.get("id") == config_id), None)


class AiFeatureBindingService:
    """管理当前租户、当前用户可用的 AI 功能绑定。"""

    def __init__(self, auth: AuthSchema, redis: Redis, *, scope: AiConfigScope = "user") -> None:
        self.auth = auth
        self.redis = redis
        self.scope = scope

    @property
    def _user_id(self) -> int:
        if not self.auth or not self.auth.user:
            raise CustomException(msg="未登录", code=10401, status_code=401)
        return self.auth.user.id

    @property
    def _key(self) -> str:
        if self.scope == "tenant":
            return _tenant_ai_feature_bindings_key(self._tenant_id)
        return _ai_feature_bindings_key(self.auth.tenant_id, self._user_id)

    @property
    def _tenant_id(self) -> int:
        if self.auth.tenant_id is None:
            raise CustomException(msg="缺少租户上下文", code=10403, status_code=403)
        return self.auth.tenant_id

    async def _saved(self) -> dict[str, dict[str, Any]]:
        raw = _decode_redis_value(await self.redis.get(self._key))
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("AI 功能绑定 JSON 解析失败: tenant_id={} user_id={}", self.auth.tenant_id, self._user_id)
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _default_item(feature: Any) -> dict[str, Any]:
        return {
            "feature_code": feature.code,
            "feature_name": feature.name,
            "description": feature.description,
            "prompt_key": feature.prompt_key,
            "prompt_version": "v1",
            "model_config_id": None,
            "fallback_config_id": None,
            "timeout_seconds": feature.default_timeout_seconds,
            "allow_business_data": False,
            "enabled": False,
        }

    async def list(self) -> list[dict[str, Any]]:
        saved = await self._saved()
        return [{**self._default_item(feature), **saved.get(feature.code, {})} for feature in default_ai_registry.list_features()]

    async def get(self, feature_code: str) -> dict[str, Any]:
        feature = default_ai_registry.get_feature(feature_code)
        if feature is None:
            raise CustomException(msg="AI 功能不存在", code=10404, status_code=404)
        saved = await self._saved()
        return {**self._default_item(feature), **saved.get(feature_code, {})}

    @_serialize_tenant_config_write
    async def upsert(self, feature_code: str, data: AiFeatureBindingUpdateSchema) -> dict[str, Any]:
        feature = default_ai_registry.get_feature(feature_code)
        if feature is None:
            raise CustomException(msg="AI 功能不存在", code=10404, status_code=404)
        tenant_id = self._tenant_id if self.scope == "tenant" else None
        if data.model_config_id:
            primary = (
                await _get_tenant_model_config_by_id(self.redis, tenant_id, data.model_config_id)
                if tenant_id is not None
                else await _get_user_model_config_by_id(self.redis, self._user_id, data.model_config_id)
            )
            if primary is None:
                raise CustomException(msg="主模型配置不存在", code=10404, status_code=404)
        if data.fallback_config_id:
            fallback = (
                await _get_tenant_model_config_by_id(self.redis, tenant_id, data.fallback_config_id)
                if tenant_id is not None
                else await _get_user_model_config_by_id(self.redis, self._user_id, data.fallback_config_id)
            )
            if fallback is None:
                raise CustomException(msg="备用模型配置不存在", code=10404, status_code=404)
        if data.model_config_id and data.model_config_id == data.fallback_config_id:
            raise CustomException(msg="主模型与备用模型不能相同", code=10400, status_code=400)
        saved = await self._saved()
        item = {
            **self._default_item(feature),
            **data.model_dump(),
            "feature_code": feature.code,
            "feature_name": feature.name,
            "description": feature.description,
            "prompt_key": feature.prompt_key,
        }
        saved[feature_code] = item
        await _save_ai_config(self.redis, self._key, json.dumps(saved, ensure_ascii=False))
        logger.info("AI 功能绑定已更新: tenant_id={} scope={} feature_code={}", self.auth.tenant_id, self.scope, feature_code)
        return item


StructuredRunner = Callable[..., Awaitable[Any]]


def _validate_structured_result[ResultT: BaseModel](response_model: type[ResultT], raw: Any) -> ResultT:
    if isinstance(raw, response_model):
        return raw
    if isinstance(raw, BaseModel):
        return response_model.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return response_model.model_validate(raw)
    content = str(raw or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1]).strip()
    return response_model.model_validate_json(content)


async def _openai_structured_runner(
    *,
    model_config: dict[str, Any],
    prompt: str,
    system_prompt: str,
    response_model: type[BaseModel],
) -> Any:
    client = AsyncOpenAI(
        api_key=str(model_config.get("api_key") or ""),
        base_url=str(model_config.get("base_url") or ""),
        timeout=float(model_config.get("timeout_seconds") or 60),
    )
    schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
    response = await client.chat.completions.create(
        model=str(model_config.get("model_id") or ""),
        messages=[
            {"role": "system", "content": f"{system_prompt}\n只返回符合以下 JSON Schema 的 JSON，不要使用 Markdown：{schema}"},
            {"role": "user", "content": prompt},
        ],
        temperature=float(model_config.get("temperature") or 0),
        max_tokens=int(model_config.get("max_tokens") or 4096),
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


class AiRuntimeService:
    """面向产品功能的有界、可审计、结构化 AI 调用入口。"""

    def __init__(
        self,
        auth: AuthSchema,
        redis: Redis,
        *,
        runner: StructuredRunner | None = None,
        scope: AiConfigScope = "user",
    ) -> None:
        self.auth = auth
        self.redis = redis
        self.runner = runner or _openai_structured_runner
        self.scope = scope

    @property
    def _user_id(self) -> int:
        if not self.auth or not self.auth.user:
            raise CustomException(msg="未登录", code=10401, status_code=401)
        return self.auth.user.id

    async def _runtime_config(self, config_id: str | None, *, source: str) -> dict[str, Any]:
        if self.scope == "tenant":
            if self.auth.tenant_id is None:
                raise CustomException(msg="缺少租户上下文", code=10403, status_code=403)
            item = await _get_tenant_model_config_by_id(self.redis, self.auth.tenant_id, config_id) if config_id else await get_tenant_model_config(self.redis, self.auth.tenant_id)
            if item is None:
                raise CustomException(msg="模型配置不存在", code=10404, status_code=404)
            runtime_item = {**item, "api_key": _decrypt_api_key(item.get("api_key_encrypted"))}
            return _runtime_model_config(runtime_item, auth=self.auth, source=source)
        if config_id:
            item = await _get_user_model_config_by_id(self.redis, self._user_id, config_id)
            if item is None:
                raise CustomException(msg="模型配置不存在", code=10404, status_code=404)
            return _runtime_model_config(item, auth=self.auth, source=source)
        return await resolve_effective_model_config(self.redis, self.auth)

    @staticmethod
    def _assert_business_data_policy(binding: dict[str, Any], model_config: dict[str, Any], contains_business_data: bool) -> None:
        if contains_business_data and not (binding.get("allow_business_data") and model_config.get("allow_business_data")):
            raise CustomException(msg="AI 安全策略未允许处理业务数据", code=10403, status_code=403)

    async def structured_generate[ResultT: BaseModel](
        self,
        *,
        feature_code: str,
        prompt: str,
        response_model: type[ResultT],
        system_prompt: str = "你是业务数据规划助手，只生成业务语义，不生成数据库事实。",
        contains_business_data: bool = False,
        business_id: str | None = None,
    ) -> ResultT:
        binding = await AiFeatureBindingService(self.auth, self.redis, scope=self.scope).get(feature_code)
        if not binding.get("enabled"):
            raise CustomException(msg="该 AI 功能尚未启用", code=10403, status_code=403)
        primary = await self._runtime_config(binding.get("model_config_id"), source="feature_primary")
        candidates = [primary]
        if binding.get("fallback_config_id"):
            candidates.append(await self._runtime_config(binding["fallback_config_id"], source="feature_fallback"))

        started_at = perf_counter()
        last_error: Exception | None = None
        used_config = primary
        for model_config in candidates:
            used_config = model_config
            try:
                self._assert_business_data_policy(binding, model_config, contains_business_data)
                timeout_seconds = min(float(binding["timeout_seconds"]), float(model_config.get("timeout_seconds") or 60))
                raw = await asyncio.wait_for(
                    self.runner(
                        model_config={**model_config, "timeout_seconds": timeout_seconds},
                        prompt=prompt,
                        system_prompt=system_prompt,
                        response_model=response_model,
                    ),
                    timeout=timeout_seconds,
                )
                result = _validate_structured_result(response_model, raw)
                await record_ai_call_audit(
                    self.redis,
                    AiCallAuditRecord(
                        user_id=self._user_id,
                        tenant_id=self.auth.tenant_id,
                        message=prompt,
                        model_config=model_config,
                        feature_code=feature_code,
                        business_id=business_id,
                        prompt_key=binding["prompt_key"],
                        prompt_version=binding["prompt_version"],
                        duration_ms=int((perf_counter() - started_at) * 1000),
                        status="success",
                    ),
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc

        await record_ai_call_audit(
            self.redis,
            AiCallAuditRecord(
                user_id=self._user_id,
                tenant_id=self.auth.tenant_id,
                message=prompt,
                model_config=used_config,
                feature_code=feature_code,
                business_id=business_id,
                prompt_key=binding["prompt_key"],
                prompt_version=binding["prompt_version"],
                duration_ms=int((perf_counter() - started_at) * 1000),
                status="failed",
                error=type(last_error).__name__ if last_error else "UnknownError",
            ),
        )
        if isinstance(last_error, CustomException):
            raise last_error
        raise CustomException(msg="AI 结构化生成失败，产品应降级使用规则结果", code=10503, status_code=503)
