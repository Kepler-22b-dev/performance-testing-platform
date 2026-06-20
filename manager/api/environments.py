"""测试环境管理 API 模块。

提供测试环境的增删改查和连通性测试接口，支持配置基础 URL、自定义变量、
请求头和认证 Token，用于管理不同测试环境的连接配置。
"""

import sys
import os
import json
import time
import uuid
import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REDIS_HOST, REDIS_PORT, REDIS_DB

router = APIRouter(prefix="/api/environments", tags=["environments"])


def _get_redis():
    """获取 Redis 连接实例。"""
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        decode_responses=True,
    )


def _load_environments() -> list:
    """从 Redis 加载所有环境配置。"""
    r = _get_redis()
    data = r.hget("jmeter:config", "environments")
    if data:
        return json.loads(data)
    return []


def _save_environments(envs: list):
    """将环境配置列表保存到 Redis。"""
    r = _get_redis()
    r.hset("jmeter:config", "environments", json.dumps(envs, ensure_ascii=False, default=str))


class EnvironmentCreate(BaseModel):
    name: str
    base_url: str
    description: str = ""
    variables: Dict[str, str] = {}
    headers: Dict[str, str] = {}
    auth_token: Optional[str] = None


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    description: Optional[str] = None
    variables: Optional[Dict[str, str]] = None
    headers: Optional[Dict[str, str]] = None
    auth_token: Optional[str] = None


@router.get("/")
def list_environments():
    """获取所有测试环境的列表。"""
    envs = _load_environments()
    return {"total": len(envs), "environments": envs}


@router.get("/{env_id}")
def get_environment(env_id: str):
    """获取指定测试环境的详细配置。"""
    envs = _load_environments()
    for env in envs:
        if env["env_id"] == env_id:
            return env
    raise HTTPException(status_code=404, detail="环境不存在")


@router.post("/")
def create_environment(req: EnvironmentCreate):
    """创建一个新的测试环境配置。"""
    envs = _load_environments()

    for env in envs:
        if env["name"] == req.name:
            raise HTTPException(status_code=400, detail=f"环境名称已存在: {req.name}")

    env_id = f"env-{uuid.uuid4().hex[:8]}"
    env = {
        "env_id": env_id,
        "name": req.name,
        "base_url": req.base_url,
        "description": req.description,
        "variables": req.variables,
        "headers": req.headers,
        "auth_token": req.auth_token,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    envs.append(env)
    _save_environments(envs)
    return {"status": "created", "environment": env}


@router.put("/{env_id}")
def update_environment(env_id: str, req: EnvironmentUpdate):
    """更新指定测试环境的配置。"""
    envs = _load_environments()
    for env in envs:
        if env["env_id"] == env_id:
            if req.name is not None:
                env["name"] = req.name
            if req.base_url is not None:
                env["base_url"] = req.base_url
            if req.description is not None:
                env["description"] = req.description
            if req.variables is not None:
                env["variables"] = req.variables
            if req.headers is not None:
                env["headers"] = req.headers
            if req.auth_token is not None:
                env["auth_token"] = req.auth_token
            env["updated_at"] = time.time()
            _save_environments(envs)
            return {"status": "updated", "environment": env}
    raise HTTPException(status_code=404, detail="环境不存在")


@router.delete("/{env_id}")
def delete_environment(env_id: str):
    """删除指定的测试环境配置。"""
    envs = _load_environments()
    original = len(envs)
    envs = [e for e in envs if e["env_id"] != env_id]
    if len(envs) == original:
        raise HTTPException(status_code=404, detail="环境不存在")
    _save_environments(envs)
    return {"status": "deleted", "env_id": env_id}


@router.post("/{env_id}/test")
def test_environment(env_id: str):
    """测试指定环境的连通性，发送 HTTP 请求验证基础 URL 是否可达。"""
    envs = _load_environments()
    env = None
    for e in envs:
        if e["env_id"] == env_id:
            env = e
            break
    if not env:
        raise HTTPException(status_code=404, detail="环境不存在")

    import urllib.request
    try:
        url = env["base_url"]
        if not url.startswith("http"):
            url = "http://" + url

        req = urllib.request.Request(url, method="GET")
        for k, v in env.get("headers", {}).items():
            req.add_header(k, v)
        if env.get("auth_token"):
            req.add_header("Authorization", f"Bearer {env['auth_token']}")

        resp = urllib.request.urlopen(req, timeout=10)
        return {
            "status": "success",
            "url": url,
            "response_code": resp.status,
            "response_time": round(resp.headers.get("Date", ""), 0),
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}
