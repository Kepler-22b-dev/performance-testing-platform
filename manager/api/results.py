"""测试结果分析与报告 API 模块。

提供压测结果的查询、汇总统计、时序分析、响应时间分布、标签统计、
错误分析、多任务对比、HTML/PDF 报告导出以及性能趋势追踪等接口。
"""

import sys
import os
import json
import re
import time
import shutil
from fastapi import APIRouter, HTTPException
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REPORTS_DIR, SCRIPTS_DIR
from common.utils import fmt_pct, percentile

router = APIRouter(prefix="/api/results", tags=["results"])

MAX_SAMPLE_PAGE_SIZE = 500
DEFAULT_RESULT_TASK_LIMIT = 100
REPORT_RETENTION_DAYS = int(os.getenv("REPORT_RETENTION_DAYS", "30"))
REPORT_RETENTION_MAX_TASKS = int(os.getenv("REPORT_RETENTION_MAX_TASKS", "200"))
REPORT_RETENTION_MIN_FREE_GB = float(os.getenv("REPORT_RETENTION_MIN_FREE_GB", "2"))


def _format_task_id(task_id: str) -> str:
    task_id = str(task_id or "")
    match = re.match(r"^task-(\d{8})-(\d{3,})$", task_id)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    if re.match(r"^(?:task-)?[0-9a-fA-F]{6,12}$", task_id):
        return "历史任务"
    return task_id or "-"


def _task_date_from_timestamp(timestamp: float) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y%m%d")
    except Exception:
        return "历史任务"


def _build_task_display_ids(tasks: list[dict]) -> dict:
    display_ids = {}
    next_seq_by_date = {}
    dated_pattern = re.compile(r"^task-(\d{8})-(\d{3,})$")

    for task in tasks:
        task_id = str(task.get("task_id", ""))
        match = dated_pattern.match(task_id)
        if not match:
            continue
        date, seq_text = match.groups()
        seq = int(seq_text)
        display_ids[task_id] = f"{date}-{seq_text}"
        next_seq_by_date[date] = max(next_seq_by_date.get(date, 0), seq)

    legacy_tasks = [
        task for task in tasks
        if task.get("task_id") and not dated_pattern.match(str(task.get("task_id")))
    ]
    legacy_tasks.sort(key=lambda t: (
        float(t.get("created_at") or t.get("modified_at") or 0),
        str(t.get("task_id", "")),
    ))

    for task in legacy_tasks:
        task_id = str(task.get("task_id"))
        date = _task_date_from_timestamp(float(task.get("created_at") or task.get("modified_at") or 0))
        seq = next_seq_by_date.get(date, 0) + 1
        next_seq_by_date[date] = seq
        display_ids[task_id] = f"{date}-{seq:03d}" if date != "历史任务" else f"历史任务-{seq:03d}"

    return display_ids


def _collect_report_tasks() -> list[dict]:
    tasks = []
    if not os.path.exists(REPORTS_DIR):
        return tasks

    for name in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, name)
        if not os.path.isdir(path):
            continue
        stat = os.stat(path)
        tasks.append({
            "task_id": name,
            "created_at": stat.st_ctime,
            "modified_at": stat.st_mtime,
        })
    return tasks


def _get_report_task_path(task_id: str) -> str | None:
    base = os.path.abspath(REPORTS_DIR)
    path = os.path.abspath(os.path.join(base, str(task_id or "")))
    if not path.startswith(base + os.sep):
        return None
    return path


def _resolve_result_task_id(task_id: str) -> str:
    """兼容展示编号和真实报告目录编号。

    页面展示使用 20260704-001 这类编号，但磁盘目录可能是 task-20260704-001
    或历史随机 ID。结果查询接口统一先解析为真实目录，避免详情页局部接口 404。
    """
    requested = str(task_id or "").strip()
    if not requested:
        return requested

    candidates = [requested]
    if re.match(r"^\d{8}-\d{3,}$", requested):
        candidates.append(f"task-{requested}")

    for candidate in candidates:
        path = _get_report_task_path(candidate)
        if path and os.path.isdir(path):
            return candidate

    display_ids = _build_task_display_ids(_collect_report_tasks())
    for real_task_id, display_task_id in display_ids.items():
        if display_task_id == requested:
            return real_task_id

    return requested


def _get_display_task_id(task_id: str) -> str:
    if not os.path.exists(REPORTS_DIR):
        return _format_task_id(task_id)
    tasks = _collect_report_tasks()
    return _build_task_display_ids(tasks).get(task_id, _format_task_id(task_id))


def _get_task_path_or_404(task_id: str) -> tuple[str, str]:
    resolved_task_id = _resolve_result_task_id(task_id)
    task_path = _get_report_task_path(resolved_task_id)
    if not task_path or not os.path.exists(task_path):
        raise HTTPException(status_code=404, detail="Task not found")
    return resolved_task_id, task_path


def _get_samples_or_404(task_id: str, detail: str = "Task result not found") -> tuple[str, list]:
    from manager.core.sample_cache import get_cached_samples

    resolved_task_id = _resolve_result_task_id(task_id)
    all_samples = get_cached_samples(resolved_task_id)
    if not all_samples:
        raise HTTPException(status_code=404, detail=detail)
    return resolved_task_id, all_samples


