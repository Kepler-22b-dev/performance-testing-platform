import sys
import os
import json
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

router = APIRouter(prefix="/api/templates", tags=["templates"])

TEMPLATES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config", "templates.json",
)

BUILTIN_TEMPLATES = [
    {
        "template_id": "builtin-smoke",
        "name": "冒烟测试",
        "description": "低并发快速验证接口可用性，确认基本功能正常",
        "category": "基础",
        "config": {
            "threads": 1,
            "ramp_time": 1,
            "duration": 30,
            "timeout": 120,
        },
        "builtin": True,
    },
    {
        "template_id": "builtin-load",
        "name": "负载测试",
        "description": "中等并发持续压测，评估系统在正常负载下的表现",
        "category": "基础",
        "config": {
            "threads": 50,
            "ramp_time": 10,
            "duration": 300,
            "timeout": 600,
        },
        "builtin": True,
    },
    {
        "template_id": "builtin-stress",
        "name": "压力测试",
        "description": "高并发持续压测，找到系统性能瓶颈和极限",
        "category": "基础",
        "config": {
            "threads": 200,
            "ramp_time": 30,
            "duration": 600,
            "timeout": 900,
        },
        "builtin": True,
    },
    {
        "template_id": "builtin-step",
        "name": "阶梯加压",
        "description": "逐步增加并发数，观察系统性能随负载变化的趋势",
        "category": "进阶",
        "config": {
            "threads": 100,
            "ramp_time": 60,
            "duration": 600,
            "timeout": 900,
            "description": "建议在JMeter脚本中配置Stepping Thread Group插件实现阶梯效果",
        },
        "builtin": True,
    },
    {
        "template_id": "builtin-peak",
        "name": "峰值测试",
        "description": "短时间大量并发冲击，验证系统抗突发流量能力",
        "category": "进阶",
        "config": {
            "threads": 500,
            "ramp_time": 5,
            "duration": 120,
            "timeout": 300,
        },
        "builtin": True,
    },
    {
        "template_id": "builtin-endurance",
        "name": "稳定性测试",
        "description": "长时间中等负载运行，检测内存泄漏和性能衰退",
        "category": "进阶",
        "config": {
            "threads": 50,
            "ramp_time": 10,
            "duration": 3600,
            "timeout": 4200,
        },
        "builtin": True,
    },
    {
        "template_id": "builtin-spike",
        "name": "尖刺测试",
        "description": "瞬间极高并发后快速释放，测试系统恢复能力",
        "category": "进阶",
        "config": {
            "threads": 1000,
            "ramp_time": 1,
            "duration": 30,
            "timeout": 120,
        },
        "builtin": True,
    },
    {
        "template_id": "builtin-api",
        "name": "API 接口测试",
        "description": "单接口循环调用，适合功能验证和基础性能评估",
        "category": "场景",
        "config": {
            "threads": 10,
            "ramp_time": 1,
            "duration": 60,
            "timeout": 120,
        },
        "builtin": True,
    },
]


def _ensure_dir():
    os.makedirs(os.path.dirname(TEMPLATES_FILE), exist_ok=True)


def _load_templates() -> list:
    _ensure_dir()
    custom = []
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r") as f:
                custom = json.load(f)
        except Exception:
            custom = []
    return BUILTIN_TEMPLATES + custom


def _save_custom_templates(templates: list):
    _ensure_dir()
    custom = [t for t in templates if not t.get("builtin")]
    with open(TEMPLATES_FILE, "w") as f:
        json.dump(custom, f, indent=2, ensure_ascii=False)


class TemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "自定义"
    config: dict = {}


@router.get("/")
def list_templates():
    templates = _load_templates()
    return {"total": len(templates), "templates": templates}


@router.get("/{template_id}")
def get_template(template_id: str):
    templates = _load_templates()
    for t in templates:
        if t["template_id"] == template_id:
            return t
    raise HTTPException(status_code=404, detail="模板不存在")


@router.post("/")
def create_template(req: TemplateCreateRequest):
    templates = _load_templates()

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

    templates.append(template)
    _save_custom_templates(templates)
    return {"status": "created", "template": template}


@router.delete("/{template_id}")
def delete_template(template_id: str):
    templates = _load_templates()
    target = None
    for t in templates:
        if t["template_id"] == template_id:
            target = t
            break

    if not target:
        raise HTTPException(status_code=404, detail="模板不存在")
    if target.get("builtin"):
        raise HTTPException(status_code=400, detail="不能删除内置模板")

    templates = [t for t in templates if t["template_id"] != template_id]
    _save_custom_templates(templates)
    return {"status": "deleted", "template_id": template_id}
