"""任务管理 API 模块。

提供压测任务的创建、启动、停止、删除、重新执行、批量创建和快速运行等接口，
通过 Scheduler 实现任务的全生命周期管理。
"""

import sys
import os
import json
import csv
import time
from dataclasses import asdict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.protocol import TaskStatus
from common.config import REPORTS_DIR
from common.utils import fmt_pct

# 前向引用 TaskScheduler，避免循环导入
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from manager.core.scheduler import TaskScheduler

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_scheduler: "Optional[TaskScheduler]" = None
# JTL 进度缓存：用于 Manager 重启后从磁盘 JTL 文件回填实时进度曲线
_jtl_progress_cache: dict = {}
_jtl_progress_cache_ttl: float = 1.0  # 缓存有效期(秒)，1秒内不重复解析同一文件
_JTL_CACHE_MAX: int = 200           # 缓存最大条目数，防止内存无限增长


def set_scheduler(scheduler: "TaskScheduler") -> None:
    """注入 Scheduler 实例供 API 路由使用。

    Args:
        scheduler: Scheduler 实例，提供任务管理的核心方法。
    """
    global _scheduler
    _scheduler = scheduler


def _get_scheduler() -> "TaskScheduler":
    """获取 Scheduler 实例，确保已注入。"""
    assert _scheduler is not None, "Scheduler 未初始化，请先调用 set_scheduler()"
    return _scheduler


def _evict_jtl_cache() -> None:
    """JTL 缓存 LRU 淘汰：当缓存超过上限时，移除最旧的条目。

    防止长时间运行的系统中缓存条目无限增长导致内存泄漏。
    淘汰策略：优先移除已过期的条目，其次移除访问时间最早的条目。
    """
    if len(_jtl_progress_cache) <= _JTL_CACHE_MAX:
        return
    now = time.time()
    entries = sorted(
        _jtl_progress_cache.items(),
        key=lambda item: item[1]["time"] if now - item[1]["time"] < _jtl_progress_cache_ttl else 0,
    )
    to_remove = len(_jtl_progress_cache) - _JTL_CACHE_MAX
    for key, _ in entries[:to_remove]:
        _jtl_progress_cache.pop(key, None)


def _row_value(row: dict, key: str, default: str = "") -> str:
    value = row.get(key)
    if value is not None:
        return value
    lower_key = key.lower()
    for row_key, row_value in row.items():
        if str(row_key).lower() == lower_key:
            return row_value if row_value is not None else default
    return default


