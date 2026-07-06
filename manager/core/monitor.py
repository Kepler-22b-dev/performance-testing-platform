"""系统监控模块。

提供 Manager 节点的系统资源监控功能，包括 CPU、内存、磁盘、
网络等系统指标采集，JMeter 进程监控，以及网络连接和速率统计。
"""

import sys
import os
import time
import threading
import psutil
import socket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

_cpu_sample_interval = 1
_last_cpu_percent = 0


def _sample_cpu():
    global _last_cpu_percent
    while True:
        _last_cpu_percent = psutil.cpu_percent(interval=_cpu_sample_interval)


_cpu_thread = threading.Thread(target=_sample_cpu, daemon=True)
_cpu_thread.start()


def get_system_metrics() -> dict:
    """采集当前系统的综合性能指标。

    包含 CPU 使用率与频率、内存与 Swap、磁盘使用、
    网络流量统计及系统负载等信息。
    """
    cpu_percent = _last_cpu_percent
    cpu_count = psutil.cpu_count()

    try:
        cpu_freq = psutil.cpu_freq()
        freq_current = round(cpu_freq.current, 0) if cpu_freq else 0
        freq_max = round(cpu_freq.max, 0) if cpu_freq else 0
    except Exception:
        freq_current = 0
        freq_max = 0

    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    disk = psutil.disk_usage("/")

    net = psutil.net_io_counters()

    load = os.getloadavg()

    return {
        "timestamp": time.time(),
        "cpu": {
            "percent": cpu_percent,
            "count": cpu_count,
            "freq_current": freq_current,
            "freq_max": freq_max,
            "load_1": round(load[0], 2),
            "load_5": round(load[1], 2),
            "load_15": round(load[2], 2),
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
            "percent": mem.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_percent": swap.percent,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
            "errin": net.errin,
            "errout": net.errout,
        },
        "hostname": socket.gethostname(),
    }


def get_jmeter_processes() -> list:
    """获取当前系统中所有 JMeter 相关进程的信息。

    返回每个进程的 PID、名称、CPU 占用、内存占用、内存MB、线程数、命令行和角色。
    """
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_percent", "num_threads", "memory_info"]):
        try:
            info = proc.info
            cmdline = " ".join(info.get("cmdline") or [])
            name = info.get("name", "")

            is_jmeter = False
            if "jmeter-server" in cmdline:
                is_jmeter = True
            elif name == "java" and ("ApacheJMeter" in cmdline or "jmeter" in cmdline.lower()):
                is_jmeter = True
            elif name in ("bash", "sh") and "jmeter" in cmdline:
                is_jmeter = True

            if not is_jmeter:
                continue

            try:
                mem_mb = round(info["memory_info"].rss / 1024 / 1024, 1)
            except (AttributeError, TypeError):
                mem_mb = 0

            if name == "java":
                role = "java主进程"
            elif "jmeter-server" in cmdline:
                role = "server"
            else:
                role = "client"

            processes.append({
                "pid": info["pid"],
                "name": name,
                "cpu_percent": round(info.get("cpu_percent", 0), 1),
                "memory_percent": round(info.get("memory_percent", 0), 1),
                "memory_mb": mem_mb,
                "threads": info.get("num_threads", 0),
                "cmdline": cmdline[:200],
                "role": role,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return processes


def get_network_connections() -> list:
    """获取当前系统所有已建立的 TCP 网络连接。"""
    conns = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == "ESTABLISHED":
                laddr = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "-"
                raddr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "-"
                conns.append({
                    "local": laddr,
                    "remote": raddr,
                    "status": conn.status,
                    "pid": conn.pid,
                })
    except psutil.AccessDenied:
        pass
    return conns


_prev_net = None
_prev_time = None


def get_network_speed() -> dict:
    """计算当前网络发送和接收速率（字节/秒）。

    通过与上次采样数据对比计算瞬时速率。
    """
    global _prev_net, _prev_time

    net = psutil.net_io_counters()
    now = time.time()

    result = {
        "bytes_sent": net.bytes_sent,
        "bytes_recv": net.bytes_recv,
        "speed_sent": 0,
        "speed_recv": 0,
    }

    if _prev_net and _prev_time:
        dt = now - _prev_time
        if dt > 0:
            result["speed_sent"] = round((net.bytes_sent - _prev_net.bytes_sent) / dt)
            result["speed_recv"] = round((net.bytes_recv - _prev_net.bytes_recv) / dt)

    _prev_net = net
    _prev_time = now

    return result
