"""服务端受控的业务任务处理器注册表。"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config.setting import settings

Handler = Callable[[Any, Any], Awaitable[dict | None]]
PayloadValidator = Callable[[dict], Any]

_HANDLER_CODE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class DuplicateBusinessTaskHandlerError(RuntimeError):
    """两个模块注册了同一个 handler_code。"""


class UnknownBusinessTaskHandlerError(LookupError):
    """handler_code 未在服务端注册。"""


@dataclass(frozen=True, slots=True)
class BusinessTaskDefinition:
    handler_code: str
    handler: Handler
    module: str
    default_queue: str
    max_retries: int
    soft_time_limit: int
    hard_time_limit: int
    supports_cancel: bool
    payload_schema: type[BaseModel] | None
    payload_validator: PayloadValidator | None
    retryable_exceptions: tuple[type[Exception], ...]
    required_permissions: tuple[str, ...]
    source: str

    def validate_payload(self, payload: dict | None) -> Any:
        value = payload or {}
        if self.payload_schema is not None:
            return self.payload_schema.model_validate(value)
        if self.payload_validator is not None:
            return self.payload_validator(value)
        return TypeAdapter(dict).validate_python(value)


class BusinessTaskRegistry:
    """进程内只读式 handler 注册表；Worker 启动时一次性装载。"""

    def __init__(self) -> None:
        self._definitions: dict[str, BusinessTaskDefinition] = {}

    def register(
        self,
        *,
        handler_code: str,
        handler: Handler,
        module: str,
        default_queue: str | None = None,
        max_retries: int | None = None,
        soft_time_limit: int | None = None,
        hard_time_limit: int | None = None,
        supports_cancel: bool = False,
        payload_schema: type[BaseModel] | None = None,
        payload_validator: PayloadValidator | None = None,
        retryable_exceptions: tuple[type[Exception], ...] = (),
        required_permissions: tuple[str, ...] = (),
        source: str | None = None,
    ) -> BusinessTaskDefinition:
        code = handler_code.strip()
        if not _HANDLER_CODE.fullmatch(code):
            raise ValueError("handler_code 必须是稳定的小写点分机器编码")
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(f"业务任务处理器必须是 async 函数: {code}")
        if payload_schema is not None and payload_validator is not None:
            raise ValueError(f"处理器 {code} 不能同时声明 payload_schema 和 payload_validator")
        origin = source or f"{handler.__module__}.{handler.__qualname__}"
        existing = self._definitions.get(code)
        if existing:
            raise DuplicateBusinessTaskHandlerError(
                f"handler_code {code} 注册冲突: {existing.source} 与 {origin}"
            )
        definition = BusinessTaskDefinition(
            handler_code=code,
            handler=handler,
            module=module.strip(),
            default_queue=(default_queue or settings.CELERY_DEFAULT_QUEUE).strip(),
            max_retries=settings.CELERY_MAX_RETRIES if max_retries is None else max_retries,
            soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT if soft_time_limit is None else soft_time_limit,
            hard_time_limit=settings.CELERY_TASK_TIME_LIMIT if hard_time_limit is None else hard_time_limit,
            supports_cancel=supports_cancel,
            payload_schema=payload_schema,
            payload_validator=payload_validator,
            retryable_exceptions=retryable_exceptions,
            required_permissions=required_permissions,
            source=origin,
        )
        if not definition.module or not definition.default_queue:
            raise ValueError(f"处理器 {code} 缺少 module 或 default_queue")
        if definition.max_retries < 0:
            raise ValueError(f"处理器 {code} 的 max_retries 不能小于 0")
        self._definitions[code] = definition
        return definition

    def get(self, handler_code: str) -> BusinessTaskDefinition:
        try:
            return self._definitions[handler_code]
        except KeyError:
            raise UnknownBusinessTaskHandlerError(f"未注册业务任务处理器: {handler_code}") from None

    def all(self) -> tuple[BusinessTaskDefinition, ...]:
        return tuple(self._definitions[code] for code in sorted(self._definitions))

    def clear(self) -> None:
        self._definitions.clear()


business_task_registry = BusinessTaskRegistry()


def register_business_task(**options):
    """供插件任务模块使用的显式装饰器扩展点。"""

    def decorator(handler: Handler) -> Handler:
        business_task_registry.register(handler=handler, **options)
        return handler

    return decorator


__all__ = [
    "BusinessTaskDefinition",
    "BusinessTaskRegistry",
    "DuplicateBusinessTaskHandlerError",
    "UnknownBusinessTaskHandlerError",
    "ValidationError",
    "business_task_registry",
    "register_business_task",
]
