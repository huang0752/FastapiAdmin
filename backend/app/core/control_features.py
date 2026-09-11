"""Runtime capability boundaries shared by startup, routes and workers."""

from app.core.assembly import AssemblyConfig, get_assembly


def is_control_provider(assembly: AssemblyConfig | None = None) -> bool:
    return (assembly or get_assembly()).is_feature_enabled("sso_provider", default=False)


def validate_control_capabilities(assembly: AssemblyConfig, configuration) -> None:
    provider = is_control_provider(assembly)
    if provider and configuration.CONTROL_SSO_ENABLED:
        raise ValueError("同一实例不能同时启用中控服务端与 SSO 客户端")
    if provider:
        if not configuration.CELERY_ENABLED:
            raise ValueError("中控授权同步需要启用 Celery Worker 配置")
        if not all(assembly.is_plugin_enabled(name) for name in ("module_task", "module_control_provision")):
            raise ValueError("中控必须启用任务与开户同步模块")
    if configuration.CONTROL_SSO_ENABLED and assembly.feature_flags.get("sso_client") is False:
        raise ValueError("当前装配已禁止 SSO 客户端")
    if configuration.CONTROL_USER_ACCESS_SYNC_ENABLED and not configuration.CONTROL_SSO_ENABLED:
        raise ValueError("授权同步依赖 SSO 客户端")
    if configuration.CONTROL_USER_ACCESS_ENFORCEMENT_ENABLED and not configuration.CONTROL_USER_ACCESS_SYNC_ENABLED:
        raise ValueError("强制检查依赖授权同步")
    if configuration.CONTROL_USER_ACCESS_SYNC_ENABLED and not assembly.application_code:
        raise ValueError("授权同步必须声明 assembly.application_code")
