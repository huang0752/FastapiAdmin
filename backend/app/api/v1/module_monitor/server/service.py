
import platform
import socket
import sys
import time
from pathlib import Path

import psutil

from app.utils.common_util import bytes2human

from .schema import (
    CpuInfoSchema,
    DiskInfoSchema,
    MemoryInfoSchema,
    PyInfoSchema,
    ServerMonitorSchema,
    SysInfoSchema,
)


class ServerService:
    """服务监控模块服务层"""

    @staticmethod
    async def get_server_monitor_info() -> ServerMonitorSchema:
        return ServerMonitorSchema(
            cpu=ServerService._get_cpu_info(),
            mem=ServerService._get_memory_info(),
            sys=ServerService._get_system_info(),
            py=ServerService._get_python_info(),
            disks=ServerService._get_disk_info(),
        )

    @staticmethod
    def _get_cpu_info() -> CpuInfoSchema:
        cpu_times = psutil.cpu_times_percent()
        cpu_num = psutil.cpu_count(logical=True)
        if not cpu_num:
            cpu_num = 1
        return CpuInfoSchema(
            cpu_num=cpu_num,
            used=cpu_times.user,
            sys=cpu_times.system,
            free=cpu_times.idle,
        )

    @staticmethod
    def _get_memory_info() -> MemoryInfoSchema:
        memory = psutil.virtual_memory()
        return MemoryInfoSchema(
            total=bytes2human(memory.total),
            used=bytes2human(memory.used),
            free=bytes2human(memory.free),
            usage=memory.percent,
        )

    @staticmethod
    def _get_system_info() -> SysInfoSchema:
        hostname = socket.gethostname()
        return SysInfoSchema(
            computer_ip=socket.gethostbyname(hostname),
            computer_name=platform.node(),
            os_arch=platform.machine(),
            os_name=platform.platform(),
            user_dir=str(Path.cwd()),
        )

    @staticmethod
    def _get_python_info() -> PyInfoSchema:
        memory = psutil.virtual_memory()
        available_memory = max(memory.available, 0)
        process_name = platform.python_implementation() or "Python"
        executable_path = sys.executable or "未知"
        start_time_text = "未知"
        run_time = "未知"
        process_memory_used = 0

        try:
            current_process = psutil.Process()
        except (psutil.Error, OSError):
            current_process = None

        if current_process is not None:
            try:
                process_name = current_process.name() or process_name
            except (psutil.Error, OSError):
                pass

            try:
                process_memory_used = max(current_process.memory_info().rss, 0)
            except (psutil.Error, OSError):
                pass

            try:
                process_start_time = current_process.create_time()
                start_time_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(process_start_time))
                run_time = ServerService._calculate_run_time(process_start_time)
            except (psutil.Error, OSError, OverflowError, ValueError):
                pass

            try:
                readable_executable_path = current_process.exe()
                if readable_executable_path:
                    executable_path = str(Path(readable_executable_path))
            except (psutil.Error, OSError):
                pass

        memory_free = max(available_memory - process_memory_used, 0)
        memory_usage = round((process_memory_used / available_memory) * 100, 2) if available_memory else 0.0
        memory_usage = min(memory_usage, 100.0)

        return PyInfoSchema(
            name=process_name,
            version=platform.python_version(),
            start_time=start_time_text,
            run_time=run_time,
            home=executable_path,
            memory_total=bytes2human(available_memory),
            memory_used=bytes2human(process_memory_used),
            memory_free=bytes2human(memory_free),
            memory_usage=memory_usage,
        )

    @staticmethod
    def _get_disk_info() -> list[DiskInfoSchema]:
        disk_info = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                mount_point = str(Path(partition.mountpoint))
                disk_info.append(
                    DiskInfoSchema(
                        dir_name=mount_point,
                        sys_type_name=partition.fstype,
                        type_name=f"本地固定磁盘（{mount_point}）",
                        total=bytes2human(usage.total),
                        used=bytes2human(usage.used),
                        free=bytes2human(usage.free),
                        usage=usage.percent,
                    )
                )
            except (PermissionError, FileNotFoundError):
                continue
        return disk_info

    @staticmethod
    def _calculate_run_time(start_time: float) -> str:
        difference = time.time() - start_time
        days = int(difference // (24 * 60 * 60))
        hours = int((difference % (24 * 60 * 60)) // (60 * 60))
        minutes = int((difference % (60 * 60)) // 60)
        return f"{days}天{hours}小时{minutes}分钟"
