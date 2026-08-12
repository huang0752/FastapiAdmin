"""食品物流产品注册表：不通过目录扫描推断产品边界。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductModule:
    code: str
    name: str
    assembly: str
    backend_module: str
    frontend_prefix: str
    permission_prefix: str
    seed_pack: str
    migration_scope: str
    package_code: str
    screen_route: str


PRODUCT_MODULES: dict[str, ProductModule] = {
    "trace": ProductModule(
        code="trace",
        name="食品安全质量追溯系统",
        assembly="food-traceability",
        backend_module="app.plugin.module_food_traceability",
        frontend_prefix="module_trace",
        permission_prefix="food_traceability",
        seed_pack="food-traceability",
        migration_scope="trace",
        package_code="trace-standard",
        screen_route="/food_traceability/screen/overview",
    ),
    "agri": ProductModule(
        code="agri",
        name="农产品配送管理系统",
        assembly="agricultural-delivery",
        backend_module="app.plugin.module_agricultural_delivery",
        frontend_prefix="module_agri",
        permission_prefix="agricultural_delivery",
        seed_pack="agricultural-delivery",
        migration_scope="agri",
        package_code="agri-standard",
        screen_route="/agricultural_delivery/screen/overview",
    ),
    "logistic": ProductModule(
        code="logistic",
        name="冷链物流配送车辆管理系统",
        assembly="cold-chain-vehicle",
        backend_module="app.plugin.module_cold_chain_vehicle",
        frontend_prefix="module_logistic",
        permission_prefix="cold_chain_vehicle",
        seed_pack="cold-chain-vehicle",
        migration_scope="logistic",
        package_code="logistic-standard",
        screen_route="/cold_chain_vehicle/screen/overview",
    ),
}


ASSEMBLY_TO_PRODUCT = {item.assembly: item for item in PRODUCT_MODULES.values()}


def get_product(code: str) -> ProductModule:
    try:
        return PRODUCT_MODULES[code]
    except KeyError as exc:
        raise ValueError(f"未知食品物流产品: {code}") from exc
