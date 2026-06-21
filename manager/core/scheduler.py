"""
任务调度器模块 - 管理测试任务的生命周期
负责任务的创建、启动、停止、结果处理等
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


class TaskScheduler:
    """
    任务调度器
    管理所有测试任务的状态和执行
    """

    def __init__(self, node_manager=None):
        """
        初始化调度器
        Args:
            node_manager: 节点管理器实例，用于获取可用 Agent
        """
        self.redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self._progress: dict[str, ProgressUpdate] = {}
        self._node_manager = node_manager
        # 从 Redis 加载已有任务
        self._load_tasks_from_redis()
        self._cleanup_stuck_tasks()

    def _cleanup_stuck_tasks(self):
        """检测并清理卡住的任务（running 状态但无结果且超时）"""
        now = time.time()
        stuck_timeout = 600  # 10 分钟
        for task_id, task in list(self._tasks.items()):
            if task.get("status") == "running":
                start_time = task.get("start_time", 0)
                elapsed = now - start_time
                has_results = len(task.get("results", {})) > 0
                if elapsed > stuck_timeout and not has_results:
                    task["status"] = "failed"
                    task["end_time"] = now
                    task["results"] = {}
                    task["error_message"] = f"任务超时：运行超过 {int(elapsed)}s 但未收到结果"
                    self._sync_task(task)

    def _load_tasks_from_redis(self):
        """从 Redis 加载所有任务数据，实现持久化"""
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
        """
        创建新的测试任务
        Args:
            script_id: 脚本 ID
            target_agents: 目标 Agent 列表
            jmeter_args: JMeter 参数 (threads, duration 等)
            timeout: 超时时间(秒)
            distributed: 是否分布式模式
            remote_hosts: 远程主机
            csv_file: CSV 数据文件路径
            csv_variable_names: CSV 变量名
            csv_delimiter: CSV 分隔符
            csv_recycle: 是否循环读取
            csv_stop_on_eof: 读完是否停止
        Returns:
            task_id: 创建的任务 ID
        """
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
        """
        启动任务
        向所有目标 Agent 发送执行命令
        """
        task = self._tasks.get(task_id)
        if not task:
            return False

        if task["status"] not in (TaskStatus.PENDING,):
            return False

        task["status"] = TaskStatus.RUNNING
        task["start_time"] = time.time()
        self._sync_task(task)

        # 读取脚本内容
        script_path = os.path.join(SCRIPTS_DIR, f"{task['script_id']}.jmx")
        script_content = None
        if not task.get("csv_file") and os.path.exists(script_path):
            with open(script_path, "r") as f:
                script_content = f.read()

        # 向每个 Agent 发送执行命令
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
        """停止任务 - 向所有 Agent 发送停止命令"""
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
        """删除任务(仅限非运行中的任务)"""
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
        """重新执行任务 - 复制原任务配置并启动"""
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
        """批量创建任务"""
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
        """获取任务详情"""
        task = self._tasks.get(task_id)
        if task:
            return task
        # 尝试从 Redis 加载
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
        """获取所有任务列表"""
        self._load_tasks_from_redis()
        return list(self._tasks.values())

    def handle_result(self, result: TaskResult):
        """
        处理 Agent 返回的任务结果
        当所有 Agent 都返回结果后，标记任务完成
        """
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

        # 检查是否所有 Agent 都已完成
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

        # 任务完成时触发通知和告警检查
        if all_done:
            self._trigger_notifications(task)
            self._check_alerts(task)

    def handle_progress(self, update: ProgressUpdate):
        """处理 Agent 上报的实时进度"""
        self._progress[update.task_id] = update

    def get_progress(self, task_id: str) -> Optional[ProgressUpdate]:
        """获取任务实时进度"""
        return self._progress.get(task_id)

    def _sync_task(self, task: dict):
        """同步任务数据到 Redis"""
        self.redis.hset(
            f"jmeter:task:{task['task_id']}",
            mapping={"data": json.dumps(task, ensure_ascii=False, default=str)},
        )

    def _trigger_notifications(self, task: dict):
        """任务完成时触发 Webhook 通知"""
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
        """检查告警规则"""
        try:
            from manager.api.alerts import check_alerts
            triggered = check_alerts(task)
            if triggered:
                self._send_alert_notification(task, triggered)
        except Exception:
            pass

    def _send_alert_notification(self, task: dict, triggered: list):
        """发送告警通知"""
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
