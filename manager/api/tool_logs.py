"""平台工具日志查看 API。"""

import os
import re
import time
from collections import Counter

from fastapi import APIRouter, HTTPException, Query

from common.config import REPORTS_DIR
from common.logger import LOG_BACKUP_COUNT, LOG_DIR, LOG_MAX_BYTES

router = APIRouter(prefix="/api/tool-logs", tags=["工具日志"])

PLATFORM_LOG_RETENTION_DAYS = int(os.getenv("PLATFORM_LOG_RETENTION_DAYS", os.getenv("LOG_RETENTION_DAYS", 90)))
TASK_LOG_RETENTION_DAYS = int(os.getenv("TASK_LOG_RETENTION_DAYS", os.getenv("LOG_RETENTION_DAYS", 90)))
LOG_CLEANUP_INTERVAL_SECONDS = int(os.getenv("LOG_CLEANUP_INTERVAL_SECONDS", 24 * 60 * 60))

PLATFORM_LOGS = {
    "manager": ("manager.log", "Manager 服务日志"),
    "api": ("api.log", "API 请求日志"),
    "agent": ("agent.log", "Agent 运行日志"),
    "task": ("task.log", "任务事件日志"),
    "error": ("error.log", "平台错误日志"),
}

TASK_LOGS = {
    "jmeter": ("jmeter.log", "JMeter 执行日志"),
    "error_responses": ("error_responses.jsonl", "错误响应采样日志"),
}


def _safe_join(base_dir: str, *parts: str) -> str:
    base = os.path.realpath(base_dir)
    path = os.path.realpath(os.path.join(base, *parts))
    if path != base and not path.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="非法路径")
    return path


def _tail_text(path: str, max_lines: int) -> str:
    return "\n".join(_tail_lines(path, max_lines))


def _tail_lines(path: str, max_lines: int) -> list:
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="日志不存在")

    max_lines = max(1, min(max_lines, 5000))
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            lines.append(line.rstrip("\n"))
            if len(lines) > max_lines:
                lines.pop(0)
    return lines


def _log_meta(path: str) -> dict:
    exists = os.path.exists(path)
    stat = os.stat(path) if exists else None
    return {
        "exists": exists,
        "size": stat.st_size if stat else 0,
        "modified_at": stat.st_mtime if stat else None,
    }


def _iter_platform_log_files(include_backups: bool = False):
    for key, (filename, description) in PLATFORM_LOGS.items():
        path = _safe_join(LOG_DIR, filename)
        yield {
            "scope": "platform",
            "key": key,
            "filename": filename,
            "description": description,
            "path": path,
            "backup": False,
        }

    if not include_backups or not os.path.isdir(LOG_DIR):
        return

    filenames = [filename for filename, _ in PLATFORM_LOGS.values()]
    for entry in sorted(os.listdir(LOG_DIR)):
        if not any(entry.startswith(f"{filename}.") for filename in filenames):
            continue
        path = _safe_join(LOG_DIR, entry)
        if os.path.isfile(path):
            yield {
                "scope": "platform",
                "key": "backup",
                "filename": entry,
                "description": "平台轮转日志备份",
                "path": path,
                "backup": True,
            }


def _iter_task_log_files():
    if not os.path.isdir(REPORTS_DIR):
        return

    for task_id in sorted(os.listdir(REPORTS_DIR)):
        task_dir = _safe_join(REPORTS_DIR, task_id)
        if not os.path.isdir(task_dir):
            continue
        for agent_id in sorted(os.listdir(task_dir)):
            agent_dir = _safe_join(task_dir, agent_id)
            if not os.path.isdir(agent_dir):
                continue
            for key, (filename, description) in TASK_LOGS.items():
                yield {
                    "scope": "task",
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "key": key,
                    "filename": filename,
                    "description": description,
                    "path": _safe_join(agent_dir, filename),
                    "backup": False,
                }


def _detect_level(line: str) -> str:
    upper = line.upper()
    if "CRITICAL" in upper:
        return "CRITICAL"
    if " ERROR " in upper or "[ERROR]" in upper or "EXCEPTION" in upper or "TRACEBACK" in upper or "CAUSED BY" in upper:
        return "ERROR"
    if " WARN " in upper or "[WARN" in upper or "WARNING" in upper:
        return "WARN"
    if " INFO " in upper or "[INFO]" in upper:
        return "INFO"
    if " DEBUG " in upper or "[DEBUG]" in upper:
        return "DEBUG"
    return "OTHER"


