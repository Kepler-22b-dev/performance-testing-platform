import sys
import os
import json
import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REDIS_HOST, REDIS_PORT, REDIS_DB

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _get_redis():
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        decode_responses=True,
    )


def _load_config() -> dict:
    r = _get_redis()
    data = r.hget("jmeter:config", "notifications")
    if data:
        return json.loads(data)
    return {"webhooks": [], "enabled": True}


def _save_config(config: dict):
    r = _get_redis()
    r.hset("jmeter:config", "notifications", json.dumps(config, ensure_ascii=False))


class WebhookAddRequest(BaseModel):
    url: str
    name: str = ""


class NotificationConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    webhooks: Optional[list[str]] = None


@router.get("/config")
def get_notification_config():
    return _load_config()


@router.put("/config")
def update_notification_config(req: NotificationConfigRequest):
    config = _load_config()
    if req.enabled is not None:
        config["enabled"] = req.enabled
    if req.webhooks is not None:
        config["webhooks"] = req.webhooks
    _save_config(config)
    return {"status": "updated", "config": config}


@router.post("/webhook")
def add_webhook(req: WebhookAddRequest):
    config = _load_config()
    if req.url in config["webhooks"]:
        raise HTTPException(status_code=400, detail="Webhook URL 已存在")
    config["webhooks"].append(req.url)
    _save_config(config)
    return {"status": "added", "webhooks": config["webhooks"]}


@router.delete("/webhook")
def remove_webhook(url: str):
    config = _load_config()
    if url not in config["webhooks"]:
        raise HTTPException(status_code=404, detail="Webhook 不存在")
    config["webhooks"].remove(url)
    _save_config(config)
    return {"status": "removed", "webhooks": config["webhooks"]}


@router.post("/webhook/test")
def test_webhook(req: WebhookAddRequest):
    import urllib.request
    try:
        payload = json.dumps({
            "event": "test",
            "message": "这是一条测试通知",
            "timestamp": __import__("time").time(),
        }).encode()
        http_req = urllib.request.Request(
            req.url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(http_req, timeout=10)
        return {"status": "success", "code": resp.status}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