def _safe_int(value, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _iter_task_jtl_files(task_id: str):
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.isdir(task_path):
        return

    for root, dirs, files in os.walk(task_path):
        dirs[:] = [d for d in dirs if d not in {"html-report", "__pycache__"}]
        for filename in files:
            if filename.lower() == "result.jtl":
                yield os.path.join(root, filename)


def _recover_progress_history_from_jtl(task_id: str, task: dict | None = None) -> list[dict]:
    """从 JTL 回填实时曲线，兜底处理 Manager 重启或 Redis 进度丢失。"""
    now = time.time()
    cached = _jtl_progress_cache.get(task_id)
    if cached and now - cached["time"] < _jtl_progress_cache_ttl:
        return cached["history"]

    jtl_files = list(_iter_task_jtl_files(task_id) or [])
    if not jtl_files:
        _jtl_progress_cache[task_id] = {"time": now, "history": []}
        return []

    start_ms = 0
    if task and task.get("start_time"):
        start_ms = int(float(task["start_time"]) * 1000)

    buckets: dict[int, dict] = {}
    earliest_ts = None

    for source_index, jtl_path in enumerate(jtl_files):
        try:
            with open(jtl_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    continue
                for row in reader:
                    ts = _safe_int(_row_value(row, "timeStamp"), 0)
                    if ts <= 0:
                        continue
                    if earliest_ts is None or ts < earliest_ts:
                        earliest_ts = ts

                    origin_ms = start_ms or earliest_ts or ts
                    elapsed = max(0, int((ts - origin_ms) / 1000))
                    bucket = buckets.setdefault(elapsed, {
                        "timestamp": ts,
                        "count": 0,
                        "errors": 0,
                        "elapsed_sum": 0,
                        "latency_sum": 0,
                        "connect_sum": 0,
                        "bytes_received": 0,
                        "bytes_sent": 0,
                        "threads_by_source": {},
                    })
                    bucket["timestamp"] = max(bucket["timestamp"], ts)
                    bucket["count"] += 1
                    success = str(_row_value(row, "success", "true")).strip().lower() == "true"
                    if not success:
                        bucket["errors"] += 1
                    bucket["elapsed_sum"] += _safe_int(_row_value(row, "elapsed"), 0)
                    bucket["latency_sum"] += _safe_int(_row_value(row, "Latency"), 0)
                    bucket["connect_sum"] += _safe_int(_row_value(row, "Connect"), 0)
                    bucket["bytes_received"] += _safe_int(_row_value(row, "bytes"), 0)
                    bucket["bytes_sent"] += _safe_int(_row_value(row, "sentBytes"), 0)
                    active_threads = max(
                        _safe_int(_row_value(row, "allThreads"), 0),
                        _safe_int(_row_value(row, "grpThreads"), 0),
                    )
                    if active_threads > 0:
                        source_threads = bucket["threads_by_source"]
                        source_threads[source_index] = max(source_threads.get(source_index, 0), active_threads)
        except Exception:
            continue

    if not buckets:
        _evict_jtl_cache()
        _jtl_progress_cache[task_id] = {"time": now, "history": []}
        return []

    history = []
    total = 0
    errors = 0
    elapsed_sum = 0
    latency_sum = 0
    connect_sum = 0
    bytes_received = 0
    bytes_sent = 0

    for elapsed in sorted(buckets.keys()):
        bucket = buckets[elapsed]
        count = bucket["count"]
        total += count
        errors += bucket["errors"]
        elapsed_sum += bucket["elapsed_sum"]
        latency_sum += bucket["latency_sum"]
        connect_sum += bucket["connect_sum"]
        bytes_received += bucket["bytes_received"]
        bytes_sent += bucket["bytes_sent"]

        history.append({
            "timestamp": bucket["timestamp"] / 1000,
            "elapsed": elapsed,
            "throughput": round(count, 2),
            "avg_response_time": round(elapsed_sum / total, 2) if total else 0,
            "error_rate": fmt_pct(errors / total * 100) if total else 0,
            "success_rate": fmt_pct((total - errors) / total * 100) if total else 100.0,
            "active_threads": sum(bucket["threads_by_source"].values()),
            "total_samples": total,
            "bytes_received": bytes_received,
            "bytes_sent": bytes_sent,
            "avg_latency": round(latency_sum / total, 2) if total else 0,
            "avg_connect_time": round(connect_sum / total, 2) if total else 0,
            "segment_id": "jtl-recovered",
        })

    history = history[-3600:]
    _evict_jtl_cache()
    _jtl_progress_cache[task_id] = {"time": now, "history": history}
    return history


def _recover_progress_from_jtl(task_id: str, task: dict | None = None) -> dict | None:
    history = _recover_progress_history_from_jtl(task_id, task)
    if not history:
        return None
    progress = dict(history[-1])
    progress["task_id"] = task_id
    progress["agent_id"] = "jtl-recovered"
    if task and task.get("status") != TaskStatus.RUNNING:
        progress["active_threads"] = 0
        progress["throughput"] = 0
    return progress


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

        task_id = _get_scheduler().create_task(
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
    success = _get_scheduler().start_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法启动任务")
    return {"message": "Task started"}


@router.post("/{task_id}/stop")
def stop_task(task_id: str):
    """停止正在运行的压测任务。"""
    success = _get_scheduler().stop_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法停止任务")
    return {"message": "Task stopped"}


@router.get("/")
def list_tasks(offset: int = 0, limit: int = 100, status: Optional[str] = None):
    """获取所有压测任务的列表。"""
    offset = max(0, int(offset or 0))
    limit = max(1, min(500, int(limit or 100)))
    result = _get_scheduler().get_all_tasks(offset=offset, limit=limit, status=status)
    if isinstance(result, tuple):
        total, tasks = result
    else:
        tasks = result
        total = len(tasks)
    return {"total": total, "offset": offset, "limit": limit, "tasks": tasks}


@router.get("/{task_id}")
def get_task(task_id: str):
    """获取指定任务的详细信息。"""
    task = _get_scheduler().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/progress")
def get_progress(task_id: str):
    """获取指定任务的执行进度。"""
    task = _get_scheduler().get_task(task_id)
    progress = _get_scheduler().get_progress(task_id)
    if progress:
        return {"task_id": task_id, "data": asdict(progress)}

    recovered = _recover_progress_from_jtl(task_id, task)
    if not recovered:
        return {"task_id": task_id, "data": None}
    return {"task_id": task_id, "data": recovered}


@router.get("/{task_id}/progress/history")
def get_progress_history(task_id: str):
    """获取指定任务的进度历史数据。"""
    history = _get_scheduler().get_progress_history(task_id)
    if not history:
        history = _recover_progress_history_from_jtl(task_id, _get_scheduler().get_task(task_id))
    return {"task_id": task_id, "data": history}


@router.post("/batch")
def batch_create_tasks(req: BatchTaskRequest):
    """批量创建多个压测任务。"""
    tasks_config = []
    for item in req.tasks:
        cfg = item.model_dump()
        cfg["timeout"] = _resolve_timeout_seconds(cfg.get("timeout"), cfg.get("jmeter_args", {}), cfg.get("distributed", False))
        tasks_config.append(cfg)
    results = _get_scheduler().batch_create_tasks(tasks_config)
    success_ids = [r["task_id"] for r in results if r["task_id"]]
    errors = [{"index": i, "error": r["error"]} for i, r in enumerate(results) if r["error"]]
    return {"results": results, "total": len(results), "success_count": len(success_ids), "error_count": len(errors)}


@router.delete("/{task_id}")
def delete_task(task_id: str):
    """删除指定的压测任务（仅限非运行中状态）。"""
    success = _get_scheduler().delete_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法删除任务（可能正在运行中）")
    return {"message": "Task deleted"}


@router.post("/{task_id}/rerun")
def rerun_task(task_id: str):
    """重新执行指定的压测任务，生成新任务。"""
    new_id = _get_scheduler().rerun_task(task_id)
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
    new_id = _get_scheduler().stop_and_rerun(task_id, overrides if overrides else None)
    if not new_id:
        raise HTTPException(status_code=400, detail="无法停止并重启任务")
    return {"task_id": new_id, "message": "已停止旧任务并启动新任务"}


@router.post("/{task_id}/adjust-load")
def adjust_load(task_id: str, req: AdjustLoadRequest):
    """动态调整运行中任务的压力。"""
    try:
        result = _get_scheduler().adjust_load(
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
        agent_id = _get_scheduler().get_available_agent()
        if not agent_id:
            summary = _get_scheduler().get_agent_status_summary()
            if summary.get("available", 0) == 0 and summary.get("busy", 0) > 0:
                raise HTTPException(status_code=400, detail="所有 Agent 节点正忙，请等待任务完成")
            elif summary.get("available", 0) == 0 and summary.get("offline", 0) > 0:
                raise HTTPException(status_code=400, detail="Agent 节点不在线，请检查 Agent 是否启动")
            else:
                raise HTTPException(status_code=400, detail="没有注册的 Agent 节点，请先启动 Agent")
        target = [agent_id]

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
        task_id = _get_scheduler().create_task(
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
        if not _get_scheduler().start_task(task_id):
            task = _get_scheduler().get_task(task_id)
            detail = (task or {}).get("error_message") or "无法启动任务"
            raise HTTPException(status_code=400, detail=detail)
        return {"task_id": task_id, "message": "任务已创建并启动"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
