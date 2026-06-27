"""测试环境管理 API 模块。"""

import sys
import os
import time
import uuid
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.database import get_db
from manager.core.db import (
    db_get_all_environments, db_get_environment,
    db_create_environment, db_update_environment, db_delete_environment,
)

router = APIRouter(prefix="/api/environments", tags=["environments"])


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
async def list_environments(db: AsyncSession = Depends(get_db)):
    envs = await db_get_all_environments(db)
    return {"total": len(envs), "environments": envs}


@router.get("/{env_id}")
async def get_environment(env_id: str, db: AsyncSession = Depends(get_db)):
    env = await db_get_environment(db, env_id)
    if not env:
        raise HTTPException(status_code=404, detail="环境不存在")
    return env


@router.post("/")
async def create_environment(req: EnvironmentCreate, db: AsyncSession = Depends(get_db)):
    envs = await db_get_all_environments(db)
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
    await db_create_environment(db, env)
    return {"status": "created", "environment": env}


@router.put("/{env_id}")
async def update_environment(env_id: str, req: EnvironmentUpdate,
                             db: AsyncSession = Depends(get_db)):
    update_data = {}
    if req.name is not None:
        update_data["name"] = req.name
    if req.base_url is not None:
        update_data["base_url"] = req.base_url
    if req.description is not None:
        update_data["description"] = req.description
    if req.variables is not None:
        update_data["variables"] = req.variables
    if req.headers is not None:
        update_data["headers"] = req.headers
    if req.auth_token is not None:
        update_data["auth_token"] = req.auth_token

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = await db_update_environment(db, env_id, **update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="环境不存在")
    return {"status": "updated", "env_id": env_id}


@router.delete("/{env_id}")
async def delete_environment(env_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await db_delete_environment(db, env_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="环境不存在")
    return {"status": "deleted", "env_id": env_id}


@router.post("/{env_id}/test")
async def test_environment(env_id: str, db: AsyncSession = Depends(get_db)):
    env = await db_get_environment(db, env_id)
    if not env:
        raise HTTPException(status_code=404, detail="环境不存在")

    import asyncio
    import urllib.request

    def _test():
        url = env["base_url"]
        if not url.startswith("http"):
            url = "http://" + url
        req = urllib.request.Request(url, method="GET")
        for k, v in env.get("headers", {}).items():
            req.add_header(k, v)
        if env.get("auth_token"):
            req.add_header("Authorization", f"Bearer {env['auth_token']}")
        resp = urllib.request.urlopen(req, timeout=10)
        return {"status": "success", "url": url, "response_code": resp.status}

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _test)
    except Exception as e:
        return {"status": "failed", "error": str(e)}
