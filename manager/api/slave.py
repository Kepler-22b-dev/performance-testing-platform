"""分布式 Slave 节点管理 API 模块。

提供 Slave 服务的状态查询、启动和停止接口，用于管理 JMeter 分布式测试中的 Slave 节点。
"""

import sys
import os
from fastapi import APIRouter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from manager.core.slave_manager import get_slave_status, start_slave, stop_slave

router = APIRouter(prefix="/api/slave", tags=["slave"])


@router.get("/status")
def slave_status():
    """获取当前 Slave 服务的运行状态。"""
    return get_slave_status()


@router.post("/start")
def slave_start(port: int = None):
    """启动 Slave 服务，可指定监听端口。"""
    return start_slave(port)


@router.post("/stop")
def slave_stop():
    """停止正在运行的 Slave 服务。"""
    return stop_slave()
