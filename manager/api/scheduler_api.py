"""定时调度管理 API 模块。

提供压测任务的定时调度配置管理接口，支持 cron 表达式、固定间隔和单次定时执行，
通过后台线程轮询实现自动触发压测任务。
"""

import sys
import os
import json
import time
import uuid
import redis
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REDIS_HOST, REDIS_PORT, REDIS_DB

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

_scheduler = None
_schedules: dict[str, dict] = {}
_scheduler_thread: Optional[threading.Thread] = None
_scheduler_running = False


def set_scheduler(scheduler):
    """注入 Scheduler 实例供调度 API 使用。

    Args:
        scheduler: Scheduler 实例，提供任务创建和启动方法。
    """
    global _scheduler
    _scheduler = scheduler


def _get_redis():
    """获取 Redis 连接实例。"""
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        decode_responses=True,
    )


def _load_schedules():
    """从 Redis 加载所有调度配置。"""
    global _schedules
    r = _get_redis()
    data = r.hget("jmeter:config", "schedules")
    if data:
        _schedules = json.loads(data)
    else:
        _schedules = {}


def _save_schedules():
    """将调度配置保存到 Redis。"""
    r = _get_redis()
    r.hset("jmeter:config", "schedules", json.dumps(_schedules, ensure_ascii=False, default=str))


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


@router.get("/")
def list_schedules():
    """获取所有调度配置的列表。"""
    _load_schedules()
    return {"total": len(_schedules), "schedules": list(_schedules.values())}


@router.get("/{schedule_id}")
def get_schedule(schedule_id: str):
    """获取指定调度配置的详细信息。"""
    _load_schedules()
    if schedule_id not in _schedules:
        raise HTTPException(status_code=404, detail="调度不存在")
    return _schedules[schedule_id]


@router.post("/")
def create_schedule(req: ScheduleCreateRequest):
    """创建一个新的定时调度配置。"""
    _load_schedules()

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

    _schedules[schedule_id] = schedule
    _save_schedules()
    return {"status": "created", "schedule": schedule}


@router.put("/{schedule_id}")
def update_schedule(schedule_id: str, req: ScheduleUpdateRequest):
    """更新指定调度配置的参数。"""
    _load_schedules()
    if schedule_id not in _schedules:
        raise HTTPException(status_code=404, detail="调度不存在")

    s = _schedules[schedule_id]
    if req.name is not None:
        s["name"] = req.name
    if req.enabled is not None:
        s["enabled"] = req.enabled
    if req.cron_expr is not None:
        s["cron_expr"] = req.cron_expr
    if req.interval_seconds is not None:
        s["interval_seconds"] = req.interval_seconds
    if req.run_at is not None:
        s["run_at"] = req.run_at

    s["next_run"] = _calc_next_run(s.get("run_at"), s.get("interval_seconds", 0))
    _save_schedules()
    return {"status": "updated", "schedule": s}


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: str):
    """删除指定的调度配置。"""
    _load_schedules()
    if schedule_id not in _schedules:
        raise HTTPException(status_code=404, detail="调度不存在")
    del _schedules[schedule_id]
    _save_schedules()
    return {"status": "deleted", "schedule_id": schedule_id}


@router.post("/{schedule_id}/run-now")
def run_now(schedule_id: str):
    """立即执行指定的调度任务。"""
    _load_schedules()
    if schedule_id not in _schedules:
        raise HTTPException(status_code=404, detail="调度不存在")

    s = _schedules[schedule_id]
    return _execute_schedule(s)


def _calc_next_run(run_at, interval_seconds):
    """计算下次执行时间，优先使用指定时间，其次使用间隔时间。"""
    if run_at and run_at > time.time():
        return run_at
    if interval_seconds and interval_seconds > 0:
        return time.time() + interval_seconds
    return None


def _execute_schedule(schedule: dict) -> dict:
    """执行一个调度任务，创建并启动压测任务。"""
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

        _save_schedules()
        return {"status": "started", "task_id": task_id, "schedule": schedule}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _start_scheduler_loop():
    """启动后台调度轮询线程，每 10 秒检查并执行到期的任务。"""
    global _scheduler_running, _scheduler_thread

    if _scheduler_running:
        return

    _scheduler_running = True

    def loop():
        while _scheduler_running:
            _load_schedules()
            now = time.time()
            for sid, s in list(_schedules.items()):
                if not s.get("enabled"):
                    continue
                next_run = s.get("next_run")
                if next_run and now >= next_run:
                    _execute_schedule(s)
            time.sleep(10)

    _scheduler_thread = threading.Thread(target=loop, daemon=True)
    _scheduler_thread.start()


def init_scheduler_loop():
    """初始化并启动调度器后台循环。"""
    _load_schedules()
    _start_scheduler_loop()
