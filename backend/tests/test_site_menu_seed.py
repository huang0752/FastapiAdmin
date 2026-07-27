import json
from pathlib import Path


def _walk(items: list[dict]):
    for item in items:
        yield item
        yield from _walk(item.get("children") or [])


def test_platform_menu_seed_contains_site_management_permissions() -> None:
    seed_path = Path(__file__).parents[1] / "app" / "scripts" / "data" / "platform_menu.json"
    menus = list(_walk(json.loads(seed_path.read_text(encoding="utf-8"))))
    permissions = {item.get("permission") for item in menus}

    assert {
        "module_platform:site:query",
        "module_platform:site:create",
        "module_platform:site:update",
        "module_platform:site:delete",
    } <= permissions
    site_menu = next(item for item in menus if item.get("route_name") == "Site")
    assert site_menu["component_path"] == "module_platform/site/index"
    assert site_menu["scope"] == "platform"
