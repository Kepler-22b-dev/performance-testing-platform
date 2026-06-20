"""节点注册中心 API 模块。

提供测试节点的注册、删除、查询、连通性验证等接口，
用于管理分布式压测环境中的节点注册信息和健康状态。
"""

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
    """获取所有已注册节点的列表。"""
    nodes = get_all_nodes()
    return {"total": len(nodes), "nodes": nodes}


@router.get("/verified")
def list_verified():
    """获取所有已通过连通性验证的节点列表。"""
    nodes = get_verified_nodes()
    return {"total": len(nodes), "nodes": nodes}


@router.get("/{node_id}")
def get_node_detail(node_id: str):
    """获取指定节点的详细信息。"""
    node = get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


@router.post("/")
def add_new_node(req: AddNodeRequest):
    """注册一个新的测试节点。"""
    result = add_node(ip=req.ip, port=req.port, name=req.name)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.delete("/{node_id}")
def delete_node(node_id: str):
    """删除指定的已注册节点。"""
    result = remove_node(node_id)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/{node_id}/verify")
def verify_single_node(node_id: str):
    """验证指定节点的连通性和环境配置。"""
    result = verify_node(node_id)
    return result


@router.post("/verify-all")
def verify_all_nodes():
    """批量验证所有已注册节点的连通性和环境配置。"""
    results = verify_all()
    passed = sum(1 for r in results if r["overall"] == "verified")
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
