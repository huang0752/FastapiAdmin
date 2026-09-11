"""Fixed-Food schema/payload contracts, not live mixed-version service tests.

Fixture payloads were validated by actual Pydantic imports from the recorded
Food git archives in isolated subprocesses (no dotenv files; sockets denied).
The exact accepted wire payload is frozen so future schema serialization changes
cannot silently claim compatibility. Old worker business rules are not covered:
for example, accepting an empty role list in a schema does not imply an old
worker accepts manual-role active grants.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures/control_legacy_protocol.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_fixed_food_schema_payload_round_trip(case):
    module_name, model_name = case["new_model"].rsplit(".", 1)
    model = getattr(importlib.import_module(module_name), model_name)
    # Newly emitted payload matches bytes-equivalent JSON accepted by old schema.
    current = model.model_validate(case["payload"]).model_dump(mode="json")
    assert current == case["legacy_validated_payload"]
    # Conversely, a legacy normalized payload remains accepted by the new schema.
    assert model.model_validate(case["legacy_validated_payload"]).model_dump(mode="json") == current


def test_protocol_fixture_records_exact_verified_sources():
    assert FIXTURE["sources"]["products"]["commit"] == "593e8e7cb5edf9ad0db5a7e7118820cb6a82a47d"
    assert FIXTURE["sources"]["control"]["commit"] == "d19ec0b1c44380a7b269e4888b086c886015642f"
    for source in FIXTURE["sources"].values():
        assert source["schema_sha256"]
        assert all(len(digest) == 64 for digest in source["schema_sha256"].values())
