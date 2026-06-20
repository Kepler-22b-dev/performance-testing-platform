"""系统监控 API 模块。

提供服务器系统指标（CPU、内存、磁盘）、JMeter 进程状态、
网络连接与速率以及综合监控概览等接口，用于实时监控压测环境资源。
"""

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
    """获取服务器系统指标，包括 CPU、内存、磁盘使用情况。"""
    return get_system_metrics()


@router.get("/jmeter")
def jmeter_processes():
    """获取当前运行的 JMeter 进程列表及其资源占用。"""
    return {"processes": get_jmeter_processes()}


@router.get("/network")
def network_info():
    """获取网络连接信息和实时传输速率。"""
    return {
        "connections": get_network_connections(),
        "speed": get_network_speed(),
    }


@router.get("/overview")
def monitoring_overview():
    """获取系统监控综合概览，汇总 CPU、内存、磁盘、网络和 JMeter 进程状态。"""
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
