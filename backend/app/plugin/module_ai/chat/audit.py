import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from app.config.setting import settings
from app.core.logger import logger
from app.core.redis_crud import RedisCURD

AI_CALL_AUDIT_KEY_PREFIX = "ai_call_audit"


def _redact_model_config(model_config: dict[str, Any] | None) -> dict[str, Any]:
    if not model_config:
        return {}
    secret_markers = ("api_key", "secret", "password", "token")
    return {key: value for key, value in model_config.items() if not any(marker in key.lower() for marker in secret_markers)}


def prompt_fingerprint(message: str | None) -> str | None:
    if not message:
        return None
    key = (settings.AI_AUDIT_HASH_KEY or settings.SECRET_KEY).encode("utf-8")
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass
class AiCallAuditRecord:
    """AI 调用审计记录的最小服务层结构。"""

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    user_id: int | None = None
    tenant_id: int | None = None
    session_id: str | None = None
    message: str | None = None
    model_config: dict[str, Any] | None = None
    feature_code: str | None = None
    business_id: str | None = None
    prompt_key: str | None = None
    prompt_version: str | None = None
    duration_ms: int | None = None
    status: str = "unknown"
    error: str | None = None
    created_time: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "message_size": len(self.message or ""),
            "prompt_fingerprint": prompt_fingerprint(self.message),
            "model_config": _redact_model_config(self.model_config),
            "feature_code": self.feature_code,
            "business_id": self.business_id,
            "prompt_key": self.prompt_key,
            "prompt_version": self.prompt_version,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "created_time": self.created_time,
        }


async def record_ai_call_audit(redis: Redis | None, record: AiCallAuditRecord) -> None:
    """写入最小审计落点；Redis 不可用时降级为结构化日志。"""
    safe_data = record.to_safe_dict()
    if redis is None:
        logger.info("AI 调用审计: {}", safe_data)
        return
    key = f"{AI_CALL_AUDIT_KEY_PREFIX}:{record.tenant_id or 0}:{record.user_id or 0}:{record.event_id}"
    ok = await RedisCURD(redis).set(key, json.dumps(safe_data, ensure_ascii=False), expire=7 * 86400)
    if not ok:
        logger.warning("AI 调用审计写入失败: {}", safe_data)