def _format_bytes(size: int | float) -> str:
    value = float(size or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(round(value))} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def _build_summary_from_samples(samples: list) -> dict:
    """从样本列表构建统计摘要。"""
    if not samples:
        return {}
    elapsed_times = sorted([s["elapsed"] for s in samples])
    error_count = sum(1 for s in samples if not s["success"])
    total = len(samples)
    timestamps = [s["timestamp"] for s in samples]
    duration = (max(timestamps) - min(timestamps)) / 1000 if len(timestamps) > 1 else 1
    tps = round(total / duration, 2) if duration > 0 else 0
    qps_metrics = _build_qps_metrics(samples)
    traffic_metrics = _build_traffic_metrics(samples)
    return {
        "total_samples": total,
        "error_count": error_count,
        "success_count": total - error_count,
        "error_rate": fmt_pct(error_count / total * 100) if total > 0 else 0,
        "success_rate": fmt_pct((total - error_count) / total * 100) if total > 0 else 100.0,
        "avg_response_time": round(sum(elapsed_times) / len(elapsed_times), 2),
        "min_response_time": min(elapsed_times),
        "max_response_time": max(elapsed_times),
        "p50": percentile(elapsed_times, 50),
        "p90": percentile(elapsed_times, 90),
        "p95": percentile(elapsed_times, 95),
        "p99": percentile(elapsed_times, 99),
        "tps": tps,
        "qps": tps,
        "stable_qps": qps_metrics["stable_qps"],
        "max_qps": qps_metrics["max_qps"],
        "stable_window": qps_metrics["stable_window"],
        "total_bytes_received": traffic_metrics["total_bytes_received"],
        "total_bytes_sent": traffic_metrics["total_bytes_sent"],
        "total_network_bytes": traffic_metrics["total_network_bytes"],
        "peak_network_bytes_per_sec": traffic_metrics["peak_network_bytes_per_sec"],
        "peak_bytes_received_per_sec": traffic_metrics["peak_bytes_received_per_sec"],
        "peak_bytes_sent_per_sec": traffic_metrics["peak_bytes_sent_per_sec"],
        "avg_bytes_per_request": round(traffic_metrics["total_network_bytes"] / total) if total > 0 else 0,
        "duration": round(duration, 1),
    }


