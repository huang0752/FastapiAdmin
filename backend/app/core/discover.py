"""
简化的动态路由发现与注册。

目录与命名规范（不满足则无法注册或导入失败）：
- 插件必须放在 ``app/plugin`` 下，且**顶级目录名**必须以 ``module_`` 开头，例如
  ``module_example``、``module_yourfeature``（扫描模式：``module_*/**/controller.py``）。
- 控制器文件名必须为 ``controller.py``（大小写敏感，Linux 上 ``Controller.py`` 无效）。
- 从 ``module_xxx`` 到 ``controller.py`` 的**每一级目录名**须为合法 Python 标识符
  （仅字母数字下划线、不以数字开头；不要使用中划线、空格、中文目录名等）。
- 每一级目录应可作为包导入：通常需有 ``__init__.py``（或符合 namespace package 规则）。
- 在 ``controller.py`` 的**模块顶层**定义 ``APIRouter`` 实例并赋值给变量
  （如 ``DemoRouter = APIRouter(...)``）；定义在函数内部的 router **不会被**扫描到。

路由前缀：顶级目录 ``module_xxx`` 映射为容器前缀 ``/xxx``（去掉前缀 ``module_`` 共 7 个字符）。

常见「路由没注册」原因：
- 目录不叫 ``module_*``，或 ``controller.py`` 不在该树下的任意子路径中。
- 包无法导入：缺 ``__init__.py``、目录名非法、拼写不一致。
- ``controller.py`` 无语法错误但模块内没有任何顶层 ``APIRouter`` 变量。
"""

# 标准库导入
import importlib
import sys
from pathlib import Path

# 第三方库导入
from fastapi import APIRouter, Depends, Request
from fastapi import FastAPI as _FastAPI
from sqlalchemy import select

# 内部库导入
from app.core.assembly import (
    get_assembly,
    is_plugin_enabled,
    known_plugin_module_codes,
    plugin_code_candidates,
)
from app.core.base_schema import AuthSchema
from app.core.dependencies import get_current_user
from app.core.exceptions import CustomException
from app.core.http_limit import TenantPackageRateLimiter
from app.core.logger import logger

# 模块级缓存：最近一次构建的动态路由实例
_dynamic_router_cache: APIRouter | None = None
# 记录最近一次注册到 app 的插件路径前缀，用于热重载时精准移除
_registered_plugin_prefixes: set[str] = set()
# 记录最近一次注册到 app 的动态路由签名，避免热重载误删同前缀的静态路由
_registered_plugin_route_keys: set[tuple[str, tuple[str, ...]]] = set()
# 记录最近一次扫描中导入失败的插件前缀；热重载失败时保留旧路由
_failed_plugin_prefixes: set[str] = set()
# 运行时引用，由 init_app.py 设置
_app_ref: _FastAPI | None = None

def set_app_ref(app: _FastAPI) -> None:
    """由 init_app.py 在 app 创建后调用，存储引用供热重载使用。"""
    global _app_ref
    _app_ref = app


def get_dynamic_router_dependencies() -> list:
    """动态插件 HTTP 路由统一依赖：限流 + 插件运行时边界。"""
    return [
        Depends(TenantPackageRateLimiter(default_times=200, seconds=10)),
        Depends(validate_dynamic_plugin_access),
    ]


async def validate_dynamic_plugin_access(
    request: Request,
    auth: AuthSchema = Depends(get_current_user),
) -> AuthSchema:
    """校验动态插件对当前租户是否已安装、启用且可用。"""
    return await validate_dynamic_plugin_access_for_path(request.url.path, auth)


