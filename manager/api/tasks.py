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
    timeout: Optional[int] = None
    enforce_single_agent_task: bool = True
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
    timeout: Optional[int] = None
    distributed: bool = False
    remote_hosts: Optional[str] = None
    csv_file: Optional[str] = None
    csv_variable_names: Optional[str] = None
    csv_delimiter: str = ","
    csv_recycle: bool = True
    csv_stop_on_eof: bool = False
    scenario: Optional[dict] = None  # 自定义并发场景配置
    jvm_heap_mb: Optional[int] = None
    capture_error_log: bool = True
    error_log_sample_limit: int = 100
    error_log_max_body_chars: int = 8192
    result_format: str = "csv"
    debug_result_xml: bool = False
    enforce_single_agent_task: bool = True
    jmeter_properties: Optional[dict] = None


class BatchTaskItem(BaseModel):
    script_id: str
    target_agents: list[str] = []
    jmeter_args: dict = {}
    timeout: Optional[int] = None
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


class AdjustLoadRequest(BaseModel):
    action: str = "increase"
    threads: int
    ramp_time: int = 1
    duration: int = 60


def _validate_jvm_heap_mb(heap_mb: Optional[int]):
    if heap_mb is None:
        return
    if heap_mb < 256:
        raise HTTPException(status_code=400, detail="JVM 内存不能小于 256MB")
    if heap_mb > 65536:
        raise HTTPException(status_code=400, detail="JVM 内存不能大于 65536MB")


def _normalize_jmeter_properties(properties: Optional[dict]) -> dict:
    if not properties:
        return {}

    internal_keys = {
        "threads", "ramp_time", "duration", "scenario",
        "jvm_heap_mb", "capture_error_log", "enforce_single_agent_task",
        "error_log_sample_limit", "error_log_max_body_chars",
        "result_format", "debug_result_xml",
    }
    normalized = {}
    for raw_key, value in properties.items():
        key = str(raw_key).strip()
        if not key or key in internal_keys:
            continue
        if any(ch.isspace() for ch in key):
            raise HTTPException(status_code=400, detail=f"JMeter 参数名不能包含空白字符: {key}")
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            normalized[key] = "true" if value else "false"
        else:
            normalized[key] = str(value).strip()
    return normalized


