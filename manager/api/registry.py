import sys
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from manager.core.node_registry import (
    get_all_nodes, get_node, add_node, remove_node,
    verify_node, verify_all, get_verified_nodes,
)

router = APIRouter(prefix="/api/registry", tags=["registry"])


class AddNodeRequest(BaseModel):
    ip: str
    port: int = 1100
    name: Optional[str] = None


@router.get("/")
def list_nodes():
    nodes = get_all_nodes()
    return {"total": len(nodes), "nodes": nodes}


@router.get("/verified")
def list_verified():
    nodes = get_verified_nodes()
    return {"total": len(nodes), "nodes": nodes}


@router.get("/{node_id}")
def get_node_detail(node_id: str):
    node = get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


@router.post("/")
def add_new_node(req: AddNodeRequest):
    result = add_node(ip=req.ip, port=req.port, name=req.name)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.delete("/{node_id}")
def delete_node(node_id: str):
    result = remove_node(node_id)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/{node_id}/verify")
def verify_single_node(node_id: str):
    result = verify_node(node_id)
    return result


@router.post("/verify-all")
def verify_all_nodes():
    results = verify_all()
    passed = sum(1 for r in results if r["overall"] == "verified")
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
