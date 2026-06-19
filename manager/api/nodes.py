import sys
import os
from fastapi import APIRouter, HTTPException
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

_node_manager = None


def set_node_manager(nm):
    global _node_manager
    _node_manager = nm


@router.get("/")
def list_nodes():
    agents = _node_manager.get_agents()
    return {
        "total": len(agents),
        "online": len([a for a in agents if a.status == "online"]),
        "agents": [a.to_dict() for a in agents],
    }


@router.get("/{agent_id}")
def get_node(agent_id: str):
    agent = _node_manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.to_dict()


@router.get("/available/list")
def list_available_nodes():
    agents = _node_manager.get_available_agents()
    return {
        "total": len(agents),
        "agents": [a.to_dict() for a in agents],
    }
