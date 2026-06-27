"""定时调度管理 API 模块。"""

import sys
import os
import json
import time
import uuid
import threading
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.database import get_db, get_sync_db
from manager.core.db_sync import (
    db_get_all_schedules, db_get_schedule, db_create_schedule,
    db_update_schedule, db_delete_schedule,
)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

_scheduler = None
_schedules: dict[str, dict] = {}
_scheduler_thread: Optional[threading.Thread] = None
_scheduler_running = False


def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


class ScheduleCreateRequest(BaseModel):
    name: str
    script_id: str
    target_agents: list[str] = []
    jmeter_args: dict = {}
    timeout: int = 3600
    distributed: bool = False
    remote_hosts: Optional[str] = None
    cron_expr: str = ""
    interval_seconds: int = 0
    run_at: Optional[float] = None
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    run_at: Optional[float] = None


def _load_schedules():
    global _schedules
    try:
        db = get_sync_db()
        try:
            schedules_list = db_get_all_schedules(db)
            _schedules = {s["schedule_id"]: s for s in schedules_list}
        finally:
            db.close()
    except Exception:
        _schedules = {}


def _save_schedule(schedule: dict):
    try:
        db = get_sync_db()
        try:
            existing = db_get_schedule(db, schedule["schedule_id"])
            if existing:
                db_update_schedule(db, schedule["schedule_id"],
                    last_run=schedule.get("last_run"),
                    run_count=schedule.get("run_count", 0),
                    next_run=schedule.get("next_run"),
                    enabled=schedule.get("enabled", True),
                )
            else:
                db_create_schedule(db, schedule)
        finally:
            db.close()
    except Exception:
        pass


@router.get("/")
async def list_schedules(db: AsyncSession = Depends(get_db)):
    from manager.core.db import db_get_all_schedules as async_db_get_all_schedules
    schedules = await async_db_get_all_schedules(db)
    return {"total": len(schedules), "schedules": schedules}


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    from manager.core.db import db_get_schedule as async_db_get_schedule
    schedule = await async_db_get_schedule(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="调度不存在")
    return schedule


@router.post("/")
async def create_schedule(req: ScheduleCreateRequest, db: AsyncSession = Depends(get_db)):
    from manager.core.db import db_create_schedule as async_db_create_schedule
    schedule_id = f"sch-{uuid.uuid4().hex[:8]}"
    schedule = {
        "schedule_id": schedule_id,
        "name": req.name,
        "script_id": req.script_id,
        "target_agents": req.target_agents,
        "jmeter_args": req.jmeter_args,
        "timeout": req.timeout,
        "distributed": req.distributed,
        "remote_hosts": req.remote_hosts,
        "cron_expr": req.cron_expr,
        "interval_seconds": req.interval_seconds,
        "run_at": req.run_at,
        "enabled": req.enabled,
        "created_at": time.time(),
        "last_run": None,
        "next_run": _calc_next_run(req.run_at, req.interval_seconds),
        "run_count": 0,
    }
    await async_db_create_schedule(db, schedule)
    return {"status": "created", "schedule": schedule}


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: str, req: ScheduleUpdateRequest,
                          db: AsyncSession = Depends(get_db)):
    from manager.core.db import (
        db_get_schedule as async_db_get_schedule,
        db_update_schedule as async_db_update_schedule,
    )
    schedule = await async_db_get_schedule(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="调度不存在")

    update_data = {}
    if req.name is not None:
        update_data["name"] = req.name
    if req.enabled is not None:
        update_data["enabled"] = req.enabled
    if req.cron_expr is not None:
        update_data["cron_expr"] = req.cron_expr
    if req.interval_seconds is not None:
        update_data["interval_seconds"] = req.interval_seconds
    if req.run_at is not None:
        update_data["run_at"] = req.run_at

    if update_data:
        update_data["next_run"] = _calc_next_run(
            update_data.get("run_at", schedule.get("run_at")),
            update_data.get("interval_seconds", schedule.get("interval_seconds", 0)),
        )
        await async_db_update_schedule(db, schedule_id, **update_data)

    return {"status": "updated", "schedule_id": schedule_id}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    from manager.core.db import db_delete_schedule as async_db_delete_schedule
    deleted = await async_db_delete_schedule(db, schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="调度不存在")
    return {"status": "deleted", "schedule_id": schedule_id}


@router.post("/{schedule_id}/run-now")
async def run_now(schedule_id: str, db: AsyncSession = Depends(get_db)):
    import asyncio
    from manager.core.db import db_get_schedule as async_db_get_schedule
    schedule = await async_db_get_schedule(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="调度不存在")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _execute_schedule, schedule)


def _calc_next_run(run_at, interval_seconds):
    if run_at and run_at > time.time():
        return run_at
    if interval_seconds and interval_seconds > 0:
        return time.time() + interval_seconds
    return None


def _execute_schedule(schedule: dict) -> dict:
    global _scheduler
    if not _scheduler:
        return {"status": "error", "message": "Scheduler 未初始化"}

    try:
        task_id = _scheduler.create_task(
            script_id=schedule["script_id"],
            target_agents=schedule.get("target_agents", []),
            jmeter_args=schedule.get("jmeter_args", {}),
            timeout=schedule.get("timeout", 3600),
            distributed=schedule.get("distributed", False),
            remote_hosts=schedule.get("remote_hosts"),
        )
        _scheduler.start_task(task_id)

        schedule["last_run"] = time.time()
        schedule["run_count"] = schedule.get("run_count", 0) + 1

        if schedule.get("interval_seconds") and schedule["interval_seconds"] > 0:
            schedule["next_run"] = time.time() + schedule["interval_seconds"]
        elif schedule.get("run_at"):
            schedule["next_run"] = None
        else:
            schedule["enabled"] = False
            schedule["next_run"] = None

        _save_schedule(schedule)
        return {"status": "started", "task_id": task_id, "schedule": schedule}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _start_scheduler_loop():
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        return
    _scheduler_running = True

    def loop():
        last_cleanup = 0
        while _scheduler_running:
            _load_schedules()
            now = time.time()
            for sid, s in list(_schedules.items()):
                if not s.get("enabled"):
                    continue
                next_run = s.get("next_run")
                if next_run and now >= next_run:
                    _execute_schedule(s)
            if now - last_cleanup > 60:
                if _scheduler:
                    _scheduler._cleanup_stuck_tasks()
                last_cleanup = now
            time.sleep(10)

    _scheduler_thread = threading.Thread(target=loop, daemon=True)
    _scheduler_thread.start()


@router.post("/cleanup")
def manual_cleanup():
    """手动触发清理卡住的任务"""
    if not _scheduler:
        raise HTTPException(status_code=500, detail="Scheduler 未初始化")
    _scheduler._cleanup_stuck_tasks()
    return {"status": "ok", "message": "已触发 cleanup"}


@router.post("/cleanup/{task_id}")
def force_cleanup_task(task_id: str):
    """强制将指定任务标记为失败"""
    if not _scheduler:
        raise HTTPException(status_code=500, detail="Scheduler 未初始化")
    task = _scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "running":
        return {"status": "skipped", "message": f"任务状态为 {task['status']}，无需清理"}
    db = get_sync_db()
    try:
        from manager.core.db_sync import db_update_task
        db_update_task(db, task_id,
            status="failed", end_time=time.time(),
            error_message="手动强制清理",
        )
    finally:
        db.close()
    return {"status": "ok", "message": f"任务 {task_id} 已标记为 failed"}


def init_scheduler_loop():
    _load_schedules()
    _start_scheduler_loop()
