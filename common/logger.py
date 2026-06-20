"""日志模块 - 统一管理平台的日志配置"""
import os
import logging
import sys
from logging.handlers import RotatingFileHandler

_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")

def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)

def get_logger(name, level="INFO", console=True, file=True, log_file=None, max_bytes=10*1024*1024, backup_count=5):
    _ensure_log_dir()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    if console:
        h = logging.StreamHandler(sys.stdout)
        h.setLevel(log_level)
        h.setFormatter(fmt)
        logger.addHandler(h)
    if file:
        if not log_file:
            log_file = f"{name.replace('.', '_')}.log"
        h = RotatingFileHandler(os.path.join(LOG_DIR, log_file), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        h.setLevel(log_level)
        h.setFormatter(fmt)
        logger.addHandler(h)
    return logger

def get_api_logger(): return get_logger("api", log_file="api.log")
def get_task_logger(): return get_logger("task", log_file="task.log")
def get_agent_logger(): return get_logger("agent", log_file="agent.log")

def log_task_event(logger, task_id, event, details=None):
    msg = f"[Task:{task_id}] {event}"
    if details:
        import json
        msg += f" | {json.dumps(details, ensure_ascii=False)}"
    logger.info(msg)

def log_error(logger, error, context=""):
    msg = f"Error: {type(error).__name__}: {str(error)}"
    if context:
        msg = f"[{context}] {msg}"
    logger.error(msg, exc_info=True)
