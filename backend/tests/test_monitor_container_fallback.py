import sys
from types import SimpleNamespace

import psutil

from app.api.v1.module_monitor.server.service import ServerService


class _ReadableProcess:
    def name(self) -> str:
        return "python-worker"

    def create_time(self) -> float:
        return 1_700_000_000.0

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=1024)

    def exe(self) -> str:
        return "/usr/local/bin/python"


def _memory() -> SimpleNamespace:
    return SimpleNamespace(available=4096)


def test_python_info_preserves_available_metrics_when_exe_is_access_denied(monkeypatch) -> None:
    class RestrictedExecutableProcess(_ReadableProcess):
        def exe(self) -> str:
            raise psutil.AccessDenied(pid=123)

    monkeypatch.setattr(psutil, "Process", RestrictedExecutableProcess)
    monkeypatch.setattr(psutil, "virtual_memory", _memory)
    monkeypatch.setattr(ServerService, "_calculate_run_time", staticmethod(lambda _: "1天2小时3分钟"))

    result = ServerService._get_python_info()

    assert result.name == "python-worker"
    assert result.home == sys.executable
    assert result.run_time == "1天2小时3分钟"
    assert result.memory_total == "4.0KB"
    assert result.memory_used == "1.0KB"
    assert result.memory_free == "3.0KB"
    assert result.memory_usage == 25.0


def test_python_info_returns_stable_fallback_when_process_disappears(monkeypatch) -> None:
    def missing_process() -> None:
        raise psutil.NoSuchProcess(pid=123)

    monkeypatch.setattr(psutil, "Process", missing_process)
    monkeypatch.setattr(psutil, "virtual_memory", _memory)

    result = ServerService._get_python_info()

    assert result.name == "CPython"
    assert result.home == sys.executable
    assert result.start_time == "未知"
    assert result.run_time == "未知"
    assert result.memory_total == "4.0KB"
    assert result.memory_used == "0.0B"
    assert result.memory_free == "4.0KB"
    assert result.memory_usage == 0.0


def test_python_info_falls_back_when_executable_path_is_empty(monkeypatch) -> None:
    class EmptyExecutableProcess(_ReadableProcess):
        def exe(self) -> str:
            return ""

    monkeypatch.setattr(psutil, "Process", EmptyExecutableProcess)
    monkeypatch.setattr(psutil, "virtual_memory", _memory)

    result = ServerService._get_python_info()

    assert result.home == sys.executable
    assert result.name == "python-worker"
    assert result.memory_used == "1.0KB"