async def validate_dynamic_plugin_access_for_path(path: str, auth: AuthSchema) -> AuthSchema:
    """按请求路径校验动态插件访问权限，供 HTTP 与 WebSocket 共用。"""
    if auth.user and auth.user.is_superuser:
        return auth

    module_code = _module_code_from_path(path)
    if not module_code:
        return auth
    if not auth.db or not auth.tenant_id:
        raise CustomException(msg="无法获取租户信息", code=10403, status_code=403)

    from app.api.v1.module_platform.package.model import PackagePluginModel
    from app.api.v1.module_platform.plugin.model import PluginModel, TenantPluginModel
    from app.api.v1.module_platform.tenant.model import TenantModel

    plugin_codes = _plugin_code_candidates(module_code)
    plugins = (
        await auth.db.execute(
            select(PluginModel).where(
                PluginModel.code.in_(plugin_codes),
                PluginModel.status == 0,
                PluginModel.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    plugin_map = {plugin.code: plugin for plugin in plugins}
    plugin = next((plugin_map[code] for code in plugin_codes if code in plugin_map), None)
    if not plugin:
        raise CustomException(msg="插件未注册或已停用", code=10403, status_code=403)

    tenant_plugin = (
        await auth.db.execute(
            select(TenantPluginModel)
            .where(
                TenantPluginModel.tenant_id == auth.tenant_id,
                TenantPluginModel.plugin_id == plugin.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if not tenant_plugin or not tenant_plugin.enabled:
        raise CustomException(msg="插件未安装或已禁用", code=10403, status_code=403)
    if plugin.price > 0 and not tenant_plugin.purchased:
        raise CustomException(msg="付费插件未购买", code=10403, status_code=403)

    tenant = await auth.db.get(TenantModel, auth.tenant_id)
    if tenant and tenant.package_id:
        package_plugin_ids = (
            await auth.db.execute(
                select(PackagePluginModel.plugin_id).where(PackagePluginModel.package_id == tenant.package_id)
            )
        ).scalars().all()
        if plugin.id not in package_plugin_ids:
            raise CustomException(msg="当前套餐不支持此插件", code=10403, status_code=403)

    return auth


def _module_code_from_path(path: str) -> str | None:
    known_segments = {prefix.strip("/") for prefix in _registered_plugin_prefixes}
    known_segments.update(code[7:] for code in known_plugin_module_codes())
    for segment in path.strip("/").split("/"):
        if segment in known_segments:
            return f"module_{segment}"
    return None


def _plugin_code_candidates(module_code: str) -> list[str]:
    return plugin_code_candidates(module_code)


def _build_dynamic_router() -> APIRouter:
    """实际执行扫描，返回新的动态路由 APIRouter 实例。"""
    logger.info("🚀 开始动态路由发现与注册")
    assembly = get_assembly()

    root_router = APIRouter()
    seen_router_ids: set[int] = set()
    failed_prefixes: set[str] = set()
    try:
        base_package = importlib.import_module("app.plugin")
        base_dir = Path(next(iter(base_package.__path__)))

        controller_files = list(base_dir.glob("module_*/**/controller.py"))
        controller_files.sort()

        container_routers: dict[str, APIRouter] = {}

        for file in controller_files:
            rel_path = file.relative_to(base_dir)
            path_parts = rel_path.parts
            top_module = path_parts[0]
            if not is_plugin_enabled(top_module):
                logger.info("⏭️  跳过插件模块 {}：assembly {} disabled", top_module, assembly.name)
                continue

            suffix = top_module[7:] if top_module.startswith("module_") else ""
            if not suffix:
                logger.error(
                    f"❌ 跳过异常顶级目录名（须为 module_ 前缀且后面还有名称）: {top_module!r}，"
                    f"文件: {file}"
                )
                continue
            prefix = f"/{suffix}"

            if prefix not in container_routers:
                container_routers[prefix] = APIRouter(prefix=prefix)
            container_router = container_routers[prefix]

            module_path = f"app.plugin.{'.'.join(path_parts[:-1])}.controller"
            try:
                module = importlib.import_module(module_path)
                registered_here = 0
                for attr_name in dir(module):
                    attr_value = getattr(module, attr_name, None)
                    if isinstance(attr_value, APIRouter):
                        router_id = id(attr_value)
                        if router_id not in seen_router_ids:
                            seen_router_ids.add(router_id)
                            container_router.include_router(attr_value)
                            registered_here += 1
                            logger.info(f"  ↳ 注册 APIRouter 变量 `{attr_name}` ← {module_path}")

                if registered_here == 0:
                    logger.warning(
                        f"⚠️ 模块已加载但未注册任何路由: {module_path}\n"
                        f"   原因：该文件中未找到**顶层** APIRouter 实例。\n"
                        f"   规范：在 controller.py 模块顶层定义，例如 "
                        f"`XxxRouter = APIRouter(route_class=..., prefix=..., tags=[...])`，"
                        f"不要仅在函数内创建 APIRouter。"
                    )

            except Exception as e:
                failed_prefixes.add(prefix)
                hint = _import_failure_hint(e)
                logger.error(f"❌ 处理模块失败: {module_path}\n   {hint}\n   异常: {e!s}")

        for prefix, container_router in sorted(container_routers.items()):
            route_count = len(container_router.routes)
            root_router.include_router(container_router)
            if route_count == 0:
                logger.warning(
                    f"⚠️ 容器前缀 {prefix} 下未挂载任何子路由（可能该 module 下所有 controller 均未导出 APIRouter）"
                )
            logger.info(f"✅ 注册容器: {prefix} (子路由数: {route_count})")

        # 记录本次注册的所有插件前缀，供热重载时精准移除
        global _registered_plugin_prefixes, _registered_plugin_route_keys, _failed_plugin_prefixes
        _registered_plugin_prefixes = set(container_routers.keys())
        _registered_plugin_route_keys = {_route_key(route) for route in root_router.routes}
        _failed_plugin_prefixes = failed_prefixes

        logger.info(f"✅ 动态路由发现完成: 共 {len(container_routers)} 个容器前缀")
        return root_router

    except Exception as e:
        logger.error(f"❌ 动态路由发现整体失败: {e!s}")
        return root_router


def get_dynamic_router() -> APIRouter:
    """
    获取动态路由实例（带缓存）。

    返回:
    - APIRouter: 包含所有动态路由的根路由实例
    """
    global _dynamic_router_cache
    if _dynamic_router_cache is None:
        _dynamic_router_cache = _build_dynamic_router()
    return _dynamic_router_cache


def _non_empty_prefixes(router: APIRouter, prefixes: set[str]) -> set[str]:
    return {
        prefix
        for prefix in prefixes
        if any(getattr(route, "path", "").startswith(prefix) for route in router.routes)
    }


def _route_key(route) -> tuple[str, tuple[str, ...]]:
    methods = getattr(route, "methods", None) or ()
    return getattr(route, "path", ""), tuple(sorted(methods))


def _filter_router_by_prefix(router: APIRouter, prefixes: set[str]) -> APIRouter:
    filtered = APIRouter()
    filtered.routes.extend(
        route
        for route in router.routes
        if any(getattr(route, "path", "").startswith(prefix) for prefix in prefixes)
    )
    return filtered


def reload_dynamic_router() -> APIRouter:
    """
    清除插件模块缓存并重新扫描，同时更新已挂载到 FastAPI app 的路由。

    工作机制：
    1. 根据 _registered_plugin_prefixes 精准移除 app.routes 中已注册的插件路由
    2. 清除 sys.modules 中缓存的插件模块
    3. 重新扫描并构建新的动态路由
    4. 将新路由挂载回 app（include_router）

    要求：init_app.py 在创建 app 后调用 discover.set_app_ref(app)，
    否则路由重载仅重建 Router 对象，不会更新运行中的 app。

    返回:
    - APIRouter: 重新构建后的动态路由实例
    """
    global _dynamic_router_cache, _registered_plugin_prefixes, _registered_plugin_route_keys, _failed_plugin_prefixes, _app_ref

    app = _app_ref
    previous_prefixes = set(_registered_plugin_prefixes)
    previous_route_keys = set(_registered_plugin_route_keys)

    # ── 1. 清除插件模块缓存，迫使 importlib 重新执行模块代码 ──
    _purge_plugin_modules()

    # ── 2. 先构建新路由；仅替换成功构建出路由的前缀，避免失败模块导致旧路由下线 ──
    new_router = _build_dynamic_router()
    discovered_prefixes = set(_registered_plugin_prefixes)
    failed_prefixes = set(_failed_plugin_prefixes)
    refreshed_prefixes = _non_empty_prefixes(new_router, discovered_prefixes)
    keep_old_prefixes = previous_prefixes & failed_prefixes
    remove_old_prefixes = previous_prefixes - keep_old_prefixes
    mount_prefixes = refreshed_prefixes - failed_prefixes
    mount_router = _filter_router_by_prefix(new_router, mount_prefixes)

    # ── 3. 从运行中的 app 移除成功重载前缀的旧路由 ──
    if app and remove_old_prefixes:
        before = len(app.routes)
        app.router.routes[:] = [
            r for r in app.router.routes
            if _route_key(r) not in previous_route_keys
            or not any(getattr(r, "path", "").startswith(p) for p in remove_old_prefixes)
        ]
        removed = before - len(app.routes)
        logger.info(f"🧹 已移除 {removed} 条旧插件路由（前缀: {remove_old_prefixes}）")
    elif not app:
        logger.warning("⚠️ _app_ref 未设置，无法更新运行中的 app 路由；请确认 init_app.py 已调用 discover.set_app_ref(app)")
    elif previous_prefixes:
        logger.warning("⚠️ 插件热重载未生成可替换的新路由，已保留运行中的旧插件路由")

    # ── 4. 将新路由挂载回 app ──
    if app and mount_prefixes:
        # 构造与 init_app.py 中相同的依赖项
        app.include_router(mount_router, dependencies=get_dynamic_router_dependencies())
        logger.info("✅ 新插件路由已挂载到运行中的 app")

    if keep_old_prefixes:
        preserved_route_keys = {
            key
            for key in previous_route_keys
            if any(key[0].startswith(prefix) for prefix in keep_old_prefixes)
        }
        _registered_plugin_prefixes = discovered_prefixes | keep_old_prefixes
        _registered_plugin_route_keys = {_route_key(route) for route in mount_router.routes} | preserved_route_keys
        logger.warning(f"⚠️ 插件前缀 {keep_old_prefixes} 热重载失败，已保留旧路由")

    if not keep_old_prefixes:
        _registered_plugin_prefixes = mount_prefixes
        _registered_plugin_route_keys = {_route_key(route) for route in mount_router.routes}
        _dynamic_router_cache = mount_router
    else:
        logger.warning("⚠️ 插件路由仅部分热重载成功，保留原动态路由缓存")

    logger.info("✅ 插件动态路由热重载完成")
    return mount_router


def _purge_plugin_modules() -> None:
    """
    从 ``sys.modules`` 中清除可安全热重载的插件模块。

    SQLAlchemy 的声明式模型一旦注册到全局 ``Base.metadata``，便不能在同一
    进程中重新执行对应 ``model.py``，否则会重复声明同名表。这里保留
    ``model`` / ``models`` 模块，仅刷新 controller、service、schema 等业务
    模块。涉及 ORM 模型定义的插件变更必须通过进程重启生效。
    """
    prefix = "app.plugin.module_"
    purged: list[str] = []
    preserved_models: list[str] = []
    for mod_name in list(sys.modules):
        if not mod_name.startswith(prefix):
            continue
        if mod_name.rsplit(".", 1)[-1] in {"model", "models"}:
            preserved_models.append(mod_name)
            continue
        del sys.modules[mod_name]
        purged.append(mod_name)

    if purged:
        logger.info(f"🧹 已清除 {len(purged)} 个插件模块缓存，将重新导入")
    else:
        logger.info("🧹 未发现需要清除的插件模块缓存")
    if preserved_models:
        logger.info(
            "🧱 已保留 {} 个 ORM 模型模块；模型定义变更需重启进程",
            len(preserved_models),
        )


def _import_failure_hint(exc: BaseException) -> str:
    """根据异常类型给出简短排查提示（中文日志）。"""
    if isinstance(exc, ModuleNotFoundError):
        missing = getattr(exc, "name", None) or str(exc)
        return (
            f"无法解析模块（ModuleNotFoundError: {missing}）。"
            "常见原因：① 从 app.plugin 到 controller 的某级目录缺少 __init__.py；"
            "② 目录名不是合法 Python 标识符（禁用连字符、空格、中文等）；"
            "③ 磁盘路径与 import 路径不一致（大小写、子目录名拼写）。"
        )
    if isinstance(exc, ImportError):
        return (
            "导入失败（ImportError）。常见原因：controller 或其依赖模块循环导入、"
            "第三方依赖未安装、或相对导入路径错误。"
        )
    if isinstance(exc, SyntaxError):
        return f"controller.py 存在语法错误：{exc.msg}（约第 {exc.lineno} 行）。"
    if isinstance(exc, PermissionError):
        return (
            "权限错误（PermissionError）。多见于受限环境（沙箱、部分 CI）："
            "import 链上某模块初始化时调用了被禁止的系统能力（如进程池），与目录命名无关。"
            "在完整操作系统下重试；若仍失败再结合堆栈排查。"
        )
    return (
        f"未分类异常（{type(exc).__name__}）。请查看下方堆栈；"
        "若与命名/包结构无关，可能是 controller 顶层 import 的依赖在加载时失败。"
    )


# 重新导出函数供外部使用
__all__ = ["get_dynamic_router", "reload_dynamic_router"]
