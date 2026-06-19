import sys
import os
import json
import time
import uuid
import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REDIS_HOST, REDIS_PORT, REDIS_DB

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _get_redis():
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        decode_responses=True,
    )


def _load_rules() -> list:
    r = _get_redis()
    data = r.hget("jmeter:config", "alert_rules")
    if data:
        return json.loads(data)
    return []


def _save_rules(rules: list):
    r = _get_redis()
    r.hset("jmeter:config", "alert_rules", json.dumps(rules, ensure_ascii=False, default=str))


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
def list_rules():
    rules = _load_rules()
    return {"total": len(rules), "rules": rules}


@router.post("/")
def create_rule(req: AlertRuleCreate):
    rules = _load_rules()
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
    rules.append(rule)
    _save_rules(rules)
    return {"status": "created", "rule": rule}


@router.put("/{rule_id}")
def update_rule(rule_id: str, req: AlertRuleUpdate):
    rules = _load_rules()
    for rule in rules:
        if rule["rule_id"] == rule_id:
            if req.name is not None:
                rule["name"] = req.name
            if req.metric is not None:
                rule["metric"] = req.metric
            if req.operator is not None:
                rule["operator"] = req.operator
            if req.threshold is not None:
                rule["threshold"] = req.threshold
            if req.enabled is not None:
                rule["enabled"] = req.enabled
            if req.notify_webhook is not None:
                rule["notify_webhook"] = req.notify_webhook
            if req.description is not None:
                rule["description"] = req.description
            _save_rules(rules)
            return {"status": "updated", "rule": rule}
    raise HTTPException(status_code=404, detail="规则不存在")


@router.delete("/{rule_id}")
def delete_rule(rule_id: str):
    rules = _load_rules()
    original = len(rules)
    rules = [r for r in rules if r["rule_id"] != rule_id]
    if len(rules) == original:
        raise HTTPException(status_code=404, detail="规则不存在")
    _save_rules(rules)
    return {"status": "deleted", "rule_id": rule_id}


def check_alerts(task_result: dict) -> list:
    rules = _load_rules()
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
            rule["triggered_count"] = rule.get("triggered_count", 0) + 1
            rule["last_triggered"] = time.time()
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
        rules_updated = _load_rules()
        for r in rules_updated:
            for t in triggered:
                if r["rule_id"] == t["rule_id"]:
                    r["triggered_count"] = r.get("triggered_count", 0) + 1
                    r["last_triggered"] = time.time()
        _save_rules(rules_updated)

    return triggered
