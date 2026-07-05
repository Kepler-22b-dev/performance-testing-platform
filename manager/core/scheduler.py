"""
任务调度器模块 - 管理测试任务的生命周期
负责任务的创建、启动、停止、结果处理等
使用 PostgreSQL 持久化任务数据，Redis 仅用于 Pub/Sub 消息
"""
import sys
import os
import time
import json
import re
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
        self._progress_segments: dict[str, dict[str, ProgressUpdate]] = {}
        self._progress_history: dict[str, list[dict]] = {}
        self._node_manager = node_manager

    def _generate_task_id(self, db=None) -> str:
        """生成按日期递增的任务 ID，例如 task-20260704-001。"""
        date_str = time.strftime("%Y%m%d", time.localtime())
        prefix = f"task-{date_str}-"
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_seq = 0

        if db is not None:
            try:
                for task in db_get_all_tasks(db):
                    match = pattern.match(str(task.get("task_id", "")))
                    if match:
                        max_seq = max(max_seq, int(match.group(1)))
            except Exception:
                pass

        if os.path.isdir(REPORTS_DIR):
            for name in os.listdir(REPORTS_DIR):
                match = pattern.match(name)
                if match:
                    max_seq = max(max_seq, int(match.group(1)))

        for seq in range(max_seq + 1, max_seq + 10000):
            task_id = f"{prefix}{seq:03d}"
            if not os.path.exists(os.path.join(REPORTS_DIR, task_id)):
                return task_id

        raise RuntimeError("当日任务编号已用完，请清理历史任务或调整编号规则")

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
        enforce_single_agent_task: bool = True,
    ) -> str:
        if not target_agents:
            raise ValueError("至少需要指定一个目标 Agent")

        if timeout <= 0:
            raise ValueError("超时时间必须大于 0")

        running_tasks = self.get_running_tasks()
        if len(running_tasks) >= MAX_CONCURRENT_TASKS:
            raise RuntimeError(f"并发任务数已达上限 ({MAX_CONCURRENT_TASKS})，请等待现有任务完成")

        if enforce_single_agent_task:
            conflicts = self._find_pressure_machine_conflicts(
                running_tasks,
                target_agents,
                remote_hosts,
            )
            if conflicts:
                raise RuntimeError("压力机已有运行中的任务：" + "；".join(conflicts))

        script_path = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Script not found: {script_id}")

        db = get_sync_db()
        try:
            task_id = self._generate_task_id(db)
        finally:
            db.close()

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

    def _split_remote_hosts(self, remote_hosts: str | None) -> set[str]:
        if not remote_hosts:
            return set()
        return {host.strip() for host in str(remote_hosts).split(",") if host.strip()}

    def _find_pressure_machine_conflicts(
        self,
        running_tasks: list[dict],
        target_agents: list[str],
        remote_hosts: str | None,
    ) -> list[str]:
        target_agent_set = set(target_agents or [])
        remote_host_set = self._split_remote_hosts(remote_hosts)
        conflicts = []

        for task in running_tasks:
            task_id = task.get("task_id", "")
            busy_agents = target_agent_set & set(task.get("target_agents") or [])
            busy_slaves = remote_host_set & self._split_remote_hosts(task.get("remote_hosts"))

            if busy_agents:
                conflicts.append(f"{', '.join(sorted(busy_agents))} 正在执行 {task_id}")
            if busy_slaves:
                conflicts.append(f"{', '.join(sorted(busy_slaves))} 正在执行 {task_id}")

        return conflicts

    def start_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False

        if task["status"] not in (TaskStatus.PENDING,):
            return False

        self._progress.pop(task_id, None)
        self._progress_segments.pop(task_id, None)
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
                target_agent_id=agent_id,
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
                target_agent_id=agent_id,
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
        self._progress_segments.pop(task_id, None)
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
            enforce_single_agent_task=False,
        )
        self.start_task(new_task_id)
        return new_task_id

    def adjust_load(
        self,
        task_id: str,
        action: str,
        threads: int,
        ramp_time: int = 1,
        duration: int = 60,
    ) -> dict:
        """向运行中的 Agent 下发动态调压命令。"""
        task = self.get_task(task_id)
        if not task:
            raise ValueError("任务不存在")
        if task.get("status") != TaskStatus.RUNNING:
            raise RuntimeError("只有运行中的任务可以动态调压")

        action = str(action or "increase").strip().lower()
        if action not in {"increase", "decrease"}:
            raise ValueError("调压动作只支持 increase 或 decrease")

        try:
            threads = int(threads)
            ramp_time = int(ramp_time)
            duration = int(duration)
        except (TypeError, ValueError):
            raise ValueError("线程数、递增速率和持续时间必须是整数")

        if threads <= 0:
            raise ValueError("调整线程数必须大于 0")
        if ramp_time < 0:
            raise ValueError("递增速率不能小于 0 秒")
        if action == "increase" and duration <= 0:
            raise ValueError("加压持续时间必须大于 0 秒")

        target_agents = task.get("target_agents") or []
        if not target_agents:
            raise RuntimeError("任务没有可调压的目标 Agent")

        segment_id = f"dyn-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        script_path = os.path.join(SCRIPTS_DIR, f"{task['script_id']}.jmx")
        adjust_args = {
            "action": action,
            "threads": str(threads),
            "ramp_time": str(ramp_time),
            "duration": str(duration),
            "segment_id": segment_id,
        }

        for agent_id in target_agents:
            command = TaskCommand(
                command=CommandType.ADJUST_LOAD,
                task_id=task_id,
                script_path=script_path,
                target_agent_id=agent_id,
                jmeter_args=adjust_args,
                timeout=max(duration + ramp_time + 60, 120),
                distributed=task.get("distributed", False),
                remote_hosts=task.get("remote_hosts"),
                csv_file=task.get("csv_file"),
                csv_variable_names=task.get("csv_variable_names"),
                csv_delimiter=task.get("csv_delimiter", ","),
                csv_recycle=task.get("csv_recycle", True),
                csv_stop_on_eof=task.get("csv_stop_on_eof", False),
                segment_id=segment_id,
            )
            self.redis.publish(REDIS_CHANNEL_COMMAND, command.to_json())

        return {
            "task_id": task_id,
            "action": action,
            "threads": threads,
            "ramp_time": ramp_time,
            "duration": duration,
            "segment_id": segment_id,
            "target_agents": target_agents,
        }

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

    def get_all_tasks(self, offset: int = None, limit: int = None, status: str = None):
        db = get_sync_db()
        try:
            if offset is not None or limit is not None or status:
                from manager.core.db_sync import db_get_tasks_page
                total, tasks = db_get_tasks_page(
                    db,
                    offset=max(0, int(offset or 0)),
                    limit=max(1, min(500, int(limit or 100))),
                    status=status,
                )
                return total, tasks
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

            task_results = task.get("results", {}) or {}
            expected_agents = {
                str(agent_id)
                for agent_id in (task.get("target_agents") or [])
                if agent_id
            }
            terminal_statuses = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)

            if expected_agents:
                all_expected_reported = expected_agents.issubset(set(task_results.keys()))
                results_to_check = [
                    task_results[agent_id]
                    for agent_id in expected_agents
                    if agent_id in task_results
                ]
            else:
                all_expected_reported = bool(task_results)
                results_to_check = list(task_results.values())

            all_done = all_expected_reported and all(
                r["status"] in terminal_statuses
                for r in results_to_check
            )

            if all_done:
                has_failed = any(
                    r["status"] in (TaskStatus.FAILED, TaskStatus.STOPPED)
                    for r in task_results.values()
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

    def _aggregate_progress(self, task_id: str) -> Optional[ProgressUpdate]:
        segments = list(self._progress_segments.get(task_id, {}).values())
        if not segments:
            return None
        if len(segments) == 1:
            return segments[0]

        total_samples = sum(max(0, int(s.total_samples or 0)) for s in segments)
        active_threads = sum(max(0, int(s.active_threads or 0)) for s in segments)
        throughput = sum(float(s.throughput or 0) for s in segments)
        bytes_received = sum(max(0, int(s.bytes_received or 0)) for s in segments)
        bytes_sent = sum(max(0, int(s.bytes_sent or 0)) for s in segments)
        timestamp = max(float(s.timestamp or 0) for s in segments)
        elapsed = max(int(s.elapsed or 0) for s in segments)

        def weighted_avg(attr: str) -> float:
            if total_samples > 0:
                weighted = sum(
                    float(getattr(s, attr, 0) or 0) * max(0, int(s.total_samples or 0))
                    for s in segments
                )
                return round(weighted / total_samples, 2)
            values = [float(getattr(s, attr, 0) or 0) for s in segments]
            return round(sum(values) / len(values), 2) if values else 0.0

        error_rate = weighted_avg("error_rate")
        success_rate = weighted_avg("success_rate")

        return ProgressUpdate(
            task_id=task_id,
            agent_id="aggregate",
            timestamp=timestamp,
            elapsed=elapsed,
            active_threads=active_threads,
            throughput=round(throughput, 2),
            avg_response_time=weighted_avg("avg_response_time"),
            error_rate=error_rate,
            success_rate=success_rate,
            total_samples=total_samples,
            bytes_received=bytes_received,
            bytes_sent=bytes_sent,
            avg_latency=weighted_avg("avg_latency"),
            avg_connect_time=weighted_avg("avg_connect_time"),
            segment_id="aggregate",
        )

    def handle_progress(self, update: ProgressUpdate):
        update.segment_id = getattr(update, "segment_id", None) or "base"
        segment_key = f"{update.agent_id}:{update.segment_id}"
        self._progress_segments.setdefault(update.task_id, {})[segment_key] = update

        aggregate = self._aggregate_progress(update.task_id) or update
        self._progress[update.task_id] = aggregate
        if aggregate.task_id not in self._progress_history:
            self._progress_history[aggregate.task_id] = []
        history = self._progress_history[aggregate.task_id]
        history.append({
            "timestamp": aggregate.timestamp,
            "elapsed": aggregate.elapsed,
            "throughput": aggregate.throughput,
            "avg_response_time": aggregate.avg_response_time,
            "error_rate": aggregate.error_rate,
            "success_rate": aggregate.success_rate,
            "active_threads": aggregate.active_threads,
            "total_samples": aggregate.total_samples,
            "bytes_received": aggregate.bytes_received,
            "avg_latency": aggregate.avg_latency,
            "avg_connect_time": aggregate.avg_connect_time,
            "segment_id": aggregate.segment_id,
        })
        if len(history) > 3600:
            self._progress_history[aggregate.task_id] = history[-3600:]

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
