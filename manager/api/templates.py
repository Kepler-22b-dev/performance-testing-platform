"""压测场景模板管理 API 模块。"""

import sys
import os
import time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.database import get_db
from manager.core.db import db_get_all_templates, db_create_template, db_delete_template

router = APIRouter(prefix="/api/templates", tags=["templates"])

BUILTIN_TEMPLATES = [
    {"template_id": "builtin-smoke", "name": "冒烟测试", "description": "低并发快速验证接口可用性，确认基本功能正常", "category": "基础", "config": {"threads": 1, "ramp_time": 1, "duration": 30, "timeout": 120}, "builtin": True},
    {"template_id": "builtin-load", "name": "负载测试", "description": "中等并发持续压测，评估系统在正常负载下的表现", "category": "基础", "config": {"threads": 50, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-stress", "name": "压力测试", "description": "高并发持续压测，找到系统性能瓶颈和极限", "category": "基础", "config": {"threads": 200, "ramp_time": 30, "duration": 600, "timeout": 900}, "builtin": True},
    {"template_id": "builtin-step", "name": "阶梯加压", "description": "逐步增加并发数，观察系统性能随负载变化的趋势", "category": "进阶", "config": {"threads": 100, "ramp_time": 60, "duration": 600, "timeout": 900}, "builtin": True},
    {"template_id": "builtin-peak", "name": "峰值测试", "description": "短时间大量并发冲击，验证系统抗突发流量能力", "category": "进阶", "config": {"threads": 500, "ramp_time": 5, "duration": 120, "timeout": 300}, "builtin": True},
    {"template_id": "builtin-endurance", "name": "稳定性测试", "description": "长时间中等负载运行，检测内存泄漏和性能衰退", "category": "进阶", "config": {"threads": 50, "ramp_time": 10, "duration": 3600, "timeout": 4200}, "builtin": True},
    {"template_id": "builtin-spike", "name": "尖刺测试", "description": "瞬间极高并发后快速释放，测试系统恢复能力", "category": "进阶", "config": {"threads": 1000, "ramp_time": 1, "duration": 30, "timeout": 120}, "builtin": True},
    {"template_id": "builtin-api", "name": "API 接口测试", "description": "单接口循环调用，适合功能验证和基础性能评估", "category": "场景", "config": {"threads": 10, "ramp_time": 1, "duration": 60, "timeout": 120}, "builtin": True},
]


class TemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "自定义"
    config: dict = {}


@router.get("/")
async def list_templates(db: AsyncSession = Depends(get_db)):
    custom = await db_get_all_templates(db)
    templates = BUILTIN_TEMPLATES + custom
    return {"total": len(templates), "templates": templates}


@router.get("/{template_id}")
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    templates = BUILTIN_TEMPLATES + await db_get_all_templates(db)
    for t in templates:
        if t["template_id"] == template_id:
            return t
    raise HTTPException(status_code=404, detail="模板不存在")


@router.post("/")
async def create_template(req: TemplateCreateRequest, db: AsyncSession = Depends(get_db)):
    template_id = f"tpl-{int(time.time()*1000)}"
    template = {
        "template_id": template_id,
        "name": req.name,
        "description": req.description,
        "category": req.category,
        "config": req.config,
        "builtin": False,
        "created_at": time.time(),
    }
    await db_create_template(db, template)
    return {"status": "created", "template": template}


@router.delete("/{template_id}")
async def delete_template(template_id: str, db: AsyncSession = Depends(get_db)):
    for t in BUILTIN_TEMPLATES:
        if t["template_id"] == template_id:
            raise HTTPException(status_code=400, detail="不能删除内置模板")

    deleted = await db_delete_template(db, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"status": "deleted", "template_id": template_id}
