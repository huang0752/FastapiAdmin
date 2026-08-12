"""食品物流三产品工程基线契约。

这些测试不连接数据库或 Redis，只验证可确定的装配、品牌、迁移和大屏边界。
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

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
    for product_code in ("trace", "agri", "logistic"):
        assert f'"build:{product_code}"' in package
        assert (REPOSITORY_DIR / "frontend" / "web" / "src" / "api" / f"module_{product_code}").is_dir()
        assert (REPOSITORY_DIR / "frontend" / "web" / "src" / "views" / f"module_{product_code}").is_dir()
    assert (REPOSITORY_DIR / "deploy" / "nginx" / "products.conf.example").is_file()
