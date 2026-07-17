"""
任务调度器模块 - 管理测试任务的生命周期
负责任务的创建、启动、停止、结果处理等
使用 PostgreSQL 持久化任务数据，Redis Stream 用于 Manager ↔ Agent 通信

消息流：
- 命令下发：Manager XADD → jmeter:command Stream → Agent XREADGROUP 消费
- 结果回传：Agent XADD → jmeter:result Stream → Manager XREADGROUP 消费
- 进度上报：Agent XADD → jmeter:progress Stream → Manager XREADGROUP 消费
"""
import sys
import os
import time
import json
import re
import uuid
import logging
import redis
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import (
    SCRIPTS_DIR, REPORTS_DIR,
    MAX_CONCURRENT_TASKS, TASK_TIMEOUT,
    REDIS_STREAM_MAX_LEN, COMMAND_TTL_SECONDS, ARTIFACT_INLINE_FALLBACK,
    get_agent_command_stream, get_redis_connection_kwargs,
)
from common.artifacts import get_artifact_store
from common.protocol import (
    TaskCommand, TaskResult, ProgressUpdate,
    CommandType, TaskStatus,
)
from common.database import get_sync_db
from manager.core.db_sync import (
    db_create_task, db_get_task, db_get_all_tasks, db_get_running_tasks,
    db_delete_task, db_add_task_result, db_transition_task_status,
    db_get_notification_config,
)
from manager.core.variables import prepare_csv_distribution, get_vars_dict

logger = logging.getLogger("scheduler")


# 这些参数控制平台的执行方式，必须由单个任务决定，不能继承全局 JMeter 属性。
_GLOBAL_VARIABLE_EXCLUDED_KEYS = {
    "threads", "ramp_time", "duration", "scenario",
    "jvm_heap_mb", "capture_error_log", "enforce_single_agent_task",
    "result_format", "debug_result_xml",
    "error_log_sample_limit", "error_log_max_body_chars",
}


