"""告警规则管理 API 模块。"""

import sys
import os
import json
import time
import uuid
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.database import get_db, get_sync_db
from manager.core.db import (
    db_get_all_alert_rules, db_create_alert_rule,
    db_update_alert_rule, db_delete_alert_rule,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertRuleCreate(BaseModel):
    name: str
    metric: str = "avg_response_time"
    operator: str = ">"
    threshold: float = 1000
    enabled: bool = True
    notify_webhook: bool = True
    description: str = ""


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    metric: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None
    enabled: Optional[bool] = None
    notify_webhook: Optional[bool] = None
    description: Optional[str] = None


METRICS = {
    "avg_response_time": "平均响应时间(ms)",
    "p90": "P90(ms)",
    "p95": "P95(ms)",
    "p99": "P99(ms)",
    "error_rate": "错误率(%)",
    "tps": "TPS",
    "total_samples": "总请求数",
}


@router.get("/metrics")
def list_metrics():
    return {"metrics": [{"key": k, "name": v} for k, v in METRICS.items()]}


@router.get("/")
async def list_rules(db: AsyncSession = Depends(get_db)):
    rules = await db_get_all_alert_rules(db)
    return {"total": len(rules), "rules": rules}


@router.post("/")
async def create_rule(req: AlertRuleCreate, db: AsyncSession = Depends(get_db)):
    rule_id = f"alert-{uuid.uuid4().hex[:8]}"
    rule = {
        "rule_id": rule_id,
        "name": req.name,
        "metric": req.metric,
        "operator": req.operator,
        "threshold": req.threshold,
        "enabled": req.enabled,
        "notify_webhook": req.notify_webhook,
        "description": req.description,
        "created_at": time.time(),
        "triggered_count": 0,
        "last_triggered": None,
    }
    await db_create_alert_rule(db, rule)
    return {"status": "created", "rule": rule}


@router.put("/{rule_id}")
async def update_rule(rule_id: str, req: AlertRuleUpdate, db: AsyncSession = Depends(get_db)):
    update_data = {}
    if req.name is not None:
        update_data["name"] = req.name
    if req.metric is not None:
        update_data["metric"] = req.metric
    if req.operator is not None:
        update_data["operator"] = req.operator
    if req.threshold is not None:
        update_data["threshold"] = req.threshold
    if req.enabled is not None:
        update_data["enabled"] = req.enabled
    if req.notify_webhook is not None:
        update_data["notify_webhook"] = req.notify_webhook
    if req.description is not None:
        update_data["description"] = req.description

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = await db_update_alert_rule(db, rule_id, **update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "updated", "rule_id": rule_id}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await db_delete_alert_rule(db, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "deleted", "rule_id": rule_id}


def check_alerts(task_result: dict) -> list:
    """检查任务结果是否触发告警规则，返回触发的规则列表。"""
    from manager.core.db_sync import db_get_all_alert_rules as sync_db_get_all_alert_rules
    from manager.core.db_sync import db_update_alert_rule as sync_db_update_alert_rule

    db = get_sync_db()
    try:
        rules = sync_db_get_all_alert_rules(db)
    finally:
        db.close()

    triggered = []

    summary = {}
    for agent_id, r in task_result.get("results", {}).items():
        s = r.get("summary", {})
        if s.get("total_samples"):
            summary = s
            break

    if not summary:
        return triggered

    for rule in rules:
        if not rule.get("enabled"):
            continue

        metric = rule["metric"]
        value = summary.get(metric)
        if value is None:
            continue

        threshold = rule["threshold"]
        operator = rule["operator"]
        exceeded = False

        if operator == ">" and value > threshold:
            exceeded = True
        elif operator == ">=" and value >= threshold:
            exceeded = True
        elif operator == "<" and value < threshold:
            exceeded = True
        elif operator == "<=" and value <= threshold:
            exceeded = True
        elif operator == "==" and value == threshold:
            exceeded = True

        if exceeded:
            triggered.append({
                "rule_id": rule["rule_id"],
                "name": rule["name"],
                "metric": metric,
                "metric_name": METRICS.get(metric, metric),
                "operator": operator,
                "threshold": threshold,
                "actual_value": value,
            })

    if triggered:
        db = get_sync_db()
        try:
            for t in triggered:
                sync_db_update_alert_rule(db, t["rule_id"],
                    triggered_count=rule.get("triggered_count", 0) + 1,
                    last_triggered=time.time(),
                )
        finally:
            db.close()

    return triggered
