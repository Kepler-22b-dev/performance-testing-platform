import sys
import os
import time
import psutil
import socket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def get_system_metrics() -> dict:
    cpu_percent = psutil.cpu_percent(interval=0.5)
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
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_percent", "num_threads"]):
        try:
            info = proc.info
            cmdline = " ".join(info.get("cmdline") or [])
            if "jmeter" in cmdline.lower() or "ApacheJMeter" in cmdline:
                processes.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu_percent": round(info.get("cpu_percent", 0), 1),
                    "memory_percent": round(info.get("memory_percent", 0), 1),
                    "threads": info.get("num_threads", 0),
                    "cmdline": cmdline[:200],
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return processes


def get_network_connections() -> list:
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
