"""当前 Assembly 的显式产品模块入口。"""

from app.core.assembly import get_assembly
from app.core.product_manifest import ASSEMBLY_TO_PRODUCT, ProductModule


def current_product() -> ProductModule:
    assembly = get_assembly().name
    try:
        return ASSEMBLY_TO_PRODUCT[assembly]
    except KeyError as exc:
        raise RuntimeError(f"Assembly {assembly} 不是食品物流产品装配") from exc
