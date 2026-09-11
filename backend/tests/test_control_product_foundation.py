from pathlib import Path

from app.core.assembly import load_assembly_from_file


def test_control_assembly_starts_from_stable_saas_foundation() -> None:
    assembly_path = Path(__file__).parents[1] / "app" / "assemblies" / "control.toml"

    assembly = load_assembly_from_file(assembly_path)

    assert assembly.name == "control"
    assert assembly.seed_packs == ["control"]
    assert assembly.is_plugin_enabled("module_task")
    assert assembly.is_plugin_enabled("module_generator")
    assert assembly.is_plugin_enabled("module_ai")
    assert not assembly.is_plugin_enabled("module_example")
    assert assembly.is_route_group_enabled("platform")
    assert assembly.is_route_group_enabled("module-control")
    assert not assembly.is_route_group_enabled("pricing")
    assert assembly.feature_flags["application_portal"] is True
    assert assembly.feature_flags["sso_provider"] is True
    assert assembly.feature_flags["sso_client"] is False