def _issue_signature(line: str) -> str:
    normalized = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*", "", line)
    normalized = re.sub(r"task-[0-9a-fA-F]+", "task-*", normalized)
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:180] or "unknown"


def _source_name(item: dict) -> str:
    if item["scope"] == "task":
        return f"{item['task_id']}/{item['agent_id']}/{item['filename']}"
    return item["filename"]


def _empty_analysis_result() -> dict:
    return {
        "generated_at": time.time(),
        "config": {
            "log_max_bytes": LOG_MAX_BYTES,
            "log_backup_count": LOG_BACKUP_COUNT,
            "platform_log_retention_days": PLATFORM_LOG_RETENTION_DAYS,
            "task_log_retention_days": TASK_LOG_RETENTION_DAYS,
            "cleanup_interval_seconds": LOG_CLEANUP_INTERVAL_SECONDS,
        },
        "storage": {
            "platform_log_bytes": 0,
            "task_log_bytes": 0,
            "total_log_bytes": 0,
        },
        "summary": {
            "files_scanned": 0,
            "lines_scanned": 0,
            "missing_files": 0,
            "level_counts": {"DEBUG": 0, "INFO": 0, "WARN": 0, "ERROR": 0, "CRITICAL": 0, "OTHER": 0},
        },
        "top_issues": [],
        "recent_issues": [],
    }


def analyze_logs(lines_per_file: int = 1000, include_tasks: bool = True) -> dict:
    """扫描平台与任务工具日志，生成轻量错误/告警分析。"""
    lines_per_file = max(100, min(lines_per_file, 5000))
    result = _empty_analysis_result()
    issue_counts = Counter()
    issue_sources = {}
    issue_examples = {}
    recent_issues = []

    items = list(_iter_platform_log_files(include_backups=True))
    if include_tasks:
        items.extend(list(_iter_task_log_files() or []))

    for item in items:
        path = item["path"]
        meta = _log_meta(path)
        if item["scope"] == "platform":
            result["storage"]["platform_log_bytes"] += meta["size"]
        else:
            result["storage"]["task_log_bytes"] += meta["size"]

        if not meta["exists"]:
            result["summary"]["missing_files"] += 1
            continue

        result["summary"]["files_scanned"] += 1
        try:
            lines = _tail_lines(path, lines_per_file)
        except HTTPException:
            result["summary"]["missing_files"] += 1
            continue

        source = _source_name(item)
        for line in lines:
            result["summary"]["lines_scanned"] += 1
            level = _detect_level(line)
            result["summary"]["level_counts"][level] += 1
            if level in {"WARN", "ERROR", "CRITICAL"}:
                signature = _issue_signature(line)
                issue_counts[signature] += 1
                issue_sources[signature] = source
                issue_examples[signature] = line[:300]
                recent_issues.append({
                    "level": level,
                    "source": source,
                    "message": line[:500],
                })

    result["storage"]["total_log_bytes"] = (
        result["storage"]["platform_log_bytes"] + result["storage"]["task_log_bytes"]
    )
    result["top_issues"] = [
        {
            "signature": signature,
            "count": count,
            "source": issue_sources.get(signature, ""),
            "example": issue_examples.get(signature, ""),
        }
        for signature, count in issue_counts.most_common(10)
    ]
    result["recent_issues"] = recent_issues[-20:]
    return result


def _cleanup_file(path: str, cutoff: float, dry_run: bool, result: dict, scope: str):
    if not os.path.isfile(path):
        return
    stat = os.stat(path)
    if stat.st_mtime >= cutoff:
        return

    item = {
        "path": path,
        "scope": scope,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }
    result["matched_files"] += 1
    result["matched_bytes"] += stat.st_size
    if dry_run:
        if len(result["files"]) < 100:
            result["files"].append(item)
        return

    os.remove(path)
    result["deleted_files"] += 1
    result["deleted_bytes"] += stat.st_size
    if len(result["files"]) < 100:
        result["files"].append(item)