def _estimate_timeout_seconds(duration: int, ramp_time: int = 0, scenario: Optional[dict] = None, distributed: bool = False) -> int:
    """按压测场景估算任务超时，避免短场景默认给过长超时。"""
    duration = max(1, int(duration or 1))
    ramp_time = max(0, int(ramp_time or 0))
    buffer = max(60, min(300, int(duration * 0.2 + 0.999)))
    resource_load = (scenario or {}).get("resource_load") if isinstance(scenario, dict) else None
    if isinstance(resource_load, dict) and resource_load.get("enabled"):
        buffer += 60
    if distributed:
        buffer += 60
    timeout = duration + ramp_time + buffer
    return max(120, ((timeout + 9) // 10) * 10)


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _scenario_from_args(jmeter_args: dict) -> Optional[dict]:
    raw = (jmeter_args or {}).get("scenario")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _resolve_timeout_seconds(timeout: Optional[int], jmeter_args: dict, distributed: bool = False) -> int:
    jmeter_args = jmeter_args or {}
    duration = _to_int(jmeter_args.get("duration"), 60)
    ramp_time = _to_int(jmeter_args.get("ramp_time"), 0)
    scenario = _scenario_from_args(jmeter_args)
    estimated = _estimate_timeout_seconds(duration, ramp_time, scenario, distributed)
    return max(_to_int(timeout, estimated), estimated)


@router.post("/")
def create_task(req: TaskCreateRequest):
    """创建一个新的压测任务。"""
    try:
        heap_value = req.jmeter_args.get("jvm_heap_mb")
        if heap_value not in (None, ""):
            try:
                _validate_jvm_heap_mb(int(heap_value))
            except ValueError:
                raise HTTPException(status_code=400, detail="JVM 内存必须是整数 MB")

        task_id = _scheduler.create_task(
            script_id=req.script_id,
            target_agents=req.target_agents,
            jmeter_args=req.jmeter_args,
            timeout=_resolve_timeout_seconds(req.timeout, req.jmeter_args),
            csv_file=req.csv_file,
            csv_variable_names=req.csv_variable_names,
            csv_delimiter=req.csv_delimiter,
            csv_recycle=req.csv_recycle,
            csv_stop_on_eof=req.csv_stop_on_eof,
            enforce_single_agent_task=req.enforce_single_agent_task,
        )
        return {"task_id": task_id, "message": "Task created"}
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
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
def list_tasks(offset: int = 0, limit: int = 100, status: Optional[str] = None):
    """获取所有压测任务的列表。"""
    offset = max(0, int(offset or 0))
    limit = max(1, min(500, int(limit or 100)))
    result = _scheduler.get_all_tasks(offset=offset, limit=limit, status=status)
    if isinstance(result, tuple):
        total, tasks = result
    else:
        tasks = result
        total = len(tasks)
    return {"total": total, "offset": offset, "limit": limit, "tasks": tasks}


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


@router.get("/{task_id}/progress/history")
def get_progress_history(task_id: str):
    """获取指定任务的进度历史数据。"""
    history = _scheduler.get_progress_history(task_id)
    return {"task_id": task_id, "data": history}


@router.post("/batch")
def batch_create_tasks(req: BatchTaskRequest):
    """批量创建多个压测任务。"""
    tasks_config = []
    for item in req.tasks:
        cfg = item.model_dump()
        cfg["timeout"] = _resolve_timeout_seconds(cfg.get("timeout"), cfg.get("jmeter_args", {}), cfg.get("distributed", False))
        tasks_config.append(cfg)
    task_ids = _scheduler.batch_create_tasks(tasks_config)
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


@router.post("/{task_id}/stop-and-restart")
def stop_and_restart(task_id: str, req: Optional[TaskCreateRequest] = None):
    """停止当前任务并用新参数重启。"""
    overrides = {}
    if req:
        timeout = _resolve_timeout_seconds(req.timeout, req.jmeter_args)
        overrides = {
            "threads": req.jmeter_args.get("threads"),
            "ramp_time": req.jmeter_args.get("ramp_time"),
            "duration": req.jmeter_args.get("duration"),
            "target_agents": req.target_agents,
            "timeout": timeout,
        }
    new_id = _scheduler.stop_and_rerun(task_id, overrides if overrides else None)
    if not new_id:
        raise HTTPException(status_code=400, detail="无法停止并重启任务")
    return {"task_id": new_id, "message": "已停止旧任务并启动新任务"}


@router.post("/{task_id}/adjust-load")
def adjust_load(task_id: str, req: AdjustLoadRequest):
    """动态调整运行中任务的压力。"""
    try:
        result = _scheduler.adjust_load(
            task_id=task_id,
            action=req.action,
            threads=req.threads,
            ramp_time=req.ramp_time,
            duration=req.duration,
        )
        return {"message": "动态调压命令已下发", **result}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/quick-run")
def quick_run(req: QuickRunRequest):
    """快速运行：自动选择可用 Agent 并立即启动压测任务。"""
    _validate_jvm_heap_mb(req.jvm_heap_mb)

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

    jmeter_args.update(_normalize_jmeter_properties(req.jmeter_properties))

    if req.jvm_heap_mb:
        jmeter_args["jvm_heap_mb"] = str(req.jvm_heap_mb)
    jmeter_args["capture_error_log"] = "true" if req.capture_error_log else "false"
    jmeter_args["error_log_sample_limit"] = str(max(0, min(10000, req.error_log_sample_limit)))
    jmeter_args["error_log_max_body_chars"] = str(max(256, min(262144, req.error_log_max_body_chars)))
    jmeter_args["result_format"] = "xml" if req.result_format == "xml" or req.debug_result_xml else "csv"
    if req.debug_result_xml:
        jmeter_args["debug_result_xml"] = "true"

    timeout = max(
        _to_int(req.timeout, _estimate_timeout_seconds(req.duration, req.ramp_time, req.scenario, req.distributed)),
        _estimate_timeout_seconds(req.duration, req.ramp_time, req.scenario, req.distributed),
    )

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
            enforce_single_agent_task=req.enforce_single_agent_task,
        )
        if not _scheduler.start_task(task_id):
            task = _scheduler.get_task(task_id)
            detail = (task or {}).get("error_message") or "无法启动任务"
            raise HTTPException(status_code=400, detail=detail)
        return {"task_id": task_id, "message": "任务已创建并启动"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
