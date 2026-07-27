"""产品能力装配配置。

框架只理解通用能力，不理解 WMS/ERP/MES 等业务语义。产品仓库通过
assembly TOML 声明启用的插件、前端路由组、seed pack 和功能开关。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config.path_conf import ASSEMBLY_DIR, BASE_DIR, PLUGIN_DIR
from app.config.setting import settings
from app.core.logger import logger

DEFAULT_CORE_MODULES = [
    "auth",
    "tenant",
    "user",
    "role",
    "permission",
    "menu",
    "settings",
    "audit",
]

PLUGIN_CODE_ALIASES: dict[str, tuple[str, ...]] = {
    "module_ai": ("ai", "ai_assistant"),
    "module_generator": ("generator", "code_generator"),
    "module_task": ("task", "workflow_engine"),
    "module_example": ("example",),
}

MENU_ROUTE_GROUP_ALIASES: dict[str, str] = {
    "ai": "ai-chat",
    "generator": "module-generator",
    "task": "module-task",
}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise TypeError(f"期望字符串列表，实际为 {type(value).__name__}")


def _normalize_module_code(code: str) -> str:
    code = code.strip()
    if not code:
        return code
    return code if code.startswith("module_") else f"module_{code}"


def known_plugin_module_codes(plugin_dir: Path | None = None) -> list[str]:
    """返回框架已知的插件模块编码。

    内置别名用于兼容历史 seed 中的插件编码；目录扫描让第三方
    ``module_*`` 插件无需修改框架常量即可参与 assembly 裁剪。
    """
    module_codes = set(PLUGIN_CODE_ALIASES)
    directory = plugin_dir or PLUGIN_DIR
    if directory.is_dir():
        module_codes.update(
            path.name
            for path in directory.iterdir()
            if path.is_dir() and path.name.startswith("module_") and len(path.name) > len("module_")
        )
    return sorted(module_codes)


def plugin_code_candidates(module_code: str) -> list[str]:
    normalized = _normalize_module_code(module_code)
    candidates = [normalized, *PLUGIN_CODE_ALIASES.get(normalized, ())]
    short_code = normalized.removeprefix("module_")
    if short_code and short_code not in candidates:
        candidates.append(short_code)
    return candidates


def _to_camel_case(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


@dataclass(frozen=True)
class AssemblyConfig:
    """当前产品能力装配。"""

    name: str = "default"
    title: str = "默认完整装配"
    description: str = "保持框架默认完整行为"
    source: str = "built-in"
    core_required: list[str] = field(default_factory=lambda: list(DEFAULT_CORE_MODULES))
    enabled_plugins: list[str] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)
    enabled_route_groups: list[str] = field(default_factory=list)
    disabled_route_groups: list[str] = field(default_factory=list)
    seed_packs: list[str] = field(default_factory=lambda: ["legacy"])
    feature_flags: dict[str, bool] = field(default_factory=dict)

    def is_plugin_enabled(self, code: str) -> bool:
        module_code = _normalize_module_code(code)
        disabled = {_normalize_module_code(item) for item in self.disabled_plugins}
        if module_code in disabled:
            return False
        enabled = {_normalize_module_code(item) for item in self.enabled_plugins}
        return not enabled or module_code in enabled

    def is_route_group_enabled(self, route_group: str | None) -> bool:
        if not route_group:
            return True
        group = route_group.strip()
        if group in set(self.disabled_route_groups):
            return False
        enabled = set(self.enabled_route_groups)
        return not enabled or group in enabled

    def is_feature_enabled(self, feature: str, default: bool = True) -> bool:
        value = self.feature_flags.get(feature)
        return value if isinstance(value, bool) else default

    def is_menu_item_enabled(self, item: dict[str, Any]) -> bool:
        permission = str(item.get("permission") or "")
        component_path = str(item.get("component_path") or "")
        route_group = self._menu_route_group(item)

        for module_code in self.disabled_plugins:
            for candidate in plugin_code_candidates(module_code):
                if permission.startswith(f"{candidate}:"):
                    return False
                if component_path.startswith(f"{candidate}/"):
                    return False

        return self.is_route_group_enabled(route_group)

    def filter_menu_tree(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for item in items:
            if not self.is_menu_item_enabled(item):
                continue

            next_item = dict(item)
            children = next_item.get("children") or []
            if children:
                next_item["children"] = self.filter_menu_tree(children)

            had_children = bool(children)
            is_empty_catalog = had_children and not next_item.get("children") and not next_item.get("component_path")
            if is_empty_catalog:
                continue
            filtered.append(next_item)
        return filtered

    def _menu_route_group(self, item: dict[str, Any]) -> str | None:
        raw_path = str(item.get("route_path") or "").strip()
        if not raw_path.startswith("/"):
            return None
        first_segment = raw_path.strip("/").split("/", 1)[0]
        if not first_segment:
            return None
        normalized = first_segment.replace("_", "-")
        return MENU_ROUTE_GROUP_ALIASES.get(normalized, normalized)

    def frontend_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "enabledRouteGroups": self.enabled_route_groups,
            "disabledRouteGroups": self.disabled_route_groups,
            "featureFlags": {_to_camel_case(k): v for k, v in self.feature_flags.items()},
        }

    def log_summary(self) -> None:
        logger.info("✅ Assembly: {} ({})", self.name, self.source)
        logger.info("✅ Core modules: {}", ",".join(self.core_required) or "-")
        logger.info("✅ Enabled plugins: {}", ",".join(self.enabled_plugins) or "all")
        logger.info("⏭️ Disabled plugins: {}", ",".join(self.disabled_plugins) or "-")
        logger.info("✅ Seed packs: {}", ",".join(self.seed_packs) or "legacy")
        logger.info("✅ Enabled route groups: {}", ",".join(self.enabled_route_groups) or "all")
        logger.info("⏭️ Disabled route groups: {}", ",".join(self.disabled_route_groups) or "-")


def _assembly_path_from_settings() -> Path:
    if settings.APP_ASSEMBLY_FILE:
        path = Path(settings.APP_ASSEMBLY_FILE)
        return path if path.is_absolute() else BASE_DIR / path
    return ASSEMBLY_DIR / f"{settings.APP_ASSEMBLY}.toml"


def load_assembly_from_file(path: Path) -> AssemblyConfig:
    """从 TOML 文件加载能力装配。"""
    with path.open("rb") as f:
        raw = tomllib.load(f)

    assembly = raw.get("assembly", {})
    core = raw.get("core", {})
    backend = raw.get("backend", {})
    frontend = raw.get("frontend", {})
    seed = raw.get("seed", {})
    features = raw.get("features", {})

    flags_raw = features.get("flags", {})
    if flags_raw is None:
        flags_raw = {}
    if not isinstance(flags_raw, dict):
        raise TypeError("[features].flags 必须是键值对象")

    core_required = _string_list(core.get("required")) or list(DEFAULT_CORE_MODULES)

    return AssemblyConfig(
        name=str(assembly.get("name") or path.stem),
        title=str(assembly.get("title") or path.stem),
        description=str(assembly.get("description") or ""),
        source=str(path),
        core_required=core_required,
        enabled_plugins=_string_list(backend.get("enabled_plugins")),
        disabled_plugins=_string_list(backend.get("disabled_plugins")),
        enabled_route_groups=_string_list(frontend.get("enabled_route_groups")),
        disabled_route_groups=_string_list(frontend.get("disabled_route_groups")),
        seed_packs=_string_list(seed.get("packs")) or ["legacy"],
        feature_flags={str(k): bool(v) for k, v in flags_raw.items()},
    )


@lru_cache(maxsize=1)
def get_assembly() -> AssemblyConfig:
    """获取当前能力装配。缺省 default 不存在时回退到内置完整装配。"""
    path = _assembly_path_from_settings()
    if not path.exists():
        if settings.APP_ASSEMBLY == "default" and not settings.APP_ASSEMBLY_FILE:
            logger.warning("⚠️ 默认 assembly 文件不存在，使用内置完整装配: {}", path)
            return AssemblyConfig()
        raise FileNotFoundError(f"能力装配文件不存在: {path}")
    return load_assembly_from_file(path)


def reset_assembly_cache() -> None:
    """测试或热更新场景清空装配缓存。"""
    get_assembly.cache_clear()


def is_plugin_enabled(code: str) -> bool:
    return get_assembly().is_plugin_enabled(code)


def get_frontend_assembly_summary() -> dict[str, Any]:
    return get_assembly().frontend_summary()


def filter_menu_tree_by_assembly(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return get_assembly().filter_menu_tree(items)
