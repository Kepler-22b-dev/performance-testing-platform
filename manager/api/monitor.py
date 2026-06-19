import sys
import os
from fastapi import APIRouter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from manager.core.monitor import (
    get_system_metrics, get_jmeter_processes,
    get_network_connections, get_network_speed,
)

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/system")
def system_metrics():
    return get_system_metrics()


@router.get("/jmeter")
def jmeter_processes():
    return {"processes": get_jmeter_processes()}


@router.get("/network")
def network_info():
    return {
        "connections": get_network_connections(),
        "speed": get_network_speed(),
    }


@router.get("/overview")
def monitoring_overview():
    metrics = get_system_metrics()
    jmeter = get_jmeter_processes()
    net = get_network_speed()

    return {
        "cpu_percent": metrics["cpu"]["percent"],
        "cpu_load": metrics["cpu"]["load_1"],
        "memory_percent": metrics["memory"]["percent"],
        "memory_used_gb": round(metrics["memory"]["used"] / (1024**3), 2),
        "memory_total_gb": round(metrics["memory"]["total"] / (1024**3), 2),
        "disk_percent": metrics["disk"]["percent"],
        "disk_used_gb": round(metrics["disk"]["used"] / (1024**3), 2),
        "net_sent_speed": net["speed_sent"],
        "net_recv_speed": net["speed_recv"],
        "jmeter_count": len(jmeter),
        "jmeter_cpu": sum(p["cpu_percent"] for p in jmeter),
        "jmeter_memory": sum(p["memory_percent"] for p in jmeter),
    }
