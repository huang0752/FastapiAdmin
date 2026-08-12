"""食品物流三产品工程基线契约。

这些测试不连接数据库或 Redis，只验证可确定的装配、品牌、迁移和大屏边界。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.v1.module_system.auth.schema import CaptchaOutSchema
from app.config.setting import Settings
from app.core.assembly import load_assembly_from_file
from app.core.base_schema import JWTPayloadSchema
from app.core.exceptions import CustomException
from app.core.security import create_access_token, decode_access_token

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = BACKEND_DIR.parent


def _required_module(name: str):
    assert importlib.util.find_spec(name) is not None, f"缺少一等产品模块: {name}"
    return importlib.import_module(name)


def test_product_registry_contains_exactly_three_independent_products() -> None:
    registry = _required_module("app.core.product_manifest")

    assert set(registry.PRODUCT_MODULES) == {"trace", "agri", "logistic"}
    assert {item.assembly for item in registry.PRODUCT_MODULES.values()} == {
        "food-traceability",
        "agricultural-delivery",
        "cold-chain-vehicle",
    }
    assert len({item.backend_module for item in registry.PRODUCT_MODULES.values()}) == 3
    assert len({item.permission_prefix for item in registry.PRODUCT_MODULES.values()}) == 3
    assert {item.permission_prefix for item in registry.PRODUCT_MODULES.values()} == {
        "module_food_traceability",
        "module_agricultural_delivery",
        "module_cold_chain_vehicle",
    }
    assert "food-logi-suite" not in {item.assembly for item in registry.PRODUCT_MODULES.values()}


def test_each_product_brand_has_an_icon_only_logo() -> None:
    logo_root = REPOSITORY_DIR / "frontend" / "web" / "public" / "brand" / "logos"
    assert {path.name for path in logo_root.glob("*.png")} == {
        "data360-trace.png",
        "data360-agri.png",
        "data360-logistic.png",
        "znceedi-trace.png",
        "znceedi-agri.png",
        "znceedi-logistic.png",
    }


@pytest.mark.parametrize(
    ("assembly_name", "business_plugin", "seed_pack"),
    [
        ("food-traceability", "module_food_traceability", "food-traceability"),
        ("agricultural-delivery", "module_agricultural_delivery", "agricultural-delivery"),
        ("cold-chain-vehicle", "module_cold_chain_vehicle", "cold-chain-vehicle"),
    ],
)
def test_each_assembly_enables_only_its_business_module(
    assembly_name: str,
    business_plugin: str,
    seed_pack: str,
) -> None:
    path = BACKEND_DIR / "app" / "assemblies" / f"{assembly_name}.toml"
    assert path.exists(), f"缺少装配文件: {path.name}"
    assembly = load_assembly_from_file(path)

    enabled_business = {
        item
        for item in assembly.enabled_plugins
        if item
        in {
            "module_food_traceability",
            "module_agricultural_delivery",
            "module_cold_chain_vehicle",
        }
    }
    assert enabled_business == {business_plugin}
    assert assembly.seed_packs == ["food-common", seed_pack]
    assert "workspace" in assembly.enabled_route_groups
    assert "dashboard" not in assembly.enabled_route_groups
    assert {"module_ai", "module_generator", "module_example"}.issubset(
        assembly.disabled_plugins
    )
    assert {"ai-chat", "generator"}.isdisjoint(assembly.enabled_route_groups)
    assert assembly.is_feature_enabled("tenant_workspace") is True
    assert assembly.is_feature_enabled("usage_certificate") is True
    assert assembly.is_feature_enabled("ai_model_foundation") is True
    assert assembly.is_feature_enabled("demo_data_blueprint") is True
    assert assembly.is_feature_enabled("ai_assistant") is False
    assert assembly.is_feature_enabled("demo_content") is False


def test_food_common_seed_extends_framework_minimal_seed() -> None:
    manifest = (
        BACKEND_DIR / "app" / "scripts" / "seeds" / "food-common" / "seed.toml"
    ).read_text()

    assert 'depends = ["minimal"]' in manifest

    sites = __import__("json").loads(
        (
            BACKEND_DIR
            / "app"
            / "scripts"
            / "seeds"
            / "food-common"
            / "platform_site.json"
        ).read_text()
    )
    assert {site["code"]: site["id"] for site in sites} == {
        "data360": 1,
        "znceedi": 2,
    }


def test_brand_contract_maps_six_production_hosts_and_six_local_hosts() -> None:
    brands = _required_module("app.core.food_brand_contract")

    assert brands.BRAND_SHORT_NAMES == {
        "data360": "华夏电投",
        "znceedi": "中能电投",
    }
    assert len(brands.PRODUCTION_HOSTS) == 6
    assert len(brands.LOCALHOST_HOSTS) == 6
    assert brands.resolve_site_code("trace.data360.org.cn") == "data360"
    assert brands.resolve_site_code("logistic.znceedi.localhost:8103") == "znceedi"
    with pytest.raises(ValueError, match="未配置"):
        brands.resolve_site_code("unknown.localhost")


def test_jwt_carries_signed_site_id_and_four_way_boundary_rejects_mismatch() -> None:
    payload = JWTPayloadSchema(sub="session-1", site_id=20, exp=4102444800)
    decoded = decode_access_token(create_access_token(payload))
    assert decoded.site_id == 20

    from app.api.v1.module_system.auth.service import validate_session_site

    validate_session_site(
        jwt_site_id=20,
        session_site_id=20,
        request_site_id=20,
        tenant_site_id=20,
    )
    with pytest.raises(CustomException, match="站点.*不匹配|跨站点"):
        validate_session_site(
            jwt_site_id=10,
            session_site_id=20,
            request_site_id=20,
            tenant_site_id=20,
        )


@pytest.mark.parametrize("product_code", ["trace", "agri", "logistic"])
def test_migration_plan_contains_core_and_only_current_product(product_code: str) -> None:
    migrations = _required_module("app.core.product_migration_guard")
    plan = migrations.migration_plan(product_code)

    assert [entry.scope for entry in plan] == ["core", product_code]
    assert all(entry.path.is_dir() for entry in plan)
    assert not ({"trace", "agri", "logistic"} - {product_code}).intersection(
        entry.scope for entry in plan
    )


@pytest.mark.parametrize("product_code", ["trace", "agri", "logistic"])
def test_screen_contract_has_one_aggregate_route_and_two_brand_variants(product_code: str) -> None:
    registry = _required_module("app.core.product_manifest")
    screens = _required_module("app.plugin.module_food_common.screen_contract")
    product = registry.PRODUCT_MODULES[product_code]

    assert product.screen_route.endswith("/screen/overview")
    assert screens.resolve_screen_variant(
        product_code=product_code,
        site_code="data360",
        package_code=product.package_code,
    ) == "data360"
    assert screens.resolve_screen_variant(
        product_code=product_code,
        site_code="znceedi",
        package_code=product.package_code,
    ) == "znceedi"
    assert set(screens.ScreenState) == {
        screens.ScreenState.LOADING,
        screens.ScreenState.NORMAL,
        screens.ScreenState.PARTIAL,
        screens.ScreenState.STALE,
        screens.ScreenState.EMPTY,
        screens.ScreenState.ERROR,
    }


def test_frontend_and_deployment_have_independent_product_entrypoints() -> None:
    package = (REPOSITORY_DIR / "frontend" / "web" / "package.json").read_text()
    expected_titles = {
        "trace": "食品安全质量追溯系统",
        "agri": "农产品配送管理系统",
        "logistic": "冷链物流配送车辆管理系统",
    }
    for product_code in ("trace", "agri", "logistic"):
        assert f'"build:{product_code}"' in package
        assert (REPOSITORY_DIR / "frontend" / "web" / "src" / "api" / f"module_{product_code}").is_dir()
        assert (REPOSITORY_DIR / "frontend" / "web" / "src" / "views" / f"module_{product_code}").is_dir()
        mode_env = REPOSITORY_DIR / "frontend" / "web" / f".env.{product_code}"
        assert mode_env.is_file()
        assert f"VITE_APP_TITLE = {expected_titles[product_code]}" in mode_env.read_text()
    assert (REPOSITORY_DIR / "deploy" / "nginx" / "products.conf.example").is_file()


def test_food_logi_product_environment_disables_captcha_without_changing_framework_default() -> None:
    assert Settings.model_fields["CAPTCHA_ENABLE"].default is True

    deployment_env = (
        REPOSITORY_DIR / "deploy" / "env" / "products.env.example"
    ).read_text()
    assert "CAPTCHA_ENABLE=false" in deployment_env


def test_disabled_captcha_endpoint_returns_empty_payload(test_client) -> None:
    response = test_client.get("/system/auth/captcha/get")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "enable": False,
        "key": "",
        "img_base": "",
    }


def test_enabled_captcha_response_still_requires_key_and_image() -> None:
    with pytest.raises(ValidationError):
        CaptchaOutSchema(enable=True, key="", img_base="")


def test_login_without_captcha_reaches_account_authentication(test_client) -> None:
    response = test_client.post(
        "/system/auth/login",
        data={"username": "food-logi-missing-user", "password": "not-a-real-password"},
    )

    assert response.status_code == 400
    assert response.json()["msg"] == "账号或密码错误"


def test_workspace_and_usage_certificate_permissions_remain_in_package_menu_contract() -> None:
    rows = json.loads(
        (BACKEND_DIR / "app" / "scripts" / "data" / "platform_package_menu.json").read_text()
    )
    assert rows
    for package in rows:
        permissions = {item.get("permission") for item in package["menus"]}
        assert "module_platform:workspace:query" in permissions
        assert "module_platform:usage-certificate:tenant-query" in permissions


def test_demo_data_blueprint_is_bindable_without_mounting_ai_chat() -> None:
    from app.plugin.module_ai.chat.registry import default_ai_registry
    from app.plugin.module_ai.chat.service import AiFeatureBindingService

    feature = default_ai_registry.get_feature("demo_data.blueprint")
    assert feature is not None
    assert feature.prompt_key == "demo_data.blueprint"
    assert AiFeatureBindingService._default_item(feature)["feature_code"] == "demo_data.blueprint"


@pytest.mark.parametrize(
    "assembly_name",
    ["food-traceability", "agricultural-delivery", "cold-chain-vehicle"],
)
def test_product_dynamic_router_does_not_mount_generic_ai_chat(assembly_name: str) -> None:
    import app.core.discover as discover
    from app.config.setting import settings
    from app.core.assembly import reset_assembly_cache

    old_file = settings.APP_ASSEMBLY_FILE
    old_name = settings.APP_ASSEMBLY
    old_router = discover._dynamic_router_cache
    settings.APP_ASSEMBLY_FILE = str(
        BACKEND_DIR / "app" / "assemblies" / f"{assembly_name}.toml"
    )
    settings.APP_ASSEMBLY = assembly_name
    reset_assembly_cache()
    discover._dynamic_router_cache = None
    try:
        paths = {getattr(route, "path", "") for route in discover.get_dynamic_router().routes}
        assert not any(path.startswith("/ai/chat") or path.startswith("/ai") for path in paths)
    finally:
        discover._dynamic_router_cache = old_router
        settings.APP_ASSEMBLY_FILE = old_file
        settings.APP_ASSEMBLY = old_name
        reset_assembly_cache()
