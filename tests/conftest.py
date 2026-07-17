import sys
import os
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.protocol import TaskStatus


class FakeDB:
    """轻量内存数据库，模拟 PostgreSQL 的 task/task_result 操作"""

    def __init__(self):
        self.tasks = {}
        self.results = {}

    def close(self):
        pass


def make_task(task_id="task-test0001", status=TaskStatus.PENDING,
              start_time=None, timeout=3600, script_id="test_script",
              target_agents=None, results=None, **kwargs):
    return {
        "task_id": task_id,
        "script_id": script_id,
        "target_agents": target_agents or ["agent-001"],
        "jmeter_args": {"threads": "10", "ramp_time": "1", "duration": "10"},
        "timeout": timeout,
        "distributed": False,
        "remote_hosts": None,
        "csv_file": None,
        "csv_variable_names": None,
        "csv_delimiter": ",",
        "csv_recycle": True,
        "csv_stop_on_eof": False,
        "csv_distribution": "replicate",
        "status": status,
        "created_at": time.time(),
        "start_time": start_time,
        "end_time": None,
        "error_message": None,
        "results": results or {},
        **kwargs,
    }


def make_running_task(task_id="task-test0001", elapsed=100, timeout=3600, results=None):
    now = time.time()
    return make_task(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        start_time=now - elapsed,
        timeout=timeout,
        results=results or {},
    )


def make_completed_result(agent_id="agent-001", status=TaskStatus.COMPLETED):
    return {
        "agent_id": agent_id,
        "status": status,
        "start_time": time.time() - 10,
        "end_time": time.time(),
        "report_path": "/tmp/report.html",
        "error_message": None,
        "summary": {"total_samples": 100},
    }


def patch_scheduler():
    """返回 mock 过的 scheduler 模块补丁"""
    return {
        "get_sync_db": patch("manager.core.scheduler.get_sync_db"),
        "db_get_running_tasks": patch("manager.core.scheduler.db_get_running_tasks"),
        "db_get_task": patch("manager.core.scheduler.db_get_task"),
        "db_update_task": patch("manager.core.scheduler.db_update_task"),
        "db_create_task": patch("manager.core.scheduler.db_create_task"),
    }
