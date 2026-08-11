from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.plugin.module_ai.chat import service as chat_service_module
from app.plugin.module_ai.chat.audit import AiCallAuditRecord
from app.plugin.module_ai.chat.registry import default_ai_registry
from app.plugin.module_ai.chat.schema import AiFeatureBindingUpdateSchema, AiModelConfigSchema, AiModelConfigUpdateSchema
from app.plugin.module_ai.chat.service import AiFeatureBindingService, AiModelConfigService, AiRuntimeService, ChatService, get_user_model_config, resolve_effective_model_config


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, name: str) -> bytes | None:
        return self.store.get(name)

    async def set(self, name: str, value: bytes, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            if self.store.pop(name, None) is not None:
                deleted += 1
        return deleted


def make_auth() -> AuthSchema:
    return AuthSchema(
        user=SimpleNamespace(id=9001, username="alice", dept_id=7),
        tenant_id=42,
    )


@pytest.mark.asyncio
async def test_model_config_responses_do_not_expose_api_key_and_update_preserves_existing_key() -> None:
    redis = MemoryRedis()
    service = AiModelConfigService(make_auth(), redis)  # type: ignore[arg-type]

    created = await service.create(
        AiModelConfigSchema(
            name="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-secret-123456",
            model_id="deepseek-chat",
            temperature=0.5,
        )
    )

    assert "api_key" not in created
    assert created["has_api_key"] is True
    assert created["api_key_masked"] != "sk-secret-123456"

    listed = await service.list()
    item = listed["items"][0]
    assert "api_key" not in item
    assert item["api_key_masked"] == created["api_key_masked"]

    updated = await service.update(
        created["id"],
        AiModelConfigUpdateSchema(
            name="Renamed",
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-reasoner",
            temperature=0.2,
        ),
    )

    assert "api_key" not in updated
    runtime_config = await get_user_model_config(redis, 9001)  # type: ignore[arg-type]
    assert runtime_config is not None
    assert runtime_config["api_key"] == "sk-secret-123456"
    assert runtime_config["model_id"] == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_rest_chat_uses_active_runtime_model_config(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = MemoryRedis()
    auth = make_auth()
    model_service = AiModelConfigService(auth, redis)  # type: ignore[arg-type]
    created = await model_service.create(
        AiModelConfigSchema(
            name="Active",
            base_url="https://active.example/v1",
            api_key="sk-active",
            model_id="active-model",
            temperature=0.1,
        )
    )

    captured: dict[str, object] = {}

    class FakeCRUD:
        def __init__(self, auth: AuthSchema) -> None:
            self.db = object()

    class FakeAgent:
        async def arun(self, input: str):
            return SimpleNamespace(content="ok")

    class FakeAgnoFactory:
        def create_agent(self, **kwargs):
            captured.update(kwargs)
            return FakeAgent()

    async def fake_record(*args, **kwargs) -> None:
        captured["audit_called"] = True

    monkeypatch.setattr(chat_service_module, "ChatSessionCRUD", FakeCRUD)
    monkeypatch.setattr(chat_service_module, "AgnoFactory", FakeAgnoFactory)
    monkeypatch.setattr(chat_service_module, "record_ai_call_audit", fake_record)

    result = await ChatService(auth).chat_non_stream("hello", "session-1", redis=redis)  # type: ignore[arg-type]

    assert result["response"] == "ok"
    assert captured["model_config"] == {
        "base_url": "https://active.example/v1",
        "api_key": "sk-active",
        "model_id": "active-model",
        "temperature": 0.1,
        "provider_type": "openai_compatible",
        "timeout_seconds": 60,
        "max_tokens": 4096,
        "allow_business_data": False,
        "config_id": created["id"],
        "source": "user_active",
        "tenant_id": 42,
        "user_id": 9001,
    }
    assert captured["audit_called"] is True


@pytest.mark.asyncio
async def test_effective_model_config_falls_back_to_system_default() -> None:
    resolved = await resolve_effective_model_config(MemoryRedis(), make_auth())  # type: ignore[arg-type]

    assert resolved["source"] == "system_default"
    assert resolved["tenant_id"] == 42
    assert resolved["user_id"] == 9001
    assert "api_key" in resolved


def test_prompt_and_action_registry_has_minimal_default_entries() -> None:
    prompt = default_ai_registry.get_prompt("chat.default")
    action = default_ai_registry.get_action("navigate.system")

    assert prompt is not None
    assert "中文" in prompt.instructions
    assert action is not None
    assert action.kind == "navigate"


def test_ai_call_audit_record_redacts_api_key() -> None:
    record = AiCallAuditRecord(
        event_id="event-1",
        user_id=9001,
        tenant_id=42,
        session_id="session-1",
        message="hello",
        model_config={"model_id": "active-model", "api_key": "sk-secret"},
        status="success",
    )

    dumped = record.to_safe_dict()
    assert dumped["model_config"]["model_id"] == "active-model"
    assert "api_key" not in dumped["model_config"]


def test_demo_data_feature_and_model_safety_defaults_are_registered() -> None:
    feature = default_ai_registry.get_feature("demo_data.blueprint")
    config = AiModelConfigSchema(
        name="Safe default",
        base_url="https://example.com/v1",
        api_key="sk-secret",
        model_id="demo-model",
    )

    assert feature is not None
    assert feature.prompt_key == "demo_data.blueprint"
    assert config.timeout_seconds == 60
    assert config.max_tokens == 4096
    assert config.allow_business_data is False


@pytest.mark.asyncio
async def test_feature_binding_is_scoped_and_rejects_unknown_model() -> None:
    redis = MemoryRedis()
    auth = make_auth()
    model_service = AiModelConfigService(auth, redis)  # type: ignore[arg-type]
    created = await model_service.create(
        AiModelConfigSchema(
            name="Business model",
            base_url="https://example.com/v1",
            api_key="sk-secret",
            model_id="demo-model",
            allow_business_data=True,
        )
    )
    service = AiFeatureBindingService(auth, redis)  # type: ignore[arg-type]

    saved = await service.upsert(
        "demo_data.blueprint",
        AiFeatureBindingUpdateSchema(
            model_config_id=created["id"],
            prompt_version="v2",
            timeout_seconds=30,
            allow_business_data=True,
            enabled=True,
        ),
    )

    assert saved["feature_code"] == "demo_data.blueprint"
    assert saved["model_config_id"] == created["id"]
    assert saved["prompt_key"] == "demo_data.blueprint"
    assert saved["prompt_version"] == "v2"
    assert "api_key" not in saved

    other_auth = AuthSchema(user=SimpleNamespace(id=9002, username="bob", dept_id=8), tenant_id=99)
    assert (await AiFeatureBindingService(other_auth, redis).list())[0]["model_config_id"] is None  # type: ignore[arg-type]

    with pytest.raises(CustomException, match="模型配置不存在"):
        await service.upsert(
            "demo_data.blueprint",
            AiFeatureBindingUpdateSchema(model_config_id="missing", enabled=True),
        )


class DemoBlueprintResult(BaseModel):
    title: str
    count: int


@pytest.mark.asyncio
async def test_structured_runtime_validates_output_and_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = MemoryRedis()
    auth = make_auth()
    model_service = AiModelConfigService(auth, redis)  # type: ignore[arg-type]
    primary = await model_service.create(
        AiModelConfigSchema(
            name="Primary",
            base_url="https://primary.example/v1",
            api_key="sk-primary",
            model_id="primary-model",
            allow_business_data=True,
        )
    )
    fallback = await model_service.create(
        AiModelConfigSchema(
            name="Fallback",
            base_url="https://fallback.example/v1",
            api_key="sk-fallback",
            model_id="fallback-model",
            allow_business_data=True,
        )
    )
    await AiFeatureBindingService(auth, redis).upsert(  # type: ignore[arg-type]
        "demo_data.blueprint",
        AiFeatureBindingUpdateSchema(
            model_config_id=primary["id"],
            fallback_config_id=fallback["id"],
            allow_business_data=True,
            enabled=True,
        ),
    )
    calls: list[str] = []
    audits: list[AiCallAuditRecord] = []

    async def runner(*, model_config: dict[str, object], **kwargs: object) -> dict[str, object]:
        calls.append(str(model_config["model_id"]))
        if model_config["model_id"] == "primary-model":
            raise RuntimeError("provider unavailable")
        return {"title": "电气设备演示", "count": 12}

    async def capture_audit(redis_arg: object, record: AiCallAuditRecord) -> None:
        audits.append(record)

    monkeypatch.setattr(chat_service_module, "record_ai_call_audit", capture_audit)
    result = await AiRuntimeService(auth, redis, runner=runner).structured_generate(  # type: ignore[arg-type]
        feature_code="demo_data.blueprint",
        prompt="客户名称与产品规格",
        response_model=DemoBlueprintResult,
        contains_business_data=True,
        business_id="batch-1",
    )

    assert result == DemoBlueprintResult(title="电气设备演示", count=12)
    assert calls == ["primary-model", "fallback-model"]
    safe_audit = audits[-1].to_safe_dict()
    assert safe_audit["feature_code"] == "demo_data.blueprint"
    assert safe_audit["business_id"] == "batch-1"
    assert "客户名称" not in str(safe_audit)
    assert safe_audit["prompt_fingerprint"]


@pytest.mark.asyncio
async def test_structured_runtime_denies_business_data_without_two_level_consent() -> None:
    redis = MemoryRedis()
    auth = make_auth()
    model_service = AiModelConfigService(auth, redis)  # type: ignore[arg-type]
    created = await model_service.create(
        AiModelConfigSchema(
            name="No business data",
            base_url="https://example.com/v1",
            api_key="sk-secret",
            model_id="demo-model",
            allow_business_data=False,
        )
    )
    await AiFeatureBindingService(auth, redis).upsert(  # type: ignore[arg-type]
        "demo_data.blueprint",
        AiFeatureBindingUpdateSchema(
            model_config_id=created["id"],
            allow_business_data=True,
            enabled=True,
        ),
    )

    with pytest.raises(CustomException, match="业务数据"):
        await AiRuntimeService(auth, redis, runner=lambda **kwargs: None).structured_generate(  # type: ignore[arg-type]
            feature_code="demo_data.blueprint",
            prompt="sensitive",
            response_model=DemoBlueprintResult,
            contains_business_data=True,
        )
