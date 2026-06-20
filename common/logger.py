"""
日志模块 - 统一管理平台的日志配置
支持控制台输出和文件输出，按日期自动轮转
"""
import os
import logging
import sys
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from datetime import datetime

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

# 日志目录
LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")

# 日志级别映射
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _ensure_log_dir():
    """确保日志目录存在"""
    os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(
    name: str,
    level: str = "INFO",
    console: bool = True,
    file: bool = True,
    log_file: str = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> logging.Logger:
    """
    获取配置好的 logger 实例

    Args:
        name: logger 名称（通常用模块名）
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        console: 是否输出到控制台
        file: 是否输出到文件
        log_file: 日志文件名（默认按名称生成）
        max_bytes: 单个日志文件最大大小
        backup_count: 保留的历史日志文件数量

    Returns:
        配置好的 Logger 实例
    """
    _ensure_log_dir()

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    log_level = LOG_LEVELS.get(level.upper(), logging.INFO)
    logger.setLevel(log_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # 文件输出
    if file:
        if not log_file:
            log_file = f"{name.replace('.', '_')}.log"
        log_path = os.path.join(LOG_DIR, log_file)

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_api_logger() -> logging.Logger:
    """获取 API 请求日志 logger"""
    return get_logger("api", level="INFO", log_file="api.log")


def get_task_logger() -> logging.Logger:
    """获取任务执行日志 logger"""
    return get_logger("task", level="INFO", log_file="task.log")


def get_agent_logger() -> logging.Logger:
    """获取 Agent 日志 logger"""
    return get_logger("agent", level="INFO", log_file="agent.log")


def get_error_logger() -> logging.Logger:
    """获取错误日志 logger"""
    return get_logger("error", level="ERROR", log_file="error.log")


def get_debug_logger() -> logging.Logger:
    """获取调试日志 logger"""
    return get_logger("debug", level="DEBUG", log_file="debug.log")


class APILoggingMiddleware:
    """
    FastAPI 中间件 - 自动记录所有 API 请求
    记录请求方法、路径、状态码、耗时
    """

    def __init__(self, app):
        self.app = app
        self.logger = get_api_logger()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "")
        path = scope.get("path", "")
        start_time = datetime.now()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status = message.get("status", 0)
                duration = (datetime.now() - start_time).total_seconds() * 1000
                self.logger.info(
                    f"{method} {path} -> {status} ({duration:.1f}ms)"
                )
            return await send(message)

        return await self.app(scope, receive, send_wrapper)


def log_task_event(logger: logging.Logger, task_id: str, event: str, details: dict = None):
    """
    记录任务事件

    Args:
        logger: 日志 logger
        task_id: 任务 ID
        event: 事件类型 (created/started/completed/failed/stopped)
        details: 附加详情
    """
    msg = f"[Task:{task_id}] {event}"
    if details:
        msg += f" | {json.dumps(details, ensure_ascii=False)}"
    logger.info(msg)


def log_error(logger: logging.Logger, error: Exception, context: str = ""):
    """
    记录错误日志

    Args:
        logger: 日志 logger
        error: 异常对象
        context: 错误上下文描述
    """
    msg = f"Error: {type(error).__name__}: {str(error)}"
    if context:
        msg = f"[{context}] {msg}"
    logger.error(msg, exc_info=True)


# 需要导入 json
import json
