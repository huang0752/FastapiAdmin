"""三产品共用的大屏响应骨架。"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.product_manifest import get_product


class ScreenState(StrEnum):
    LOADING = "loading"
    NORMAL = "normal"
    PARTIAL = "partial"
    STALE = "stale"
    EMPTY = "empty"
    ERROR = "error"


class ScreenIdentity(BaseModel):
    site_code: str
    package_code: str
    screen_variant: str
    tenant_id: int


class ScreenFreshness(BaseModel):
    generated_at: datetime
    data_cutoff_at: datetime | None = None
    stale_after_seconds: int = Field(gt=0)


class ScreenOverview(BaseModel):
    identity: ScreenIdentity
    state: ScreenState
    freshness: ScreenFreshness
    data: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def resolve_screen_variant(*, product_code: str, site_code: str, package_code: str) -> str:
    product = get_product(product_code)
    if site_code not in {"data360", "znceedi"} or package_code != product.package_code:
        raise PermissionError("未配置的 Site/Package 大屏组合")
    return site_code
