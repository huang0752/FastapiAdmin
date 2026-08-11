from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromptSpec:
    key: str
    name: str
    instructions: str
    version: str = "1.0.0"


@dataclass(frozen=True)
class ActionSpec:
    key: str
    name: str
    kind: str
    description: str
    payload_schema: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureSpec:
    code: str
    name: str
    description: str
    prompt_key: str
    default_timeout_seconds: int = 60


class AiRegistry:
    """Prompt/智能动作注册中心的轻量内存抽象。"""

    def __init__(self) -> None:
        self._prompts: dict[str, PromptSpec] = {}
        self._actions: dict[str, ActionSpec] = {}
        self._features: dict[str, FeatureSpec] = {}

    def register_prompt(self, spec: PromptSpec) -> None:
        self._prompts[spec.key] = spec

    def register_action(self, spec: ActionSpec) -> None:
        self._actions[spec.key] = spec

    def get_prompt(self, key: str) -> PromptSpec | None:
        return self._prompts.get(key)

    def get_action(self, key: str) -> ActionSpec | None:
        return self._actions.get(key)

    def list_prompts(self) -> list[PromptSpec]:
        return list(self._prompts.values())

    def list_actions(self) -> list[ActionSpec]:
        return list(self._actions.values())

    def register_feature(self, spec: FeatureSpec) -> None:
        self._features[spec.code] = spec

    def get_feature(self, code: str) -> FeatureSpec | None:
        return self._features.get(code)

    def list_features(self) -> list[FeatureSpec]:
        return list(self._features.values())


default_ai_registry = AiRegistry()
default_ai_registry.register_prompt(
    PromptSpec(
        key="chat.default",
        name="默认对话 Prompt",
        instructions="使用中文回答，保持简洁明了；如果不确定，请说明。",
    )
)
default_ai_registry.register_action(
    ActionSpec(
        key="navigate.system",
        name="系统页面导航",
        kind="navigate",
        description="建议前端跳转到系统管理相关页面。",
        payload_schema={
            "type": "object",
            "required": ["path", "name"],
            "properties": {
                "path": {"type": "string"},
                "name": {"type": "string"},
            },
        },
    )
)
default_ai_registry.register_feature(
    FeatureSpec(
        code="demo_data.blueprint",
        name="演示数据蓝图增强",
        description="只增强经过产品模块筛选后的业务语义，不直接写业务表。",
        prompt_key="demo_data.blueprint",
        default_timeout_seconds=60,
    )
)
