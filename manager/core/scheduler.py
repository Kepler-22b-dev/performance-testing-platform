import sys
import os
import time
import json
import uuid
import redis
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB,
    SCRIPTS_DIR, REPORTS_DIR,
    REDIS_CHANNEL_COMMAND, REDIS_CHANNEL_RESULT,
    REDIS_CHANNEL_PROGRESS, MAX_CONCURRENT_TASKS, TASK_TIMEOUT,
)
from common.protocol import (
    TaskCommand, TaskResult, ProgressUpdate,
    CommandType, TaskStatus,
)


class TaskScheduler:
    def __init__(self, node_manager=None):
        self.redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self._progress: dict[str, ProgressUpdate] = {}
        self._node_manager = node_manager
        self._load_tasks_from_redis()

    def _load_tasks_from_redis(self):
        self._tasks: dict[str, dict] = {}
        keys = self.redis.keys("jmeter:task:*")
        for key in keys:
            task_id = key.replace("jmeter:task:", "")
            if ":" in task_id:
                continue
            try:
                data = self.redis.hget(key, "data")
                if data:
                    task = json.loads(data)
                    self._tasks[task_id] = task
            except Exception:
                pass

    def create_task(
        self,
        script_id: str,
        target_agents: list[str],
        jmeter_args: dict,
        timeout: int = TASK_TIMEOUT,
        distributed: bool = False,
        remote_hosts: str = None,
        csv_file: str = None,
        csv_variable_names: str = None,
        csv_delimiter: str = ",",
        csv_recycle: bool = True,
        csv_stop_on_eof: bool = False,
    ) -> str:
        task_id = f"task-{uuid.uuid4().hex[:8]}"

        script_path = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Script not found: {script_id}")

        with open(script_path, "r") as f:
            script_content = f.read()

        task = {
            "task_id": task_id,
            "script_id": script_id,
            "target_agents": target_agents,
            "jmeter_args": jmeter_args,
            "timeout": timeout,
            "distributed": distributed,
            "remote_hosts": remote_hosts,
            "csv_file": csv_file,
            "csv_variable_names": csv_variable_names,
            "csv_delimiter": csv_delimiter,
            "csv_recycle": csv_recycle,
            "csv_stop_on_eof": csv_stop_on_eof,
            "status": TaskStatus.PENDING,
            "created_at": time.time(),
            "results": {},
        }

        self._tasks[task_id] = task
        self._sync_task(task)

        self.redis.sadd("jmeter:task_ids", task_id)

        return task_id

    def start_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False

        if task["status"] not in (TaskStatus.PENDING,):
            return False

        task["status"] = TaskStatus.RUNNING
        task["start_time"] = time.time()
        self._sync_task(task)

        script_path = os.path.join(SCRIPTS_DIR, f"{task['script_id']}.jmx")
        script_content = None
        if not task.get("csv_file") and os.path.exists(script_path):
            with open(script_path, "r") as f:
                script_content = f.read()

        for agent_id in task["target_agents"]:
            command = TaskCommand(
                command=CommandType.EXECUTE,
                task_id=task_id,
                script_path=os.path.join(SCRIPTS_DIR, f"{task['script_id']}.jmx"),
                script_content=script_content if not task.get("csv_file") else None,
                jmeter_args=task["jmeter_args"],
                timeout=task["timeout"],
                distributed=task.get("distributed", False),
                remote_hosts=task.get("remote_hosts"),
                csv_file=task.get("csv_file"),
                csv_variable_names=task.get("csv_variable_names"),
                csv_delimiter=task.get("csv_delimiter", ","),
                csv_recycle=task.get("csv_recycle", True),
                csv_stop_on_eof=task.get("csv_stop_on_eof", False),
            )
            self.redis.publish(REDIS_CHANNEL_COMMAND, command.to_json())

        return True

    def stop_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task["status"] != TaskStatus.RUNNING:
            return False

        for agent_id in task["target_agents"]:
            command = TaskCommand(
                command=CommandType.STOP,
                task_id=task_id,
                script_path="",
            )
            self.redis.publish(REDIS_CHANNEL_COMMAND, command.to_json())

        task["status"] = TaskStatus.STOPPED
        task["end_time"] = time.time()
        self._sync_task(task)
        return True

    def delete_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.get("status") == TaskStatus.RUNNING:
            return False

        del self._tasks[task_id]
        self.redis.delete(f"jmeter:task:{task_id}")
        self.redis.srem("jmeter:task_ids", task_id)
        return True

    def rerun_task(self, task_id: str) -> Optional[str]:
        old_task = self._tasks.get(task_id)
        if not old_task:
            return None

        new_task_id = self.create_task(
            script_id=old_task["script_id"],
            target_agents=old_task["target_agents"],
            jmeter_args=old_task["jmeter_args"],
            timeout=old_task.get("timeout", TASK_TIMEOUT),
            distributed=old_task.get("distributed", False),
            remote_hosts=old_task.get("remote_hosts"),
        )
        self.start_task(new_task_id)
        return new_task_id

    def batch_create_tasks(self, tasks_config: list[dict]) -> list[str]:
        task_ids = []
        for cfg in tasks_config:
            try:
                task_id = self.create_task(
                    script_id=cfg["script_id"],
                    target_agents=cfg.get("target_agents", []),
                    jmeter_args=cfg.get("jmeter_args", {}),
                    timeout=cfg.get("timeout", TASK_TIMEOUT),
                    distributed=cfg.get("distributed", False),
                    remote_hosts=cfg.get("remote_hosts"),
                    csv_file=cfg.get("csv_file"),
                    csv_variable_names=cfg.get("csv_variable_names"),
                    csv_delimiter=cfg.get("csv_delimiter", ","),
                    csv_recycle=cfg.get("csv_recycle", True),
                    csv_stop_on_eof=cfg.get("csv_stop_on_eof", False),
                )
                task_ids.append(task_id)
                if cfg.get("auto_start", False):
                    self.start_task(task_id)
            except Exception as e:
                task_ids.append(f"error:{str(e)}")
        return task_ids

    def get_task(self, task_id: str) -> Optional[dict]:
        task = self._tasks.get(task_id)
        if task:
            return task
        try:
            data = self.redis.hget(f"jmeter:task:{task_id}", "data")
            if data:
                task = json.loads(data)
                self._tasks[task_id] = task
                return task
        except Exception:
            pass
        return None

    def get_all_tasks(self) -> list[dict]:
        self._load_tasks_from_redis()
        return list(self._tasks.values())

    def handle_result(self, result: TaskResult):
        task = self._tasks.get(result.task_id)
        if not task:
            task = self.get_task(result.task_id)
        if not task:
            return

        task["results"][result.agent_id] = {
            "status": result.status,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "report_path": result.report_path,
            "error_message": result.error_message,
            "summary": result.summary,
        }

        all_done = all(
            r["status"] in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)
            for r in task["results"].values()
        )

        if all_done:
            has_failed = any(
                r["status"] in (TaskStatus.FAILED, TaskStatus.STOPPED)
                for r in task["results"].values()
            )
            task["status"] = TaskStatus.FAILED if has_failed else TaskStatus.COMPLETED
            task["end_time"] = time.time()

        self._sync_task(task)

        if all_done:
            self._trigger_notifications(task)
            self._check_alerts(task)

    def handle_progress(self, update: ProgressUpdate):
        self._progress[update.task_id] = update

    def get_progress(self, task_id: str) -> Optional[ProgressUpdate]:
        return self._progress.get(task_id)

    def _sync_task(self, task: dict):
        self.redis.hset(
            f"jmeter:task:{task['task_id']}",
            mapping={"data": json.dumps(task, ensure_ascii=False, default=str)},
        )

    def _trigger_notifications(self, task: dict):
        try:
            notifications_data = self.redis.hget("jmeter:config", "notifications")
            if not notifications_data:
                return
            config = json.loads(notifications_data)
            webhooks = config.get("webhooks", [])
            for webhook_url in webhooks:
                try:
                    import urllib.request
                    payload = json.dumps({
                        "event": "task_completed",
                        "task_id": task["task_id"],
                        "status": task["status"],
                        "script_id": task.get("script_id", ""),
                        "results": task.get("results", {}),
                    }).encode()
                    req = urllib.request.Request(
                        webhook_url,
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    urllib.request.urlopen(req, timeout=10)
                except Exception:
                    pass
        except Exception:
            pass

    def _check_alerts(self, task: dict):
        try:
            from manager.api.alerts import check_alerts
            triggered = check_alerts(task)
            if triggered:
                self._send_alert_notification(task, triggered)
        except Exception:
            pass

    def _send_alert_notification(self, task: dict, triggered: list):
        try:
            notifications_data = self.redis.hget("jmeter:config", "notifications")
            if not notifications_data:
                return
            config = json.loads(notifications_data)
            if not config.get("enabled"):
                return
            webhooks = config.get("webhooks", [])
            for webhook_url in webhooks:
                try:
                    import urllib.request
                    payload = json.dumps({
                        "event": "alert_triggered",
                        "task_id": task["task_id"],
                        "alerts": triggered,
                    }).encode()
                    req = urllib.request.Request(
                        webhook_url,
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    urllib.request.urlopen(req, timeout=10)
                except Exception:
                    pass
        except Exception:
            pass
