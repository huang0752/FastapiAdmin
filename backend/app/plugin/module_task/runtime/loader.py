"""当前 Assembly 的业务任务处理器模块加载器。"""

from __future__ import annotations

import importlib

from app.core.assembly import get_assembly

_loaded = False


def load_business_task_modules() -> tuple[str, ...]:
    global _loaded
    modules = tuple(get_assembly().business_task_modules())
    if _loaded:
        return modules
    for module in modules:
        importlib.import_module(module)
    _loaded = True
    return modules
