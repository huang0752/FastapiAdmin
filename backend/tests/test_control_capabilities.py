from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.assembly import AssemblyConfig, load_assembly_from_file


def test_default_role_is_manual_without_product_configuration():
    assert AssemblyConfig().federation_default_role.mode == "manual"


def test_declared_role_rejects_empty_permissions(tmp_path: Path):
    path = tmp_path / "role.toml"
    path.write_text('[federation.default_role]\nmode="declared"\n')
    with pytest.raises(ValueError, match="权限"):
        load_assembly_from_file(path)


@pytest.mark.parametrize("prefix", ["module_system", "module_control", "module_platform", "module_ai", "", "*"])
def test_default_role_rejects_reserved_or_wildcard_prefix(tmp_path: Path, prefix):
    path = tmp_path / "role.toml"
    path.write_text(f'[federation.default_role]\nmode="declared"\npermission_prefixes=["{prefix}"]\n')
    with pytest.raises(ValueError):
        load_assembly_from_file(path)


def test_declared_role_parses_custom_product_without_food_mapping(tmp_path: Path):
    path = tmp_path / "role.toml"
    path.write_text('[assembly]\nname="example-product"\n[federation.default_role]\nmode="declared"\npermission_prefixes=["module_example_orders"]\n')
    policy = load_assembly_from_file(path).federation_default_role
    assert policy.permission_prefixes == ["module_example_orders"]
    assert policy.data_scope == 1


def config(**changes):
    values = {"CONTROL_SSO_ENABLED": False, "CONTROL_TENANT_PROVISIONING_ENABLED": False,
              "CONTROL_USER_ACCESS_SYNC_ENABLED": False, "CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED": False,
              "CELERY_ENABLED": False}
    values.update(changes)
    return SimpleNamespace(**values)


def test_standalone_has_no_control_dependency():
    from app.core.control_features import validate_control_capabilities
    validate_control_capabilities(AssemblyConfig(), config())


def test_provider_uses_capability_not_assembly_name():
    from app.core.control_features import is_control_provider, validate_control_capabilities
    assembly = AssemblyConfig(name="custom-hub", feature_flags={"sso_provider": True}, enabled_plugins=["module_task", "module_control_provision"])
    assert is_control_provider(assembly)
    validate_control_capabilities(assembly, config(CELERY_ENABLED=True))


def test_provider_requires_worker_and_disallows_client_role():
    from app.core.control_features import validate_control_capabilities
    assembly = AssemblyConfig(feature_flags={"sso_provider": True})
    with pytest.raises(ValueError, match="Celery"):
        validate_control_capabilities(assembly, config())
    with pytest.raises(ValueError, match="同时"):
        validate_control_capabilities(assembly, config(CELERY_ENABLED=True, CONTROL_SSO_ENABLED=True))


def test_explicitly_disabled_client_cannot_be_enabled_by_environment():
    from app.core.control_features import validate_control_capabilities
    with pytest.raises(ValueError, match="SSO"):
        validate_control_capabilities(AssemblyConfig(feature_flags={"sso_client": False}), config(CONTROL_SSO_ENABLED=True))


def test_standalone_does_not_load_control_tasks():
    assert not any("module_control_provision" in name for name in AssemblyConfig().business_task_modules())


def test_disabled_control_menu_is_not_available():
    assert not AssemblyConfig().is_menu_item_enabled({"permission": "module_control:application:query", "route_path": "/module_control"})


def test_provider_subcapabilities_remove_write_routes():
    from app.api.v1.module_control import build_control_router
    disabled = AssemblyConfig(feature_flags={"sso_provider": True})
    paths = {r.path for r in build_control_router(disabled).routes}
    assert "/control/applications" in paths
    assert "/control/users" not in paths
    assert not any("/grants" in path or "/portal/" in path or "/tenant-provisions" in path for path in paths)
    enabled = load_assembly_from_file(Path(__file__).parents[1] / "app/assemblies/control.toml")
    enabled_paths = {r.path for r in build_control_router(enabled).routes}
    assert {"/control/users", "/control/portal/my-applications", "/control/tenant-applications/{tenant_application_id}/grants/{user_id}"} <= enabled_paths
