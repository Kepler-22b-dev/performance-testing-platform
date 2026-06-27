"""任务管理 API 模块。

提供压测任务的创建、启动、停止、删除、重新执行、批量创建和快速运行等接口，
通过 Scheduler 实现任务的全生命周期管理。
"""

import sys
import os
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.protocol import TaskStatus

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_scheduler = None


def set_scheduler(scheduler):
    """注入 Scheduler 实例供 API 路由使用。

    Args:
        scheduler: Scheduler 实例，提供任务管理的核心方法。
    """
    global _scheduler
    _scheduler = scheduler


class TaskCreateRequest(BaseModel):
    script_id: str
    target_agents: list[str]
    jmeter_args: dict = {}
    timeout: int = 3600
    csv_file: Optional[str] = None
    csv_variable_names: Optional[str] = None
    csv_delimiter: str = ","
    csv_recycle: bool = True
    csv_stop_on_eof: bool = False


class QuickRunRequest(BaseModel):
    """快速执行压测请求"""
    script_id: str
    target_agents: list[str] = []
    threads: int = 1
    ramp_time: int = 1
    duration: int = 10
    timeout: int = 3600
    distributed: bool = False
    remote_hosts: Optional[str] = None
    csv_file: Optional[str] = None
    csv_variable_names: Optional[str] = None
    csv_delimiter: str = ","
    csv_recycle: bool = True
    csv_stop_on_eof: bool = False
    scenario: Optional[dict] = None  # 自定义并发场景配置


class BatchTaskItem(BaseModel):
    script_id: str
    target_agents: list[str] = []
    jmeter_args: dict = {}
    timeout: int = 3600
    distributed: bool = False
    remote_hosts: Optional[str] = None
    auto_start: bool = False
    csv_file: Optional[str] = None
    csv_variable_names: Optional[str] = None
    csv_delimiter: str = ","
    csv_recycle: bool = True
    csv_stop_on_eof: bool = False


class BatchTaskRequest(BaseModel):
    tasks: list[BatchTaskItem]


class TaskStopRequest(BaseModel):
    task_id: str


@router.post("/")
def create_task(req: TaskCreateRequest):
    """创建一个新的压测任务。"""
    try:
        task_id = _scheduler.create_task(
            script_id=req.script_id,
            target_agents=req.target_agents,
            jmeter_args=req.jmeter_args,
            timeout=req.timeout,
            csv_file=req.csv_file,
            csv_variable_names=req.csv_variable_names,
            csv_delimiter=req.csv_delimiter,
            csv_recycle=req.csv_recycle,
            csv_stop_on_eof=req.csv_stop_on_eof,
        )
        return {"task_id": task_id, "message": "Task created"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/start")
def start_task(task_id: str):
    """启动指定的压测任务。"""
    success = _scheduler.start_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot start task")
    return {"message": "Task started"}


@router.post("/{task_id}/stop")
def stop_task(task_id: str):
    """停止正在运行的压测任务。"""
    success = _scheduler.stop_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot stop task")
    return {"message": "Task stopped"}


@router.get("/")
def list_tasks():
    """获取所有压测任务的列表。"""
    tasks = _scheduler.get_all_tasks()
    return {"total": len(tasks), "tasks": tasks}


@router.get("/{task_id}")
def get_task(task_id: str):
    """获取指定任务的详细信息。"""
    task = _scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/progress")
def get_progress(task_id: str):
    """获取指定任务的执行进度。"""
    progress = _scheduler.get_progress(task_id)
    if not progress:
        return {"task_id": task_id, "data": None}
    from dataclasses import asdict
    return {"task_id": task_id, "data": asdict(progress)}


@router.post("/batch")
def batch_create_tasks(req: BatchTaskRequest):
    """批量创建多个压测任务。"""
    task_ids = _scheduler.batch_create_tasks([t.model_dump() for t in req.tasks])
    return {"task_ids": task_ids, "total": len(task_ids)}


@router.delete("/{task_id}")
def delete_task(task_id: str):
    """删除指定的压测任务（仅限非运行中状态）。"""
    success = _scheduler.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法删除任务（可能正在运行中）")
    return {"message": "Task deleted"}


@router.post("/{task_id}/rerun")
def rerun_task(task_id: str):
    """重新执行指定的压测任务，生成新任务。"""
    new_id = _scheduler.rerun_task(task_id)
    if not new_id:
        raise HTTPException(status_code=400, detail="无法重新执行任务")
    return {"task_id": new_id, "message": "任务已重新执行"}


@router.post("/quick-run")
def quick_run(req: QuickRunRequest):
    """快速运行：自动选择可用 Agent 并立即启动压测任务。"""
    target = req.target_agents
    if not target:
        agents = _scheduler._node_manager.get_available_agents() if hasattr(_scheduler, '_node_manager') else []
        if not agents:
            all_agents = _scheduler._node_manager.get_agents() if hasattr(_scheduler, '_node_manager') else []
            if not all_agents:
                raise HTTPException(status_code=400, detail="没有注册的 Agent 节点，请先启动 Agent")
            online_agents = [a for a in all_agents if a.status == "online"]
            if not online_agents:
                busy_agents = [a for a in all_agents if a.status == "busy"]
                if busy_agents:
                    raise HTTPException(status_code=400, detail="Agent 正在执行其他任务，请等待当前任务完成")
                else:
                    raise HTTPException(status_code=400, detail="Agent 节点不在线，请检查 Agent 是否启动")
            raise HTTPException(status_code=400, detail="所有 Agent 节点正忙，请等待任务完成")
        target = [agents[0].agent_id]

    # 构建 JMeter 参数
    jmeter_args = {
        "threads": str(req.threads),
        "ramp_time": str(req.ramp_time),
        "duration": str(req.duration),
    }

    # 添加场景配置到 jmeter_args
    if req.scenario:
        jmeter_args["scenario"] = json.dumps(req.scenario)

    timeout = req.timeout
    if req.distributed:
        timeout = max(timeout, int(req.duration) + 60)

    try:
        task_id = _scheduler.create_task(
            script_id=req.script_id,
            target_agents=target,
            jmeter_args=jmeter_args,
            timeout=timeout,
            distributed=req.distributed,
            remote_hosts=req.remote_hosts,
            csv_file=req.csv_file,
            csv_variable_names=req.csv_variable_names,
            csv_delimiter=req.csv_delimiter,
            csv_recycle=req.csv_recycle,
            csv_stop_on_eof=req.csv_stop_on_eof,
        )
        _scheduler.start_task(task_id)
        return {"task_id": task_id, "message": "任务已创建并启动"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
