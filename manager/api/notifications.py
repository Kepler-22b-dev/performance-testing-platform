"""通知管理 API 模块。"""

import sys
import os
import json
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.database import get_db
from manager.core.db import db_get_notification_config, db_save_notification_config

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class WebhookAddRequest(BaseModel):
    url: str
    name: str = ""


class NotificationConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    webhooks: Optional[list[str]] = None


@router.get("/config")
async def get_notification_config(db: AsyncSession = Depends(get_db)):
    return await db_get_notification_config(db)


@router.put("/config")
async def update_notification_config(req: NotificationConfigRequest,
                                     db: AsyncSession = Depends(get_db)):
    config = await db_get_notification_config(db)
    if req.enabled is not None:
        config["enabled"] = req.enabled
    if req.webhooks is not None:
        config["webhooks"] = req.webhooks
    await db_save_notification_config(db, config)
    return {"status": "updated", "config": config}


@router.post("/webhook")
async def add_webhook(req: WebhookAddRequest, db: AsyncSession = Depends(get_db)):
    config = await db_get_notification_config(db)
    if req.url in config["webhooks"]:
        raise HTTPException(status_code=400, detail="Webhook URL 已存在")
    config["webhooks"].append(req.url)
    await db_save_notification_config(db, config)
    return {"status": "added", "webhooks": config["webhooks"]}


@router.delete("/webhook")
async def remove_webhook(url: str, db: AsyncSession = Depends(get_db)):
    config = await db_get_notification_config(db)
    if url not in config["webhooks"]:
        raise HTTPException(status_code=404, detail="Webhook 不存在")
    config["webhooks"].remove(url)
    await db_save_notification_config(db, config)
    return {"status": "removed", "webhooks": config["webhooks"]}


@router.post("/webhook/test")
async def test_webhook(req: WebhookAddRequest):
    import asyncio
    import urllib.request

    def _test():
        payload = json.dumps({
            "event": "test",
            "message": "这是一条测试通知",
            "timestamp": __import__("time").time(),
        }).encode()
        http_req = urllib.request.Request(
            req.url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(http_req, timeout=10)
        return {"status": "success", "code": resp.status}

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _test)
    except Exception as e:
        return {"status": "failed", "error": str(e)}
