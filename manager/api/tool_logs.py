"""平台工具日志查看 API。"""

import os

from fastapi import APIRouter, HTTPException, Query

from common.config import REPORTS_DIR
from common.logger import LOG_DIR

router = APIRouter(prefix="/api/tool-logs", tags=["工具日志"])

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
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="日志不存在")

    max_lines = max(1, min(max_lines, 5000))
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            lines.append(line.rstrip("\n"))
            if len(lines) > max_lines:
                lines.pop(0)
    return "\n".join(lines)


def _log_meta(path: str) -> dict:
    exists = os.path.exists(path)
    stat = os.stat(path) if exists else None
    return {
        "exists": exists,
        "size": stat.st_size if stat else 0,
        "modified_at": stat.st_mtime if stat else None,
    }


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
