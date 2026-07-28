from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.module_platform.package.service import PackageService
from app.config.setting import settings
from app.core.assembly import (
    AssemblyConfig,
    known_plugin_module_codes,
    load_assembly_from_file,
    plugin_code_candidates,
    reset_assembly_cache,
)
from app.scripts.initialize import InitializeData


def test_load_assembly_from_toml(tmp_path: Path) -> None:
    config = tmp_path / "demo.toml"
    config.write_text(
        """
[assembly]
name = "demo"
title = "Demo"

[backend]
disabled_plugins = ["module_example"]

[frontend]
enabled_route_groups = ["home", "system"]
disabled_route_groups = ["pricing"]

[seed]
packs = ["minimal"]

[features]
flags = { ai_assistant = false }
""",
        encoding="utf-8",
    )

    assembly = load_assembly_from_file(config)

    assert assembly.name == "demo"
    assert not assembly.is_plugin_enabled("module_example")
    assert assembly.is_plugin_enabled("module_task")
    assert assembly.is_route_group_enabled("home")
    assert not assembly.is_route_group_enabled("pricing")
    assert assembly.seed_packs == ["minimal"]
    assert assembly.frontend_summary()["featureFlags"]["aiAssistant"] is False


def test_plugin_module_codes_are_discovered_from_plugin_directory(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    (plugin_dir / "module_ai").mkdir(parents=True)
    (plugin_dir / "module_custom").mkdir()
    (plugin_dir / "common").mkdir()
    (plugin_dir / "module_file.py").write_text("", encoding="utf-8")

    module_codes = known_plugin_module_codes(plugin_dir)

    assert "module_ai" in module_codes
    assert "module_custom" in module_codes
    assert "common" not in module_codes
    assert "module_file.py" not in module_codes
    assert plugin_code_candidates("module_custom") == ["module_custom", "custom"]


def test_disabled_plugin_is_not_registered_in_dynamic_router(tmp_path: Path) -> None:
    config = tmp_path / "minimal.toml"
    config.write_text(
        """
[assembly]
name = "minimal-test"

[backend]
disabled_plugins = ["module_example"]

[seed]
packs = ["legacy"]
""",
        encoding="utf-8",
    )

    old_file = settings.APP_ASSEMBLY_FILE
    old_name = settings.APP_ASSEMBLY
    settings.APP_ASSEMBLY_FILE = str(config)
    settings.APP_ASSEMBLY = "minimal-test"
    reset_assembly_cache()

    import app.core.discover as discover

    old_router = discover._dynamic_router_cache
    discover._dynamic_router_cache = None
    try:
        router = discover.get_dynamic_router()
        paths = {getattr(route, "path", "") for route in router.routes}
        assert not any(path.startswith("/example") for path in paths)
        assert any(path.startswith("/task") for path in paths)
    finally:
        discover._dynamic_router_cache = old_router
        settings.APP_ASSEMBLY_FILE = old_file
        settings.APP_ASSEMBLY = old_name
        reset_assembly_cache()


def test_public_config_info_returns_assembly(test_client: TestClient) -> None:
    response = test_client.get("/system/config/info")
    assert response.status_code == 200
    payload = response.json()["data"]["assembly"]
    assert payload["name"]
    assert isinstance(payload["enabledRouteGroups"], list)
    assert isinstance(payload["disabledRouteGroups"], list)
    assert isinstance(payload["featureFlags"], dict)


def test_assembly_filters_runtime_menu_tree_by_plugin_and_route_group() -> None:
    assembly = AssemblyConfig(
        disabled_plugins=["module_example"],
        enabled_route_groups=["system", "module-task"],
        disabled_route_groups=["monitor", "swagger"],
    )
    menu_tree = [
        {"title": "系统管理", "route_path": "/system", "children": []},
        {"title": "监控管理", "route_path": "/monitor", "children": []},
        {"title": "接口管理", "route_path": "/swagger", "children": []},
        {
            "title": "任务管理",
            "route_path": "/task",
            "children": [{"title": "业务任务", "route_path": "business/task"}],
        },
        {
            "title": "案例管理",
            "route_path": "/example",
            "children": [
                {
                    "title": "demo示例",
                    "route_path": "demo",
                    "component_path": "module_example/demo/index",
                    "permission": "module_example:demo:query",
                }
            ],
        },
    ]

    filtered = assembly.filter_menu_tree(menu_tree)

    titles = {item["title"] for item in filtered}
    assert titles == {"系统管理", "任务管理"}
    task = next(item for item in filtered if item["title"] == "任务管理")
    assert task["children"] == [{"title": "业务任务", "route_path": "business/task"}]


def test_menu_route_name_allowlist_supports_platform_and_tenant_audiences() -> None:
    assembly = AssemblyConfig(
        included_menu_route_names=["Tenant", "Dept"],
        platform_menu_route_names=["Tenant"],
        tenant_menu_route_names=["Dept"],
    )
    menu_tree = [
        {
            "route_name": "Platform",
            "route_path": "/platform",
            "children": [
                {
                    "route_name": "Tenant",
                    "route_path": "tenant",
                    "children": [{"permission": "module_platform:tenant:create"}],
                },
                {"route_name": "PlatformOrder", "route_path": "order"},
            ],
        },
        {
            "route_name": "System",
            "route_path": "/system",
            "children": [
                {"route_name": "Dept", "route_path": "dept"},
                {"route_name": "Dict", "route_path": "dict"},
            ],
        },
    ]

    filtered = assembly.filter_menu_tree(menu_tree)
    platform_filtered = assembly.filter_menu_tree(menu_tree, audience="platform")
    tenant_filtered = assembly.filter_menu_tree(menu_tree, audience="tenant")

    assert [item["route_name"] for item in filtered] == ["Platform", "System"]
    assert [item["route_name"] for item in filtered[0]["children"]] == ["Tenant"]
    assert filtered[0]["children"][0]["children"] == [
        {"permission": "module_platform:tenant:create"}
    ]
    assert [item["route_name"] for item in filtered[1]["children"]] == ["Dept"]
    assert [item["route_name"] for item in platform_filtered] == ["Platform"]
    assert [item["route_name"] for item in tenant_filtered] == ["System"]


def test_tenant_workspace_feature_flag_removes_workspace_owner_permissions(tmp_path: Path) -> None:
    config = tmp_path / "no-workspace.toml"
    config.write_text(
        """
[assembly]
name = "no-workspace"

[features]
flags = { tenant_workspace = false }
""",
        encoding="utf-8",
    )
    old_file = settings.APP_ASSEMBLY_FILE
    old_name = settings.APP_ASSEMBLY
    settings.APP_ASSEMBLY_FILE = str(config)
    settings.APP_ASSEMBLY = "no-workspace"
    reset_assembly_cache()
    try:
        required = PackageService.owner_required_menu_permissions()
        assert "module_platform:workspace:query" not in required
        assert "module_platform:workspace:update" not in required
        assert "module_system:dept:query" in required
    finally:
        settings.APP_ASSEMBLY_FILE = old_file
        settings.APP_ASSEMBLY = old_name
        reset_assembly_cache()


@pytest.mark.asyncio
async def test_seed_data_filters_disabled_plugin_menus_and_relations() -> None:
    old_file = settings.APP_ASSEMBLY_FILE
    old_name = settings.APP_ASSEMBLY
    settings.APP_ASSEMBLY_FILE = "app/assemblies/business-admin.toml"
    settings.APP_ASSEMBLY = "business-admin"
    reset_assembly_cache()

    try:
        initializer = InitializeData()

        menus = await initializer._InitializeData__load_json("platform_menu")
        flat_menus = _flatten_menu_seed(menus)
        assert not any(str(item.get("permission") or "").startswith("module_ai:") for item in flat_menus)
        assert not any(str(item.get("permission") or "").startswith("module_example:") for item in flat_menus)
        assert not any(str(item.get("component_path") or "").startswith("module_ai/") for item in flat_menus)
        assert not any(str(item.get("component_path") or "").startswith("module_example/") for item in flat_menus)
        assert any(str(item.get("component_path") or "").startswith("module_generator/") for item in flat_menus)

        plugins = await initializer._InitializeData__load_json("platform_plugin")
        plugin_codes = {item["code"] for item in plugins}
        assert "ai_assistant" not in plugin_codes
        assert "code_generator" in plugin_codes
        assert "workflow_engine" in plugin_codes

        package_plugins = await initializer._InitializeData__load_json("platform_package_plugin")
        tenant_plugins = await initializer._InitializeData__load_json("platform_tenant_plugin")
        assert all(item["plugin_id"] <= len(plugins) for item in package_plugins)
        assert all(item["plugin_id"] <= len(plugins) for item in tenant_plugins)
    finally:
        settings.APP_ASSEMBLY_FILE = old_file
        settings.APP_ASSEMBLY = old_name
        reset_assembly_cache()


@pytest.mark.asyncio
async def test_seed_menu_filters_disabled_route_groups_without_disabled_plugins(tmp_path: Path) -> None:
    config = tmp_path / "route-group-only.toml"
    config.write_text(
        """
[assembly]
name = "route-group-only"

[backend]
disabled_plugins = []

[frontend]
enabled_route_groups = ["system", "platform"]
disabled_route_groups = ["monitor", "swagger"]

[seed]
packs = ["legacy"]
""",
        encoding="utf-8",
    )
    old_file = settings.APP_ASSEMBLY_FILE
    old_name = settings.APP_ASSEMBLY
    settings.APP_ASSEMBLY_FILE = str(config)
    settings.APP_ASSEMBLY = "route-group-only"
    reset_assembly_cache()

    try:
        initializer = InitializeData()
        menus = await initializer._InitializeData__load_json("platform_menu")
        top_paths = {item.get("route_path") for item in menus}
        assert "/monitor" not in top_paths
        assert "/swagger" not in top_paths
    finally:
        settings.APP_ASSEMBLY_FILE = old_file
        settings.APP_ASSEMBLY = old_name
        reset_assembly_cache()


def _flatten_menu_seed(items: list[dict]) -> list[dict]:
    result: list[dict] = []
    for item in items:
        result.append(item)
        result.extend(_flatten_menu_seed(item.get("children") or []))
    return result
