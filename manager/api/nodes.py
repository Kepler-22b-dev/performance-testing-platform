"""Agent 节点管理 API 模块。

提供执行压测任务的 Agent 节点的列表查询、详情查看和可用节点筛选接口，
用于管理分布式压测环境中的执行节点状态。
"""

import sys
import os
from fastapi import APIRouter, HTTPException
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

_node_manager = None


def set_node_manager(nm):
    """注入 NodeManager 实例供 API 路由使用。

    Args:
        nm: NodeManager 实例，提供 Agent 节点的管理方法。
    """
    global _node_manager
    _node_manager = nm


@router.get("/")
def list_nodes():
    """获取所有 Agent 节点的列表及在线数量统计。"""
    agents = _node_manager.get_agents()
    return {
        "total": len(agents),
        "online": len([a for a in agents if a.status == "online"]),
        "agents": [a.to_dict() for a in agents],
    }


@router.get("/{agent_id}")
def get_node(agent_id: str):
    """获取指定 Agent 节点的详细信息。"""
    agent = _node_manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.to_dict()


@router.get("/available/list")
def list_available_nodes():
    """获取当前空闲可用的 Agent 节点列表。"""
    agents = _node_manager.get_available_agents()
    return {
        "total": len(agents),
        "agents": [a.to_dict() for a in agents],
    }
