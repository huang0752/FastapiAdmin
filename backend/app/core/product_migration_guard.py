"""产品迁移计划：每次只允许 core + 当前产品。"""

from dataclasses import dataclass
from pathlib import Path

from app.config.path_conf import BASE_DIR
from app.core.product_manifest import get_product


@dataclass(frozen=True)
class MigrationScope:
    scope: str
    path: Path


MIGRATIONS_DIR = BASE_DIR / "app" / "migrations"


def migration_plan(product_code: str) -> tuple[MigrationScope, MigrationScope]:
    product = get_product(product_code)
    return (
        MigrationScope("core", MIGRATIONS_DIR / "core"),
        MigrationScope(product.migration_scope, MIGRATIONS_DIR / product.migration_scope),
    )


def assert_migration_plan(product_code: str) -> None:
    for entry in migration_plan(product_code):
        if not entry.path.is_dir():
            raise RuntimeError(f"迁移目录不存在: {entry.path}")
