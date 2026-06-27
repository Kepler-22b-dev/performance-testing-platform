"""
任务调度器模块 - 管理测试任务的生命周期
负责任务的创建、启动、停止、结果处理等
使用 PostgreSQL 持久化任务数据，Redis 仅用于 Pub/Sub 消息
"""
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
from common.database import get_sync_db
from manager.core.db_sync import (
    db_create_task, db_get_task, db_get_all_tasks, db_get_running_tasks, db_update_task,
    db_delete_task, db_add_task_result, db_update_task_result,
    db_get_notification_config,
)


class TaskScheduler:
    """
    任务调度器
    管理所有测试任务的状态和执行
    """

    def __init__(self, node_manager=None):
        self.redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self._progress: dict[str, ProgressUpdate] = {}
        self._progress_history: dict[str, list[dict]] = {}
        self._node_manager = node_manager

    def _cleanup_stuck_tasks(self):
        """检测并清理卡住的任务"""
        now = time.time()
        db = get_sync_db()
        try:
            tasks = db_get_running_tasks(db)
            for task in tasks:
                start_time = task.get("start_time", 0)
                elapsed = now - start_time
                task_timeout = task.get("timeout", TASK_TIMEOUT)
                if elapsed > task_timeout:
                    results = task.get("results", {})
                    if not results:
                        error_msg = f"任务超时：运行超过 {int(elapsed)}s 但未收到任何 Agent 响应"
                    else:
                        completed = sum(1 for r in results.values() if r.get("status") in ("completed", "failed", "stopped"))
                        total = len(results)
                        error_msg = f"任务超时：运行超过 {int(elapsed)}s，{completed}/{total} 个 Agent 已响应"
                    db_update_task(db, task["task_id"],
                        status="failed", end_time=now,
                        error_message=error_msg,
                    )
        finally:
            db.close()

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
        if not target_agents:
            raise ValueError("至少需要指定一个目标 Agent")

        if timeout <= 0:
            raise ValueError("超时时间必须大于 0")

        running_count = len(self.get_running_tasks())
        if running_count >= MAX_CONCURRENT_TASKS:
            raise RuntimeError(f"并发任务数已达上限 ({MAX_CONCURRENT_TASKS})，请等待现有任务完成")

        task_id = f"task-{uuid.uuid4().hex[:8]}"

        script_path = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Script not found: {script_id}")

        task_data = {
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
        }

        db = get_sync_db()
        try:
            db_create_task(db, task_data)
        finally:
            db.close()

        return task_id

    def start_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False

        if task["status"] not in (TaskStatus.PENDING,):
            return False

        self._progress.pop(task_id, None)
        self._progress_history.pop(task_id, None)

        script_path = os.path.join(SCRIPTS_DIR, f"{task['script_id']}.jmx")
        if not task.get("csv_file") and not os.path.exists(script_path):
            db = get_sync_db()
            try:
                db_update_task(db, task_id,
                    status="failed", end_time=time.time(),
                    error_message=f"脚本文件不存在: {task['script_id']}.jmx",
                )
            finally:
                db.close()
            return False

        db = get_sync_db()
        try:
            db_update_task(db, task_id,
                status=TaskStatus.RUNNING, start_time=time.time(),
            )
        finally:
            db.close()

        script_content = None
        if not task.get("csv_file") and os.path.exists(script_path):
            with open(script_path, "r") as f:
                script_content = f.read()

        for agent_id in task["target_agents"]:
            command = TaskCommand(
                command=CommandType.EXECUTE,
                task_id=task_id,
                script_path=script_path,
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
        task = self.get_task(task_id)
        if not task or task["status"] != TaskStatus.RUNNING:
            return False

        for agent_id in task["target_agents"]:
            command = TaskCommand(
                command=CommandType.STOP,
                task_id=task_id,
                script_path="",
            )
            self.redis.publish(REDIS_CHANNEL_COMMAND, command.to_json())

        return True

    def delete_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False
        if task.get("status") == TaskStatus.RUNNING:
            return False

        db = get_sync_db()
        try:
            db_delete_task(db, task_id)
        finally:
            db.close()
        return True

    def rerun_task(self, task_id: str) -> Optional[str]:
        old_task = self.get_task(task_id)
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

    def stop_and_rerun(self, task_id: str, overrides: dict = None) -> Optional[str]:
        """停止当前任务并用新参数重启"""
        task = self.get_task(task_id)
        if not task:
            return None

        if task["status"] == TaskStatus.RUNNING:
            self.stop_task(task_id)

        self._progress.pop(task_id, None)
        self._progress_history.pop(task_id, None)

        jmeter_args = dict(task.get("jmeter_args", {}))
        if overrides:
            if "threads" in overrides:
                jmeter_args["threads"] = str(overrides["threads"])
            if "ramp_time" in overrides:
                jmeter_args["ramp_time"] = str(overrides["ramp_time"])
            if "duration" in overrides:
                jmeter_args["duration"] = str(overrides["duration"])

        timeout = task.get("timeout", TASK_TIMEOUT)
        if overrides and "timeout" in overrides:
            timeout = overrides["timeout"]

        target_agents = overrides.get("target_agents", task["target_agents"]) if overrides else task["target_agents"]

        new_task_id = self.create_task(
            script_id=task["script_id"],
            target_agents=target_agents,
            jmeter_args=jmeter_args,
            timeout=timeout,
            distributed=task.get("distributed", False),
            remote_hosts=task.get("remote_hosts"),
            csv_file=task.get("csv_file"),
            csv_variable_names=task.get("csv_variable_names"),
            csv_delimiter=task.get("csv_delimiter", ","),
            csv_recycle=task.get("csv_recycle", True),
            csv_stop_on_eof=task.get("csv_stop_on_eof", False),
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
        db = get_sync_db()
        try:
            return db_get_task(db, task_id)
        finally:
            db.close()

    def get_all_tasks(self) -> list[dict]:
        db = get_sync_db()
        try:
            return db_get_all_tasks(db)
        finally:
            db.close()

    def get_running_tasks(self) -> list[dict]:
        db = get_sync_db()
        try:
            return db_get_running_tasks(db)
        finally:
            db.close()

    def handle_result(self, result: TaskResult):
        task = self.get_task(result.task_id)
        if not task:
            return

        db = get_sync_db()
        try:
            existing_results = task.get("results", {})
            agent_result = existing_results.get(result.agent_id)

            if agent_result:
                db_update_task_result(db, result.task_id, result.agent_id,
                    status=result.status, start_time=result.start_time,
                    end_time=result.end_time, report_path=result.report_path,
                    error_message=result.error_message, summary=result.summary,
                )
            else:
                db_add_task_result(db, result.task_id, result.agent_id,
                    status=result.status, start_time=result.start_time,
                    end_time=result.end_time, report_path=result.report_path,
                    error_message=result.error_message, summary=result.summary,
                )

            task = db_get_task(db, result.task_id)
            if not task:
                return

            all_done = all(
                r["status"] in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)
                for r in task.get("results", {}).values()
            )

            if all_done:
                has_failed = any(
                    r["status"] in (TaskStatus.FAILED, TaskStatus.STOPPED)
                    for r in task.get("results", {}).values()
                )
                new_status = TaskStatus.FAILED if has_failed else TaskStatus.COMPLETED
                db_update_task(db, result.task_id,
                    status=new_status, end_time=time.time(),
                )
                try:
                    from manager.core.sample_cache import invalidate_cache
                    invalidate_cache(result.task_id)
                except Exception:
                    pass
        finally:
            db.close()

    def handle_progress(self, update: ProgressUpdate):
        self._progress[update.task_id] = update
        if update.task_id not in self._progress_history:
            self._progress_history[update.task_id] = []
        history = self._progress_history[update.task_id]
        history.append({
            "timestamp": update.timestamp,
            "elapsed": update.elapsed,
            "throughput": update.throughput,
            "avg_response_time": update.avg_response_time,
            "error_rate": update.error_rate,
            "success_rate": update.success_rate,
            "active_threads": update.active_threads,
            "total_samples": update.total_samples,
            "bytes_received": update.bytes_received,
            "avg_latency": update.avg_latency,
            "avg_connect_time": update.avg_connect_time,
        })
        if len(history) > 3600:
            self._progress_history[update.task_id] = history[-3600:]

    def get_progress(self, task_id: str) -> Optional[ProgressUpdate]:
        return self._progress.get(task_id)

    def get_progress_history(self, task_id: str) -> list[dict]:
        return self._progress_history.get(task_id, [])

    def _trigger_notifications(self, task: dict):
        try:
            db = get_sync_db()
            try:
                config = db_get_notification_config(db)
            finally:
                db.close()

            if not config.get("enabled"):
                return
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
                        webhook_url, data=payload,
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
            db = get_sync_db()
            try:
                config = db_get_notification_config(db)
            finally:
                db.close()

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
                        webhook_url, data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    urllib.request.urlopen(req, timeout=10)
                except Exception:
                    pass
        except Exception:
            pass
