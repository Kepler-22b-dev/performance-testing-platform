"""日志模块 - 统一管理平台的日志配置

支持两种日志格式：
- text（默认）：人类可读的纯文本格式，适合开发环境
- json：结构化 JSON 格式，适合对接 ELK/Loki 等日志分析系统

通过环境变量 LOG_FORMAT 控制，例如：LOG_FORMAT=json
"""
import os
import json
import logging
import sys
from typing import Optional
from logging.handlers import RotatingFileHandler

# 日志相关配置常量
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 50 * 1024 * 1024))  # 单个日志文件最大 50MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 20))          # 保留 20 个历史日志
LOG_FORMAT = os.getenv("LOG_FORMAT", "text")  # text 或 json


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式化器。

    将日志记录输出为 JSON 格式，包含 timestamp、level、logger、message 字段。
    支持附加 task_id 和 agent_id 上下文字段，便于按任务/节点过滤日志。
    异常信息通过 exception 字段输出完整堆栈。
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 附加任务上下文字段（如果存在）
        if hasattr(record, "task_id"):
            log_entry["task_id"] = record.task_id
        if hasattr(record, "agent_id"):
            log_entry["agent_id"] = record.agent_id
        # 附加异常堆栈
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def _ensure_log_dir() -> None:
    """确保日志目录存在，不存在时自动创建。"""
    os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(
    name: str,
    level: str = "INFO",
    console: bool = True,
    file: bool = True,
    log_file: Optional[str] = None,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
) -> logging.Logger:
    """获取或创建指定名称的日志记录器。

    Args:
        name: 日志记录器名称，通常使用模块路径（如 "manager.api.tasks"）
        level: 日志级别，支持 DEBUG/INFO/WARNING/ERROR/CRITICAL
        console: 是否输出到控制台（标准输出）
        file: 是否输出到文件（RotatingFileHandler 自动轮转）
        log_file: 日志文件名，默认为 {name}.log
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的历史日志文件数量
    """
    _ensure_log_dir()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    if LOG_FORMAT == "json":
        fmt: logging.Formatter = JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    else:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    if console:
        console_handler: logging.Handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)
    if file:
        if not log_file:
            log_file = f"{name.replace('.', '_')}.log"
        file_handler: logging.Handler = RotatingFileHandler(
            os.path.join(LOG_DIR, log_file),
            maxBytes=max_bytes or LOG_MAX_BYTES,
            backupCount=backup_count if backup_count is not None else LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    return logger

# ===== 便捷函数：预配置常用日志记录器 =====

def get_api_logger() -> logging.Logger:
    """获取 API 请求日志记录器，输出到 logs/api.log"""
    return get_logger("api", log_file="api.log")


def get_task_logger() -> logging.Logger:
    """获取任务事件日志记录器，输出到 logs/task.log"""
    return get_logger("task", log_file="task.log")


def get_agent_logger() -> logging.Logger:
    """获取 Agent 运行日志记录器，输出到 logs/agent.log"""
    return get_logger("agent", log_file="agent.log")


def log_task_event(
    logger: logging.Logger,
    task_id: str,
    event: str,
    details: Optional[dict] = None,
) -> None:
    """记录任务生命周期事件。

    格式：[Task:{task_id}] {event} | {json_details}

    Args:
        logger: 日志记录器实例
        task_id: 任务唯一标识
        event: 事件描述（如 "开始执行"、"执行完成"）
        details: 可选的事件详情字典，自动序列化为 JSON
    """
    msg = f"[Task:{task_id}] {event}"
    if details:
        msg += f" | {json.dumps(details, ensure_ascii=False)}"
    logger.info(msg)


def log_error(
    logger: logging.Logger,
    error: Exception,
    context: str = "",
) -> None:
    """记录异常错误，附带完整堆栈信息。

    Args:
        logger: 日志记录器实例
        error: 捕获的异常对象
        context: 错误上下文描述（如 "命令处理"、"任务执行"）
    """
    msg = f"Error: {type(error).__name__}: {str(error)}"
    if context:
        msg = f"[{context}] {msg}"
    logger.error(msg, exc_info=True)
