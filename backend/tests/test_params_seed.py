"""系统参数 Seed 与持久化契约回归。"""

import json
from pathlib import Path

from app.api.v1.module_system.params.schema import ParamsCreateSchema


def test_all_system_parameter_seed_values_satisfy_api_schema() -> None:
    seed_path = Path(__file__).parents[1] / "app" / "scripts" / "data" / "sys_param.json"
    seed_items = json.loads(seed_path.read_text(encoding="utf-8"))

    validated = [ParamsCreateSchema.model_validate(item) for item in seed_items]

    assert len(validated) == len(seed_items)
    assert any(len(item.config_value or "") > 500 for item in validated)