def cleanup_expired_logs(dry_run: bool = False) -> dict:
    """清理过期工具日志，不删除压测报告和结果数据。"""
    now = time.time()
    result = {
        "dry_run": dry_run,
        "generated_at": now,
        "platform_log_retention_days": PLATFORM_LOG_RETENTION_DAYS,
        "task_log_retention_days": TASK_LOG_RETENTION_DAYS,
        "matched_files": 0,
        "matched_bytes": 0,
        "deleted_files": 0,
        "deleted_bytes": 0,
        "files": [],
    }

    platform_cutoff = now - PLATFORM_LOG_RETENTION_DAYS * 86400
    if os.path.isdir(LOG_DIR):
        filenames = [filename for filename, _ in PLATFORM_LOGS.values()]
        for entry in os.listdir(LOG_DIR):
            if not any(entry.startswith(f"{filename}.") for filename in filenames):
                continue
            _cleanup_file(_safe_join(LOG_DIR, entry), platform_cutoff, dry_run, result, "platform")

    task_cutoff = now - TASK_LOG_RETENTION_DAYS * 86400
    for item in _iter_task_log_files() or []:
        _cleanup_file(item["path"], task_cutoff, dry_run, result, "task")

    return result


@router.get("/platform")
def list_platform_logs():
    """列出平台组件日志。"""
    items = []
    for key, (filename, description) in PLATFORM_LOGS.items():
        path = _safe_join(LOG_DIR, filename)
        items.append({
            "key": key,
            "filename": filename,
            "description": description,
            **_log_meta(path),
        })
    return {"logs": items}


@router.get("/analysis")
def get_log_analysis(
    lines_per_file: int = Query(1000, ge=100, le=5000),
    include_tasks: bool = Query(True),
):
    """返回平台与任务工具日志的轻量分析摘要。"""
    return analyze_logs(lines_per_file=lines_per_file, include_tasks=include_tasks)


@router.post("/cleanup")
def cleanup_tool_logs(dry_run: bool = Query(False)):
    """手动触发过期工具日志清理。"""
    return cleanup_expired_logs(dry_run=dry_run)


@router.get("/platform/{log_key}")
def read_platform_log(log_key: str, lines: int = Query(300, ge=1, le=5000)):
    """读取平台组件日志尾部内容。"""
    if log_key not in PLATFORM_LOGS:
        raise HTTPException(status_code=404, detail="未知日志类型")

    filename, description = PLATFORM_LOGS[log_key]
    path = _safe_join(LOG_DIR, filename)
    content = _tail_text(path, lines)
    return {
        "scope": "platform",
        "key": log_key,
        "filename": filename,
        "description": description,
        "lines": lines,
        "content": content,
        **_log_meta(path),
    }


@router.get("/tasks/{task_id}")
def list_task_tool_logs(task_id: str):
    """列出指定任务下各 Agent 的工具日志。"""
    task_dir = _safe_join(REPORTS_DIR, task_id)
    if not os.path.isdir(task_dir):
        raise HTTPException(status_code=404, detail="任务日志目录不存在")

    agents = []
    for agent_id in sorted(os.listdir(task_dir)):
        agent_dir = _safe_join(task_dir, agent_id)
        if not os.path.isdir(agent_dir):
            continue
        logs = []
        for key, (filename, description) in TASK_LOGS.items():
            path = _safe_join(agent_dir, filename)
            logs.append({
                "key": key,
                "filename": filename,
                "description": description,
                **_log_meta(path),
            })
        agents.append({"agent_id": agent_id, "logs": logs})

    return {"task_id": task_id, "agents": agents}


@router.get("/tasks/{task_id}/{agent_id}/{log_key}")
def read_task_tool_log(
    task_id: str,
    agent_id: str,
    log_key: str,
    lines: int = Query(300, ge=1, le=5000),
):
    """读取指定任务、Agent 的工具日志尾部内容。"""
    if log_key not in TASK_LOGS:
        raise HTTPException(status_code=404, detail="未知任务日志类型")

    filename, description = TASK_LOGS[log_key]
    path = _safe_join(REPORTS_DIR, task_id, agent_id, filename)
    content = _tail_text(path, lines)
    return {
        "scope": "task",
        "task_id": task_id,
        "agent_id": agent_id,
        "key": log_key,
        "filename": filename,
        "description": description,
        "lines": lines,
        "content": content,
        **_log_meta(path),
    }