class TaskScheduler:
    """
    任务调度器
    管理所有测试任务的状态和执行

    核心职责：
    1. 任务生命周期管理（创建、启动、停止、删除）
    2. 通过 Redis Stream 与 Agent 通信
    3. 收集和聚合多 Agent 的进度数据
    4. 并发任务数控制和资源冲突检测
    """

    # 进度历史上限，防止长时间任务内存无限增长
    # 例如 7200 条 ≈ 2 小时的逐秒进度数据
    PROGRESS_HISTORY_MAX = 7200

    def __init__(self, node_manager=None):
        self.redis = redis.Redis(**get_redis_connection_kwargs())
        self._progress: dict[str, ProgressUpdate] = {}
        self._progress_segments: dict[str, dict[str, ProgressUpdate]] = {}
        self._progress_history: dict[str, list[dict]] = {}
        self._node_manager = node_manager

    def _generate_task_id(self, db=None) -> str:
        """生成按日期递增的任务 ID，例如 task-20260704-001。使用 Redis INCR 保证原子性。"""
        date_str = time.strftime("%Y%m%d", time.localtime())
        prefix = f"task-{date_str}-"
        redis_key = f"jmeter:task_seq:{date_str}"

        try:
            seq = self.redis.incr(redis_key)
            if seq == 1:
                self.redis.expire(redis_key, 86400 * 2)
        except Exception:
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
            seq = max_seq + 1

        task_id = f"{prefix}{seq:03d}"
        return task_id

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
                    db_transition_task_status(
                        db, task["task_id"],
                        from_statuses=(TaskStatus.RUNNING,),
                        to_status=TaskStatus.FAILED,
                        end_time=now,
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
        remote_hosts: Optional[str] = None,
        csv_file: Optional[str] = None,
        csv_variable_names: Optional[str] = None,
        csv_delimiter: str = ",",
        csv_recycle: bool = True,
        csv_stop_on_eof: bool = False,
        csv_distribution: str = "replicate",
        enforce_single_agent_task: bool = True,
    ) -> str:
        if not target_agents:
            raise ValueError("至少需要指定一个目标 Agent")

        if timeout <= 0:
            raise ValueError("超时时间必须大于 0")

        csv_distribution = str(csv_distribution or "replicate").strip().lower()
        if csv_distribution not in {"replicate", "shard"}:
            raise ValueError("CSV 分发策略只支持 replicate 或 shard")
        if csv_distribution == "shard" and not csv_file:
            raise ValueError("启用 CSV 分片时必须选择 CSV 文件")

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

        # 创建时快照全局变量，并以 -Jkey=value 传给 JMeter。
        # 同名任务参数优先，确保单次压测可按需覆盖公共配置。
        global_jmeter_args = {
            key: value
            for key, value in get_vars_dict().items()
            if key not in _GLOBAL_VARIABLE_EXCLUDED_KEYS
        }
        jmeter_args = {**global_jmeter_args, **(jmeter_args or {})}

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
            "csv_distribution": csv_distribution,
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

    def _find_unavailable_target_agents(self, target_agents: list[str]) -> list[str]:
        if not self._node_manager:
            return []

        unavailable = []
        for agent_id in target_agents or []:
            try:
                agent = self._node_manager.get_agent(agent_id)
            except Exception:
                agent = None
            if not agent:
                unavailable.append(f"{agent_id} 未在线")
            elif getattr(agent, "status", None) != "online":
                unavailable.append(f"{agent_id} 状态为 {getattr(agent, 'status', 'unknown')}")
        return unavailable

    def _resolve_rerun_target_agents(self, target_agents: list[str]) -> list[str]:
        unavailable = self._find_unavailable_target_agents(target_agents)
        if not unavailable or not self._node_manager:
            return target_agents

        try:
            available_agents = self._node_manager.get_available_agents()
        except Exception:
            available_agents = []
        if available_agents:
            return [available_agents[0].agent_id]
        return target_agents

    def _fail_task_start(self, task_id: str, error_message: str):
        db = get_sync_db()
        try:
            db_transition_task_status(
                db, task_id,
                from_statuses=(TaskStatus.PENDING, TaskStatus.RUNNING),
                to_status=TaskStatus.FAILED,
                end_time=time.time(),
                error_message=error_message,
            )
        finally:
            db.close()

    def get_available_agent(self) -> Optional[str]:
        """返回一个可用的 Agent ID，无可用 Agent 时返回 None。

        公开方法，供 API 层调用，避免直接访问 _node_manager 私有属性。
        """
        if not self._node_manager:
            return None
        try:
            agents = self._node_manager.get_available_agents()
            if agents:
                return agents[0].agent_id
        except Exception:
            pass
        return None

    def get_agent_status_summary(self) -> dict:
        """返回 Agent 状态摘要，供 API 层使用。

        返回格式：
        {
            "available": int,   # 在线可用的 Agent 数量
            "busy": int,        # 正在执行任务的 Agent 数量
            "offline": int,     # 离线的 Agent 数量
            "agents": list      # 所有 Agent 信息列表
        }
        """
        if not self._node_manager:
            return {"available": 0, "busy": 0, "offline": 0}
        try:
            all_agents = self._node_manager.get_agents()
        except Exception:
            return {"available": 0, "busy": 0, "offline": 0}
        available = sum(1 for a in all_agents if a.status == "online")
        busy = sum(1 for a in all_agents if a.status == "busy")
        offline = sum(1 for a in all_agents if a.status not in ("online", "busy"))
        return {"available": available, "busy": busy, "offline": offline, "agents": all_agents}

    def _stream_publish(self, stream_key: str, payload: str) -> bool:
        """通过 Redis Stream 发送消息，带重试机制。

        替代原来的 redis.publish()，提供消息持久化能力。
        即使 Manager 重启，未消费的消息也不会丢失。

        Args:
            stream_key: Stream 频道名（如 jmeter:command）
            payload: JSON 序列化的消息内容

        Returns:
            发送成功返回 True，全部重试失败返回 False
        """
        for attempt in range(3):
            try:
                self.redis.xadd(
                    stream_key, {"data": payload},
                    maxlen=REDIS_STREAM_MAX_LEN,
                )
                return True
            except Exception as e:
                logger.warning("Redis XADD attempt %d failed for %s: %s", attempt + 1, stream_key, e)
                time.sleep(0.5 * (attempt + 1))
        logger.error("Redis XADD failed after 3 attempts for stream %s", stream_key)
        return False

    def _publish_command(self, command: TaskCommand) -> bool:
        """将命令投递到目标 Agent 的独立 Stream。"""
        if not command.target_agent_id:
            logger.error("Command %s has no target_agent_id", command.command_id)
            return False
        return self._stream_publish(
            get_agent_command_stream(command.target_agent_id),
            command.to_json(),
        )

    def start_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False

        if task["status"] not in (TaskStatus.PENDING,):
            return False

        unavailable_agents = self._find_unavailable_target_agents(task.get("target_agents") or [])
        if unavailable_agents:
            self._fail_task_start(
                task_id,
                "目标 Agent 不可用：" + "；".join(unavailable_agents),
            )
            return False

        self._progress.pop(task_id, None)
        self._progress_segments.pop(task_id, None)
        self._progress_history.pop(task_id, None)

        script_path = os.path.join(SCRIPTS_DIR, f"{task['script_id']}.jmx")
        if not os.path.exists(script_path):
            db = get_sync_db()
            try:
                db_transition_task_status(
                    db, task_id,
                    from_statuses=(TaskStatus.PENDING,),
                    to_status=TaskStatus.FAILED,
                    end_time=time.time(),
                    error_message=f"脚本文件不存在: {task['script_id']}.jmx",
                )
            finally:
                db.close()
            return False

        script_content = None
        script_artifact = None
        csv_artifact = None
        csv_artifacts_by_agent = {}
        csv_partitions = {}
        csv_legacy_path = task.get("csv_file")
        csv_delimiter = task.get("csv_delimiter", ",")
        csv_distribution = task.get("csv_distribution") or "replicate"
        try:
            with open(script_path, "rb") as f:
                script_bytes = f.read()
            script_artifact = get_artifact_store().put_bytes(
                kind="scripts",
                logical_id=str(task["script_id"]),
                filename=os.path.basename(script_path),
                content=script_bytes,
            ).to_dict()
            if ARTIFACT_INLINE_FALLBACK:
                script_content = script_bytes.decode("utf-8", errors="replace")

            if task.get("csv_file"):
                csv_artifacts_by_agent, csv_partitions, csv_meta = prepare_csv_distribution(
                    task["csv_file"],
                    task_id,
                    task["target_agents"],
                    csv_distribution,
                )
                csv_legacy_path = csv_meta.get("filepath") or task["csv_file"]
                csv_delimiter = csv_meta.get("delimiter") or csv_delimiter
        except Exception as exc:
            logger.exception("Failed to prepare task artifacts for task %s", task_id)
            db = get_sync_db()
            try:
                db_transition_task_status(
                    db, task_id,
                    from_statuses=(TaskStatus.PENDING,),
                    to_status=TaskStatus.FAILED,
                    end_time=time.time(),
                    error_message=f"任务制品准备失败: {exc}",
                )
            finally:
                db.close()
            return False

        start_time = time.time()
        db = get_sync_db()
        try:
            claimed = db_transition_task_status(
                db, task_id,
                from_statuses=(TaskStatus.PENDING,),
                to_status=TaskStatus.RUNNING,
                start_time=start_time,
            )
        finally:
            db.close()
        if not claimed:
            return False

        for agent_id in task["target_agents"]:
            csv_ref = csv_artifacts_by_agent.get(agent_id)
            csv_artifact = csv_ref.to_dict() if csv_ref else None
            command = TaskCommand(
                command=CommandType.EXECUTE,
                task_id=task_id,
                script_path=script_path,
                expires_at=time.time() + COMMAND_TTL_SECONDS,
                target_agent_id=agent_id,
                script_content=script_content,
                script_artifact=script_artifact,
                jmeter_args=task["jmeter_args"],
                timeout=task["timeout"],
                distributed=task.get("distributed", False),
                remote_hosts=task.get("remote_hosts"),
                csv_file=csv_legacy_path if csv_distribution == "replicate" else None,
                csv_artifact=csv_artifact,
                csv_variable_names=task.get("csv_variable_names"),
                csv_delimiter=csv_delimiter,
                csv_recycle=task.get("csv_recycle", True),
                csv_stop_on_eof=task.get("csv_stop_on_eof", False),
                csv_distribution=csv_distribution,
                csv_partition=csv_partitions.get(agent_id),
            )
            published = self._publish_command(command)
            if not published:
                logger.error("Failed to send command to agent %s for task %s", agent_id, task_id)
                db = get_sync_db()
                try:
                    db_transition_task_status(
                        db, task_id,
                        from_statuses=(TaskStatus.RUNNING,),
                        to_status=TaskStatus.FAILED,
                        end_time=time.time(),
                        error_message="Redis Stream 通信失败，无法下发任务命令",
                    )
                finally:
                    db.close()
                return False

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
                expires_at=time.time() + COMMAND_TTL_SECONDS,
                target_agent_id=agent_id,
            )
            self._publish_command(command)

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
            target_agents=self._resolve_rerun_target_agents(old_task["target_agents"]),
            jmeter_args=old_task["jmeter_args"],
            timeout=old_task.get("timeout", TASK_TIMEOUT),
            distributed=old_task.get("distributed", False),
            remote_hosts=old_task.get("remote_hosts"),
            csv_file=old_task.get("csv_file"),
            csv_variable_names=old_task.get("csv_variable_names"),
            csv_delimiter=old_task.get("csv_delimiter", ","),
            csv_recycle=old_task.get("csv_recycle", True),
            csv_stop_on_eof=old_task.get("csv_stop_on_eof", False),
            csv_distribution=old_task.get("csv_distribution", "replicate"),
        )
        if not self.start_task(new_task_id):
            return None
        return new_task_id

    def stop_and_rerun(self, task_id: str, overrides: Optional[dict] = None) -> Optional[str]:
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
        if not overrides or "target_agents" not in overrides:
            target_agents = self._resolve_rerun_target_agents(target_agents)

        new_task_id = self.create_task(
            script_id=task["script_id"],
            target_agents=target_agents,
            jmeter_args=jmeter_args,
            timeout=timeout,
            distributed=task.get("distributed", False),
            remote_hosts=task.get("remote_hosts"),
            csv_file=overrides.get("csv_file", task.get("csv_file")) if overrides else task.get("csv_file"),
            csv_variable_names=overrides.get("csv_variable_names", task.get("csv_variable_names")) if overrides else task.get("csv_variable_names"),
            csv_delimiter=overrides.get("csv_delimiter", task.get("csv_delimiter", ",")) if overrides else task.get("csv_delimiter", ","),
            csv_recycle=overrides.get("csv_recycle", task.get("csv_recycle", True)) if overrides else task.get("csv_recycle", True),
            csv_stop_on_eof=overrides.get("csv_stop_on_eof", task.get("csv_stop_on_eof", False)) if overrides else task.get("csv_stop_on_eof", False),
            csv_distribution=overrides.get("csv_distribution", task.get("csv_distribution", "replicate")) if overrides else task.get("csv_distribution", "replicate"),
            enforce_single_agent_task=False,
        )
        if not self.start_task(new_task_id):
            return None
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
                expires_at=time.time() + COMMAND_TTL_SECONDS,
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
            self._publish_command(command)

        return {
            "task_id": task_id,
            "action": action,
            "threads": threads,
            "ramp_time": ramp_time,
            "duration": duration,
            "segment_id": segment_id,
            "target_agents": target_agents,
        }

    def batch_create_tasks(self, tasks_config: list[dict]) -> list[dict]:
        results = []
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
                    csv_distribution=cfg.get("csv_distribution", "replicate"),
                )
                if cfg.get("auto_start", False):
                    self.start_task(task_id)
                results.append({"task_id": task_id, "error": None})
            except Exception as e:
                results.append({"task_id": None, "error": str(e)})
        return results

    def get_task(self, task_id: str) -> Optional[dict]:
        db = get_sync_db()
        try:
            return db_get_task(db, task_id)
        finally:
            db.close()

    def get_all_tasks(self, offset: Optional[int] = None, limit: Optional[int] = None, status: Optional[str] = None):
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
                has_failed = any(r["status"] == TaskStatus.FAILED for r in task_results.values())
                has_stopped = any(r["status"] == TaskStatus.STOPPED for r in task_results.values())
                if has_failed:
                    new_status = TaskStatus.FAILED
                elif has_stopped:
                    new_status = TaskStatus.STOPPED
                else:
                    new_status = TaskStatus.COMPLETED
                db_transition_task_status(
                    db, result.task_id,
                    from_statuses=(TaskStatus.RUNNING,),
                    to_status=new_status,
                    end_time=time.time(),
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
        if len(history) > self.PROGRESS_HISTORY_MAX:
            self._progress_history[aggregate.task_id] = history[-self.PROGRESS_HISTORY_MAX:]

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