def _build_qps_metrics(samples: list) -> dict:
    """按 1 秒粒度计算 QPS 峰值和稳定期平均值。"""
    if not samples:
        return {"stable_qps": 0, "max_qps": 0, "stable_window": "all"}

    per_second = {}
    for s in samples:
        bucket_key = (s["timestamp"] // 1000) * 1000
        per_second[bucket_key] = per_second.get(bucket_key, 0) + 1

    values = [per_second[k] for k in sorted(per_second.keys())]
    if not values:
        return {"stable_qps": 0, "max_qps": 0, "stable_window": "all"}

    trim = int(len(values) * 0.1)
    if len(values) >= 10 and trim > 0 and len(values) - trim * 2 >= 1:
        stable_values = values[trim:-trim]
        stable_window = "middle_80_percent"
    else:
        stable_values = values
        stable_window = "all"

    stable_qps = round(sum(stable_values) / len(stable_values), 2) if stable_values else 0
    return {
        "stable_qps": stable_qps,
        "max_qps": max(values),
        "stable_window": stable_window,
    }


def _build_traffic_metrics(samples: list) -> dict:
    """按样本字节数计算总流量和每秒峰值流量。"""
    total_received = 0
    total_sent = 0
    per_second = {}

    for s in samples:
        received = int(s.get("bytes") or 0)
        sent = int(s.get("sent_bytes") or 0)
        total_received += received
        total_sent += sent
        bucket_key = (s["timestamp"] // 1000) * 1000
        if bucket_key not in per_second:
            per_second[bucket_key] = {"received": 0, "sent": 0, "total": 0}
        per_second[bucket_key]["received"] += received
        per_second[bucket_key]["sent"] += sent
        per_second[bucket_key]["total"] += received + sent

    peak_total = max((v["total"] for v in per_second.values()), default=0)
    peak_received = max((v["received"] for v in per_second.values()), default=0)
    peak_sent = max((v["sent"] for v in per_second.values()), default=0)

    return {
        "total_bytes_received": total_received,
        "total_bytes_sent": total_sent,
        "total_network_bytes": total_received + total_sent,
        "peak_network_bytes_per_sec": peak_total,
        "peak_bytes_received_per_sec": peak_received,
        "peak_bytes_sent_per_sec": peak_sent,
    }


def _build_time_series(samples: list) -> dict:
    """将样本数据按秒聚合，构建 TPS、平均响应时间和错误率的时序数据。"""
    if not samples:
        return {
            "timestamps": [],
            "tps": [],
            "avg_rt": [],
            "error_rate": [],
            "active_threads": [],
            "bytes_received": [],
            "bytes_sent": [],
            "network_bytes": [],
        }

    bucket_size = 1000
    buckets = {}

    for s in samples:
        bucket_key = (s["timestamp"] // bucket_size) * bucket_size
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "count": 0,
                "errors": 0,
                "total_elapsed": 0,
                "elapsed_times": [],
                "bytes_received": 0,
                "bytes_sent": 0,
                "network_bytes": 0,
            }
        buckets[bucket_key]["count"] += 1
        buckets[bucket_key]["total_elapsed"] += s["elapsed"]
        buckets[bucket_key]["elapsed_times"].append(s["elapsed"])
        received = int(s.get("bytes") or 0)
        sent = int(s.get("sent_bytes") or 0)
        buckets[bucket_key]["bytes_received"] += received
        buckets[bucket_key]["bytes_sent"] += sent
        buckets[bucket_key]["network_bytes"] += received + sent
        if not s["success"]:
            buckets[bucket_key]["errors"] += 1

    sorted_keys = sorted(buckets.keys())
    timestamps = []
    tps = []
    avg_rt = []
    error_rate = []
    active_threads = []
    bytes_received = []
    bytes_sent = []
    network_bytes = []

    for key in sorted_keys:
        b = buckets[key]
        timestamps.append(key)
        tps.append(b["count"])
        avg_rt.append(round(b["total_elapsed"] / b["count"], 2) if b["count"] > 0 else 0)
        error_rate.append(fmt_pct(b["errors"] / b["count"] * 100) if b["count"] > 0 else 0)
        active_threads.append(0)
        bytes_received.append(b["bytes_received"])
        bytes_sent.append(b["bytes_sent"])
        network_bytes.append(b["network_bytes"])

    return {
        "timestamps": timestamps,
        "tps": tps,
        "avg_rt": avg_rt,
        "error_rate": error_rate,
        "active_threads": active_threads,
        "bytes_received": bytes_received,
        "bytes_sent": bytes_sent,
        "network_bytes": network_bytes,
    }


def _build_label_time_series(samples: list, label: str) -> dict:
    """按接口标签返回聚合后的时序数据，避免向前端返回全量原始样本。"""
    filtered = [s for s in samples if s.get("label") == label]
    return _build_time_series(filtered)


def _build_response_time_distribution(samples: list) -> dict:
    """构建响应时间分布统计，按预设时间区间分桶计数。"""
    ranges = [
        (0, 100, "0-100ms"),
        (100, 200, "100-200ms"),
        (200, 500, "200-500ms"),
        (500, 1000, "500-1s"),
        (1000, 2000, "1-2s"),
        (2000, 5000, "2-5s"),
        (5000, float("inf"), ">5s"),
    ]

    distribution = {r[2]: 0 for r in ranges}

    for s in samples:
        for low, high, label in ranges:
            if low <= s["elapsed"] < high:
                distribution[label] += 1
                break

    return {
        "labels": [r[2] for r in ranges],
        "values": [distribution[r[2]] for r in ranges],
    }


def _build_label_stats(samples: list) -> dict:
    """按接口标签聚合统计，计算每个接口的 TPS、响应时间和错误率。"""
    label_data = {}
    for s in samples:
        label = s["label"]
        if label not in label_data:
            label_data[label] = {"count": 0, "errors": 0, "elapsed_times": [], "timestamps": [], "url": s.get("url", "")}
        label_data[label]["count"] += 1
        label_data[label]["elapsed_times"].append(s["elapsed"])
        label_data[label]["timestamps"].append(s["timestamp"])
        if not label_data[label]["url"] and s.get("url"):
            label_data[label]["url"] = s["url"]
        if not s["success"]:
            label_data[label]["errors"] += 1

    result = []
    for label, data in label_data.items():
        times = sorted(data["elapsed_times"])
        ts = data["timestamps"]
        duration = (max(ts) - min(ts)) / 1000 if len(ts) > 1 else 1
        tps = round(data["count"] / duration, 2) if duration > 0 else 0
        result.append({
            "label": label,
            "url": data["url"],
            "count": data["count"],
            "errors": data["errors"],
            "error_rate": fmt_pct(data["errors"] / data["count"] * 100),
            "tps": tps,
            "avg": round(sum(times) / len(times), 2),
            "min": min(times),
            "max": max(times),
            "p50": percentile(times, 50),
            "p90": percentile(times, 90),
            "p99": percentile(times, 99),
        })

    return result


def _clamp_page(offset: int, limit: int, max_limit: int = 500) -> tuple[int, int]:
    offset = max(0, int(offset or 0))
    limit = max(1, min(max_limit, int(limit or max_limit)))
    return offset, limit


@router.get("/tasks")
def list_completed_tasks(offset: int = 0, limit: int = DEFAULT_RESULT_TASK_LIMIT):
    """获取所有已完成测试任务的列表。"""
    offset, limit = _clamp_page(offset, limit, 500)
    tasks = []
    if not os.path.exists(REPORTS_DIR):
        return {"total": 0, "offset": offset, "limit": limit, "tasks": []}

    # 从数据库获取任务元数据
    task_meta = {}
    try:
        from common.database import get_sync_db
        from manager.core.db_sync import db_get_all_tasks
        db = get_sync_db()
        try:
            db_tasks = db_get_all_tasks(db)
            for t in db_tasks:
                task_meta[t["task_id"]] = t
        finally:
            db.close()
    except Exception:
        pass

    for task_dir in os.listdir(REPORTS_DIR):
        task_path = os.path.join(REPORTS_DIR, task_dir)
        if not os.path.isdir(task_path):
            continue

        stat = os.stat(task_path)
        meta = task_meta.get(task_dir, {})
        tasks.append({
            "task_id": task_dir,
            "script_id": meta.get("script_id", ""),
            "created_at": stat.st_ctime,
            "modified_at": stat.st_mtime,
        })

    display_ids = _build_task_display_ids(tasks)
    for task in tasks:
        task["display_task_id"] = display_ids.get(task["task_id"], _format_task_id(task["task_id"]))

    tasks.sort(key=lambda x: x["modified_at"], reverse=True)
    return {
        "total": len(tasks),
        "offset": offset,
        "limit": limit,
        "tasks": tasks[offset:offset + limit],
    }


@router.get("/tasks/{task_id}/summary")
def get_task_summary(task_id: str):
    """获取指定任务的结果摘要，返回聚合数据，不返回全量原始样本。"""
    resolved_task_id, all_samples = _get_samples_or_404(task_id, "No result data found")

    merged_summary = _build_summary_from_samples(all_samples)
    time_series = _build_time_series(all_samples)
    distribution = _build_response_time_distribution(all_samples)
    label_stats = _build_label_stats(all_samples)

    return {
        "task_id": resolved_task_id,
        "display_task_id": _get_display_task_id(resolved_task_id),
        "summary": merged_summary,
        "time_series": time_series,
        "distribution": distribution,
        "label_stats": label_stats,
    }


@router.get("/tasks/{task_id}/timeseries")
def get_task_timeseries(task_id: str):
    """获取指定任务的时序数据（TPS、平均响应时间、错误率）。"""
    _, all_samples = _get_samples_or_404(task_id)
    return _build_time_series(all_samples)


@router.get("/tasks/{task_id}/label-timeseries")
def get_task_label_timeseries(task_id: str, label: str):
    """获取指定接口标签的聚合时序数据。"""
    if not label:
        raise HTTPException(status_code=400, detail="label is required")

    resolved_task_id, all_samples = _get_samples_or_404(task_id)

    series = _build_label_time_series(all_samples, label)
    return {"task_id": resolved_task_id, "label": label, "time_series": series}


@router.get("/tasks/{task_id}/samples")
def get_task_samples(task_id: str, offset: int = 0, limit: int = 50, label: str = None, errors_only: bool = False):
    """分页获取测试样本数据，支持按标签和错误状态过滤。"""
    offset, limit = _clamp_page(offset, limit, MAX_SAMPLE_PAGE_SIZE)
    resolved_task_id, _ = _get_task_path_or_404(task_id)
    _, all_samples = _get_samples_or_404(resolved_task_id)

    filtered = all_samples
    if label:
        filtered = [s for s in filtered if s["label"] == label]
    if errors_only:
        filtered = [s for s in filtered if not s["success"]]

    total = len(filtered)
    page = filtered[offset:offset + limit]

    return {
        "task_id": resolved_task_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "samples": page,
    }


@router.get("/tasks/{task_id}/sample/{index}")
def get_sample_detail(task_id: str, index: int):
    """获取指定样本的详细信息。"""
    resolved_task_id, all_samples = _get_samples_or_404(task_id)

    if index < 1 or index > len(all_samples):
        raise HTTPException(status_code=404, detail="Sample not found")

    return {"task_id": resolved_task_id, "sample": all_samples[index - 1]}


@router.get("/tasks/{task_id}/errors")
def get_task_errors(task_id: str):
    """获取指定任务的错误汇总和错误样本列表。"""
    resolved_task_id, all_samples = _get_samples_or_404(task_id)

    errors = [s for s in all_samples if not s["success"]]

    error_summary = {}
    for e in errors:
        key = f"{e['label']} - {e['response_code']}"
        if key not in error_summary:
            error_summary[key] = {"label": e["label"], "response_code": e["response_code"], "count": 0, "avg_elapsed": 0, "total_elapsed": 0}
        error_summary[key]["count"] += 1
        error_summary[key]["total_elapsed"] += e["elapsed"]

    for v in error_summary.values():
        v["avg_elapsed"] = round(v["total_elapsed"] / v["count"], 1) if v["count"] > 0 else 0
        del v["total_elapsed"]

    return {
        "task_id": resolved_task_id,
        "total_errors": len(errors),
        "error_summary": list(error_summary.values()),
        "samples": errors[:100],
    }


@router.get("/tasks/{task_id}/full-report")
def get_full_report(task_id: str):
    """获取指定任务的完整报告数据，包含汇总、时序、分布、标签统计和 Agent 详情。"""
    resolved_task_id, all_samples = _get_samples_or_404(task_id, "Task not found")

    merged_summary = _build_summary_from_samples(all_samples)
    time_series = _build_time_series(all_samples)
    distribution = _build_response_time_distribution(all_samples)
    label_stats = _build_label_stats(all_samples)
    errors = [s for s in all_samples if not s["success"]]
    labels = list(set(s["label"] for s in all_samples))

    return {
        "task_id": resolved_task_id,
        "summary": merged_summary,
        "time_series": time_series,
        "distribution": distribution,
        "label_stats": label_stats,
        "labels": labels,
        "total_requests": len(all_samples),
        "total_errors": len(errors),
    }


@router.get("/tasks/{task_id}/logs")
def get_task_logs(task_id: str):
    """获取指定任务所有 Agent 的 JMeter 日志。"""
    resolved_task_id, task_path = _get_task_path_or_404(task_id)

    logs = {}
    for agent_dir in os.listdir(task_path):
        log_path = os.path.join(task_path, agent_dir, "jmeter.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, "r") as f:
                    content = f.read()
                logs[agent_dir] = content
            except Exception:
                logs[agent_dir] = "(读取日志失败)"

    if not logs:
        raise HTTPException(status_code=404, detail="No logs found")

    return {"task_id": resolved_task_id, "agents": logs}


@router.get("/tasks/{task_id}/logs/{agent_id}")
def get_agent_log(task_id: str, agent_id: str):
    """获取指定 Agent 的 JMeter 日志内容。"""
    resolved_task_id, task_path = _get_task_path_or_404(task_id)
    log_path = os.path.join(task_path, agent_id, "jmeter.log")
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="Log not found")

    try:
        with open(log_path, "r") as f:
            content = f.read()
        return {"task_id": resolved_task_id, "agent_id": agent_id, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_running_task_ids() -> set[str]:
    running = set()
    try:
        from common.database import get_sync_db
        from manager.core.db_sync import db_get_running_tasks
        db = get_sync_db()
        try:
            for task in db_get_running_tasks(db):
                task_id = task.get("task_id")
                if task_id:
                    running.add(task_id)
        finally:
            db.close()
    except Exception:
        pass
    return running


def _collect_report_dirs() -> list[dict]:
    items = []
    if not os.path.exists(REPORTS_DIR):
        return items

    for task_id in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, task_id)
        if not os.path.isdir(path):
            continue
        try:
            stat = os.stat(path)
            size = 0
            for root, _, files in os.walk(path):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    try:
                        size += os.path.getsize(filepath)
                    except OSError:
                        pass
            items.append({
                "task_id": task_id,
                "path": path,
                "created_at": stat.st_ctime,
                "modified_at": stat.st_mtime,
                "size_bytes": size,
            })
        except OSError:
            continue
    items.sort(key=lambda x: x["modified_at"], reverse=True)
    return items


def _build_retention_candidates(days: int, max_tasks: int, min_free_gb: float) -> tuple[list[dict], dict]:
    now = time.time()
    running_ids = _get_running_task_ids()
    reports = _collect_report_dirs()
    protected = [r for r in reports if r["task_id"] in running_ids]
    candidates_by_id = {}

    if days > 0:
        cutoff = now - days * 86400
        for report in reports:
            if report["task_id"] not in running_ids and report["modified_at"] < cutoff:
                candidates_by_id[report["task_id"]] = {**report, "reason": f"older_than_{days}_days"}

    if max_tasks > 0:
        non_running = [r for r in reports if r["task_id"] not in running_ids]
        for report in non_running[max_tasks:]:
            candidates_by_id.setdefault(
                report["task_id"],
                {**report, "reason": f"exceeds_{max_tasks}_tasks"},
            )

    disk = shutil.disk_usage(REPORTS_DIR if os.path.exists(REPORTS_DIR) else os.path.dirname(REPORTS_DIR))
    free_target = int(max(0, min_free_gb) * 1024 * 1024 * 1024)
    free_after_candidates = disk.free + sum(c["size_bytes"] for c in candidates_by_id.values())
    if free_target > 0 and free_after_candidates < free_target:
        oldest_first = sorted(
            [r for r in reports if r["task_id"] not in running_ids and r["task_id"] not in candidates_by_id],
            key=lambda x: x["modified_at"],
        )
        for report in oldest_first:
            candidates_by_id[report["task_id"]] = {**report, "reason": f"free_space_below_{min_free_gb:g}gb"}
            free_after_candidates += report["size_bytes"]
            if free_after_candidates >= free_target:
                break

    candidates = sorted(candidates_by_id.values(), key=lambda x: x["modified_at"])
    status = {
        "reports_dir": REPORTS_DIR,
        "total_tasks": len(reports),
        "protected_running_tasks": len(protected),
        "total_size_bytes": sum(r["size_bytes"] for r in reports),
        "disk_total_bytes": disk.total,
        "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free,
        "retention_days": days,
        "retention_max_tasks": max_tasks,
        "retention_min_free_gb": min_free_gb,
    }
    return candidates, status


@router.get("/retention/status")
def get_retention_status(days: int = REPORT_RETENTION_DAYS,
                         max_tasks: int = REPORT_RETENTION_MAX_TASKS,
                         min_free_gb: float = REPORT_RETENTION_MIN_FREE_GB):
    """查看结果保留策略命中情况，不删除文件。"""
    candidates, status = _build_retention_candidates(days, max_tasks, min_free_gb)
    status["cleanup_candidates"] = len(candidates)
    status["cleanup_size_bytes"] = sum(c["size_bytes"] for c in candidates)
    return {
        **status,
        "candidates": [
            {
                "task_id": c["task_id"],
                "modified_at": c["modified_at"],
                "size_bytes": c["size_bytes"],
                "reason": c["reason"],
            }
            for c in candidates
        ],
    }


@router.post("/retention/cleanup")
def cleanup_retention(dry_run: bool = True,
                      days: int = REPORT_RETENTION_DAYS,
                      max_tasks: int = REPORT_RETENTION_MAX_TASKS,
                      min_free_gb: float = REPORT_RETENTION_MIN_FREE_GB):
    """按天数、任务数和磁盘空闲水位清理历史报告目录。默认 dry_run=true。"""
    candidates, status = _build_retention_candidates(days, max_tasks, min_free_gb)
    deleted = []
    failed = []

    if not dry_run:
        for candidate in candidates:
            try:
                shutil.rmtree(candidate["path"])
                deleted.append({
                    "task_id": candidate["task_id"],
                    "size_bytes": candidate["size_bytes"],
                    "reason": candidate["reason"],
                })
                try:
                    from manager.core.sample_cache import invalidate_cache
                    invalidate_cache(candidate["task_id"])
                except Exception:
                    pass
            except Exception as exc:
                failed.append({
                    "task_id": candidate["task_id"],
                    "error": str(exc),
                    "reason": candidate["reason"],
                })

    return {
        **status,
        "dry_run": dry_run,
        "candidates": len(candidates),
        "candidate_size_bytes": sum(c["size_bytes"] for c in candidates),
        "deleted": deleted,
        "failed": failed,
    }


@router.get("/compare")
def compare_tasks(task_ids: str):
    """对比多个任务的测试结果，返回各任务的汇总统计和时序数据。"""
    from manager.core.sample_cache import get_cached_samples
    ids = [t.strip() for t in task_ids.split(",") if t.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="需要至少两个任务ID进行对比")

    results = []
    for task_id in ids:
        resolved_task_id = _resolve_result_task_id(task_id)
        all_samples = get_cached_samples(resolved_task_id)
        if not all_samples:
            continue

        merged = _build_summary_from_samples(all_samples)
        time_series = _build_time_series(all_samples)
        label_stats = _build_label_stats(all_samples)

        task_path = _get_report_task_path(resolved_task_id)
        stat = os.stat(task_path) if task_path and os.path.exists(task_path) else None
        results.append({
            "task_id": resolved_task_id,
            "display_task_id": _get_display_task_id(resolved_task_id),
            "created_at": stat.st_ctime if stat else 0,
            "summary": merged,
            "time_series": time_series,
            "label_stats": label_stats,
        })

    if len(results) < 2:
        raise HTTPException(status_code=404, detail="未找到足够的任务数据进行对比")

    return {"tasks": results}


@router.get("/tasks/{task_id}/export")
def export_report(task_id: str):
    """导出指定任务的 HTML 格式测试报告。"""
    resolved_task_id, _ = _get_task_path_or_404(task_id)
    _, all_samples = _get_samples_or_404(resolved_task_id, "No result data found")

    display_task_id = _get_display_task_id(resolved_task_id)
    merged_summary = _build_summary_from_samples(all_samples)
    label_stats = _build_label_stats(all_samples)
    distribution = _build_response_time_distribution(all_samples)

    total_time = 0
    if all_samples:
        total_time = (all_samples[-1]["timestamp"] - all_samples[0]["timestamp"]) / 1000
    tps = round(merged_summary.get("total_samples", 0) / total_time, 2) if total_time > 0 else 0
    stable_qps = merged_summary.get("stable_qps", tps)
    max_qps = merged_summary.get("max_qps", tps)
    stable_qps_desc = "去掉前后10%后的平均值" if merged_summary.get("stable_window") == "middle_80_percent" else "按全程时序平均值估算"
    peak_network = _format_bytes(merged_summary.get("peak_network_bytes_per_sec", 0))
    total_network = _format_bytes(merged_summary.get("total_network_bytes", merged_summary.get("total_bytes_received", 0)))

    time_series = _build_time_series(all_samples)

    import json as _json
    ts_timestamps = time_series.get("timestamps", [])
    ts_labels = [__import__('datetime').datetime.fromtimestamp(t / 1000).strftime('%H:%M:%S') for t in ts_timestamps]
    ts_tps_data = _json.dumps(time_series.get("tps", []))
    ts_rt_data = _json.dumps(time_series.get("avg_rt", []))
    ts_err_data = _json.dumps(time_series.get("error_rate", []))

    dist_labels = _json.dumps(distribution.get("labels", []))
    dist_values = _json.dumps(distribution.get("values", []))

    pct_labels = _json.dumps(["P50", "P90", "P95", "P99"])
    pct_values = _json.dumps([
        merged_summary.get("p50", 0),
        merged_summary.get("p90", 0),
        merged_summary.get("p95", 0),
        merged_summary.get("p99", 0),
    ])

    label_rows = ""
    for l in label_stats:
        err_color = "#dc2626" if l["error_rate"] > 0 else "#16a34a"
        label_rows += f"""<tr>
<td>{l['label']}</td><td>{l['count']}</td><td style="color:#2563eb;font-weight:500">{l.get('tps', 0)}</td>
<td>{l['avg']}ms</td><td>{l['p50']}ms</td><td>{l['p90']}ms</td><td>{l['p99']}ms</td>
<td style="color:{err_color}">{l['error_rate']}%</td>
</tr>"""

    success_rate = merged_summary.get("success_rate", max(0, 100 - merged_summary.get("error_rate", 0)))
    success_color = "#16a34a" if success_rate >= 99 else "#d97706" if success_rate >= 95 else "#dc2626"
    err_rate_color = "#dc2626" if merged_summary.get("error_rate", 0) > 0 else "#16a34a"

    # 缓存 ECharts 脚本
    echarts_path = os.path.join(os.path.dirname(__file__), "..", "static", "echarts.min.js")
    if not os.path.exists(echarts_path):
        import urllib.request
        urllib.request.urlretrieve("https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js", echarts_path)
    with open(echarts_path, "r", encoding="utf-8") as f:
        echarts_js = f.read()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>压测报告 - {display_task_id}</title>
<script>{echarts_js}</script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f5f5f5; color: #1a1a1a; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
.header {{ background: #fff; padding: 20px 24px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #e0e0e0; }}
.header h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
.header .meta {{ font-size: 13px; color: #888; }}
.summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.card {{ background: #fff; border-radius: 8px; padding: 16px; border: 1px solid #e0e0e0; text-align: center; }}
.card .label {{ font-size: 12px; color: #888; margin-bottom: 4px; }}
.card .value {{ font-size: 22px; font-weight: 600; }}
.card .desc {{ margin-top: 4px; font-size: 11px; color: #777; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.card .value.green {{ color: #16a34a; }}
.card .value.red {{ color: #dc2626; }}
.card .value.blue {{ color: #2563eb; }}
.card .value.yellow {{ color: #d97706; }}
.chart-section {{ background: #fff; border-radius: 8px; padding: 20px; border: 1px solid #e0e0e0; margin-bottom: 16px; }}
.chart-section h2 {{ font-size: 15px; font-weight: 600; margin-bottom: 16px; color: #333; }}
.chart-container {{ height: 320px; }}
.charts-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; font-size: 13px; }}
th {{ color: #888; font-weight: 500; background: #fafafa; }}
tr:hover {{ background: #fafafa; }}
.footer {{ margin-top: 40px; padding: 20px; text-align: center; font-size: 12px; color: #999; border-top: 1px solid #e0e0e0; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>压测报告</h1>
        <div class="meta">任务编号: {display_task_id} | 总耗时: {round(total_time, 1)}s | 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
    </div>

    <div class="summary-cards">
        <div class="card"><div class="label">稳定期 QPS</div><div class="value blue">{stable_qps}</div><div class="desc">{stable_qps_desc}</div></div>
        <div class="card"><div class="label">最大 QPS</div><div class="value blue">{max_qps}</div><div class="desc">1 秒粒度峰值</div></div>
        <div class="card"><div class="label">总请求数</div><div class="value blue">{merged_summary.get('total_samples', 0)}</div></div>
        <div class="card"><div class="label">成功率</div><div class="value" style="color:{success_color}">{success_rate}%</div></div>
        <div class="card"><div class="label">平均响应时间</div><div class="value green">{merged_summary.get('avg_response_time', 0)}ms</div></div>
        <div class="card"><div class="label">P99</div><div class="value red">{merged_summary.get('p99', 0)}ms</div></div>
        <div class="card"><div class="label">错误率</div><div class="value" style="color:{err_rate_color}">{merged_summary.get('error_rate', 0)}%</div></div>
        <div class="card"><div class="label">网络流量</div><div class="value blue" style="font-size:16px">{peak_network}/s</div><div class="desc">总量 {total_network}</div></div>
        <div class="card"><div class="label">平均延迟</div><div class="value">{merged_summary.get('avg_latency', merged_summary.get('avg_response_time', 0))}ms</div></div>
    </div>

    <div class="chart-section">
        <h2>TPS / 响应时间 / 错误率 时序图</h2>
        <div class="chart-container" id="chart-ts"></div>
    </div>

    <div class="charts-grid">
        <div class="chart-section">
            <h2>响应时间分布</h2>
            <div class="chart-container" id="chart-dist"></div>
        </div>
        <div class="chart-section">
            <h2>百分位响应时间</h2>
            <div class="chart-container" id="chart-pct"></div>
        </div>
    </div>

    <div class="chart-section">
        <h2>接口统计</h2>
        <table>
        <thead><tr><th>接口名称</th><th>请求数</th><th>TPS</th><th>平均耗时</th><th>P50</th><th>P90</th><th>P99</th><th>错误率</th></tr></thead>
        <tbody>{label_rows}</tbody>
        </table>
    </div>

    <div class="footer">
        <p>由性能测试平台自动生成</p>
    </div>
</div>

<script>
(function() {{
    const timestamps = {ts_labels.__repr__().replace("'", '"')};
    const tpsData = {ts_tps_data};
    const rtData = {ts_rt_data};
    const errData = {ts_err_data};

    if (timestamps.length > 0) {{
        const chartTs = echarts.init(document.getElementById('chart-ts'));
        chartTs.setOption({{
            tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }}, formatter: function(params) {{
                let html = '<div style="font-size:12px;margin-bottom:4px;color:#888">' + params[0].axisValue + '</div>';
                params.forEach(function(p) {{
                    var unit = p.seriesName.includes('RT') ? 'ms' : p.seriesName.includes('错误') ? '%' : '/s';
                    html += '<div style="font-size:12px">' + p.marker + ' ' + p.seriesName + ': <strong>' + p.value + '</strong>' + unit + '</div>';
                }});
                return html;
            }} }},
            legend: {{ data: ['TPS', '平均RT(ms)', '错误率(%)'], top: 0 }},
            xAxis: {{ type: 'category', data: timestamps, boundaryGap: false }},
            yAxis: [
                {{ type: 'value', name: 'TPS' }},
                {{ type: 'value', name: 'ms / %' }}
            ],
            series: [
                {{ name: 'TPS', type: 'line', data: tpsData, smooth: true, lineStyle: {{ width: 2.5, color: '#2563eb' }}, itemStyle: {{ color: '#2563eb' }}, symbol: 'circle', symbolSize: 4, areaStyle: {{ color: 'rgba(37,99,235,0.08)' }}, yAxisIndex: 0 }},
                {{ name: '平均RT(ms)', type: 'line', data: rtData, smooth: true, lineStyle: {{ width: 2.5, color: '#16a34a' }}, itemStyle: {{ color: '#16a34a' }}, symbol: 'circle', symbolSize: 4, areaStyle: {{ color: 'rgba(22,163,74,0.06)' }}, yAxisIndex: 1 }},
                {{ name: '错误率(%)', type: 'line', data: errData, smooth: true, lineStyle: {{ width: 2, color: '#dc2626' }}, itemStyle: {{ color: '#dc2626' }}, symbol: 'circle', symbolSize: 4, yAxisIndex: 1 }}
            ],
            grid: {{ left: 60, right: 60, top: 40, bottom: 30 }}
        }});
        window.addEventListener('resize', () => chartTs.resize());
    }}

    const distLabels = {dist_labels};
    const distValues = {dist_values};
    if (distLabels.length > 0) {{
        const chartDist = echarts.init(document.getElementById('chart-dist'));
        chartDist.setOption({{
            tooltip: {{ trigger: 'axis', formatter: function(p) {{ return p[0].name + '<br/>' + p[0].marker + ' 请求数: <b>' + p[0].value + '</b>'; }} }},
            xAxis: {{ type: 'category', data: distLabels, boundaryGap: false }},
            yAxis: {{ type: 'value' }},
            series: [{{ data: distValues, type: 'line', smooth: true, lineStyle: {{ width: 2.5, color: '#2563eb' }}, itemStyle: {{ color: '#2563eb' }}, symbol: 'circle', symbolSize: 6, areaStyle: {{ color: 'rgba(37,99,235,0.1)' }}, label: {{ show: true, position: 'top', color: '#666', fontSize: 11 }} }}],
            grid: {{ left: 50, right: 20, top: 20, bottom: 30 }}
        }});
        window.addEventListener('resize', () => chartDist.resize());
    }}

    const pctLabels = {pct_labels};
    const pctValues = {pct_values};
    if (pctValues.some(v => v > 0)) {{
        const chartPct = echarts.init(document.getElementById('chart-pct'));
        chartPct.setOption({{
            tooltip: {{ trigger: 'axis', formatter: function(p) {{ return p[0].name + '<br/>' + p[0].marker + ' 响应时间: <b>' + p[0].value + 'ms</b>'; }} }},
            xAxis: {{ type: 'category', data: pctLabels, boundaryGap: false }},
            yAxis: {{ type: 'value', name: 'ms' }},
            series: [{{ data: pctValues, type: 'line', smooth: true, lineStyle: {{ width: 2.5, color: '#d97706' }}, itemStyle: {{ color: '#d97706' }}, symbol: 'circle', symbolSize: 8, areaStyle: {{ color: 'rgba(217,119,6,0.1)' }}, label: {{ show: true, position: 'top', color: '#333', formatter: '{{c}}ms' }} }}],
            grid: {{ left: 50, right: 20, top: 20, bottom: 30 }}
        }});
        window.addEventListener('resize', () => chartPct.resize());
    }}
}})();
// 确保 ECharts 渲染完成
setTimeout(() => {{ window.__echartsReady = true; }}, 1000);
</script>
</body>
</html>"""

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@router.get("/tasks/{task_id}/export-pdf")
def export_report_pdf(task_id: str):
    """导出指定任务的 PDF 格式测试报告（使用 Chrome headless 渲染）。"""
    resolved_task_id, task_path = _get_task_path_or_404(task_id)

    html_resp = export_report(resolved_task_id)
    html_content = html_resp.body.decode("utf-8")

    pdf_path = os.path.join(task_path, f"{resolved_task_id}_report.pdf")
    html_path = os.path.join(task_path, f"{resolved_task_id}_report.html")

    # 先保存 HTML，再用 Chrome 渲染 PDF
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome_path):
        chrome_path = "chromium"

    import subprocess
    import tempfile

    screenshot_path = pdf_path.replace('.pdf', '.png')

    try:
        subprocess.run([
            chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--screenshot={screenshot_path}",
            "--window-size=1200,2000",
            "--virtual-time-budget=15000",
            f"file://{os.path.abspath(html_path)}",
        ], timeout=30, capture_output=True, check=True)

        # 将截图转为 PDF
        from PIL import Image
        img = Image.open(screenshot_path)
        img.save(pdf_path, "PDF", resolution=150)
        os.remove(screenshot_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 生成失败: {str(e)}")

    from fastapi.responses import FileResponse
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"report_{_get_display_task_id(resolved_task_id)}.pdf",
    )


_trend_cache = {"data": None, "time": 0}
_TREND_CACHE_TTL = 60  # 缓存 60 秒

@router.get("/trend")
def get_performance_trend(label: str = None, limit: int = 20):
    """获取性能趋势数据，展示多个任务的关键指标变化趋势。"""
    import time
    now = time.time()

    # 检查缓存（无 label 且 limit=30 时使用缓存）
    cache_key = f"{label}_{limit}"
    if not label and limit == 30 and _trend_cache["data"] and now - _trend_cache["time"] < _TREND_CACHE_TTL:
        cached = _trend_cache["data"]
        return {"tasks": cached.get("tasks", []), "labels": cached.get("labels", [])}

    from manager.core.sample_cache import get_cached_samples
    if not os.path.exists(REPORTS_DIR):
        return {"tasks": [], "labels": []}

    # 获取所有任务目录并按修改时间排序
    task_dirs = []
    for task_dir in os.listdir(REPORTS_DIR):
        task_path = os.path.join(REPORTS_DIR, task_dir)
        if not os.path.isdir(task_path):
            continue
        stat = os.stat(task_path)
        task_dirs.append((task_dir, task_path, stat.st_mtime))

    # 只处理最近的任务（限制数量避免慢查询）
    task_dirs.sort(key=lambda x: x[2], reverse=True)
    task_dirs = task_dirs[:limit]

    # 获取脚本名称映射
    script_map = {}
    try:
        from common.database import get_sync_db
        from manager.core.db_sync import db_get_all_tasks
        db = get_sync_db()
        try:
            db_tasks = db_get_all_tasks(db)
            for t in db_tasks:
                script_map[t["task_id"]] = t.get("script_id", "")
        finally:
            db.close()
    except Exception:
        pass

    # 获取脚本ID到名称的映射
    script_name_map = {}
    try:
        from manager.core.db_sync import db_get_all_scripts
        db = get_sync_db()
        try:
            scripts = db_get_all_scripts(db)
            for s in scripts:
                script_name_map[str(s["script_id"])] = s.get("original_name") or s.get("filename") or str(s["script_id"])
        finally:
            db.close()
    except Exception:
        pass

    task_data = []
    label_stats = {}
    label_set = set()

    for task_dir, task_path, mtime in reversed(task_dirs):
        try:
            all_samples = get_cached_samples(task_dir)
            if not all_samples:
                continue

            # 收集所有 label
            for s in all_samples:
                lbl = s.get("label", "")
                if lbl:
                    label_set.add(lbl)

            # 如果指定了 label，过滤
            filtered = all_samples
            if label:
                filtered = [s for s in all_samples if s.get("label") == label]

            if not filtered:
                continue

            # 按 label 分组统计
            by_label = {}
            for s in filtered:
                lbl = s.get("label", "unknown")
                if lbl not in by_label:
                    by_label[lbl] = []
                by_label[lbl].append(s)

            for lbl, samples in by_label.items():
                if lbl not in label_stats:
                    label_stats[lbl] = {"samples": [], "timestamps": [], "errors": 0, "total": 0}
                label_stats[lbl]["samples"].extend([s["elapsed"] for s in samples])
                label_stats[lbl]["timestamps"].extend([s["timestamp"] for s in samples])
                label_stats[lbl]["errors"] += sum(1 for s in samples if not s["success"])
                label_stats[lbl]["total"] += len(samples)

        except Exception:
            continue

    # 按接口维度汇总
    for lbl, stats in label_stats.items():
        if not stats["samples"]:
            continue
        elapsed = sorted(stats["samples"])
        total = stats["total"]
        errors = stats["errors"]
        # 计算 TPS
        ts_list = stats.get("timestamps", [])
        if len(ts_list) > 1:
            duration = (max(ts_list) - min(ts_list)) / 1000
            tps = round(total / duration, 2) if duration > 0 else 0
        else:
            tps = 0
        label_stats_entry = {
            "label": lbl,
            "total_samples": total,
            "error_count": errors,
            "error_rate": fmt_pct(errors / total * 100) if total > 0 else 0,
            "avg_response_time": round(sum(elapsed) / len(elapsed), 2),
            "p50": percentile(elapsed, 50),
            "p90": percentile(elapsed, 90),
            "p99": percentile(elapsed, 99),
            "tps": tps,
        }
        task_data.append(label_stats_entry)

    task_data.sort(key=lambda x: x["total_samples"], reverse=True)

    result = {
        "tasks": task_data,
        "labels": sorted(label_set - {""}),
    }

    # 更新缓存
    if not label and limit == 30:
        _trend_cache["data"] = result
        _trend_cache["time"] = now

    return result
