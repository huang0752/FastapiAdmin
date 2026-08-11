import importlib

from fastapi.testclient import TestClient

from app.core.discover import reload_dynamic_router


def test_plugin_reload_preserves_orm_models_and_refreshes_business_modules(
    test_client: TestClient,
) -> None:
    model_module_names = (
        "app.plugin.module_task.cronjob.job.model",
        "app.plugin.module_task.workflow.flows.model",
    )
    service_module_name = "app.plugin.module_task.workflow.flows.service"
    registry_module_name = "app.plugin.module_task.runtime.registry"
    model_modules_before = {
        name: importlib.import_module(name) for name in model_module_names
    }
    model_classes_before = {
        name: module.JobModel if name.endswith("job.model") else module.WorkflowModel
        for name, module in model_modules_before.items()
    }
    service_module_before = importlib.import_module(service_module_name)
    registry_module_before = importlib.import_module(registry_module_name)
    registry_before = registry_module_before.business_task_registry

    router = reload_dynamic_router()

    assert any(getattr(route, "path", "").startswith("/task/") for route in router.routes)
    for name, module_before in model_modules_before.items():
        module_after = importlib.import_module(name)
        model_after = (
            module_after.JobModel
            if name.endswith("job.model")
            else module_after.WorkflowModel
        )
        assert module_after is module_before
        assert model_after is model_classes_before[name]
    assert importlib.import_module(service_module_name) is not service_module_before
    registry_module_after = importlib.import_module(registry_module_name)
    assert registry_module_after is registry_module_before
    assert registry_module_after.business_task_registry is registry_before
