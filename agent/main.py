"""JMeter Agent 主程序。

本模块实现 JMeter 性能测试代理节点，负责接收 Manager 下发的测试任务，
执行 JMeter 脚本并将测试结果实时上报。

核心功能：
- 订阅 Redis 命令通道，接收任务执行/停止指令
- 通过 JMeterRunner 执行压测脚本
- 实时计算并上报测试进度（TPS、响应时间、错误率）
- 定时上报心跳和节点状态信息
- 支持分布式压测模式
- 完整的日志记录，便于问题排查
"""

import os
import sys
import time
import json
import signal
import socket
import threading
import psutil
import redis
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from common.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB,
    JMETER_HOME, SCRIPTS_DIR, REPORTS_DIR,
    REDIS_CHANNEL_COMMAND, REDIS_CHANNEL_RESULT,
    REDIS_CHANNEL_HEARTBEAT, REDIS_CHANNEL_PROGRESS,
    AGENT_HEARTBEAT_INTERVAL,
    AGENT_REDIS_RETRY_DELAY, AGENT_REDIS_MAX_RETRIES,
    REDIS_STREAM_MAX_LEN, REDIS_STREAM_READ_BLOCK_MS,
)
from common.protocol import (
    AgentInfo, TaskCommand, TaskResult, ProgressUpdate,
    CommandType, TaskStatus,
)
from common.logger import get_agent_logger, get_task_logger, log_task_event, log_error
from common.utils import fmt_pct
from jmeter_runner import JMeterRunner


class JMeterAgent:
    """JMeter 性能测试代理节点。

    负责连接 Redis 进行任务通信，执行 JMeter 压测脚本，
    并通过 Redis 发布/订阅机制上报测试结果和进度。
    """

    def __init__(self):
        """初始化 Agent 实例。生成唯一 ID、获取本机 IP、建立 Redis 连接、注册信号处理。"""
        self.agent_id = self._load_or_create_agent_id()
        self.host = self._get_local_ip()
        self.port = int(os.getenv("AGENT_PORT", 9999))

        self.logger = get_agent_logger()
        self.task_logger = get_task_logger()

        self.redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self.pubsub = self.redis.pubsub()

        self.runner = JMeterRunner(JMETER_HOME)
        self.current_task: TaskCommand = None
        self._task_lock = threading.RLock()
        self._task_thread = None
        self._base_task_completed = False
        self.adjust_runners = {}
        self._segment_results = {}

        self.info = AgentInfo(
            agent_id=self.agent_id,
            host=self.host,
            port=self.port,
            jmeter_home=JMETER_HOME,
        )

        self._running = True
        self._stopping_task_id = None
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _load_or_create_agent_id(self) -> str:
        """加载持久化的 Agent ID，不存在则生成新的并保存。

        Agent ID 持久化到 .agent_id 文件，确保：
        1. Agent 重启后保持相同身份，Manager 可追踪历史任务
        2. 避免每次重启生成新 UUID 导致节点列表混乱
        """
        agent_id_file = os.path.join(os.path.dirname(__file__), ".agent_id")
        try:
            if os.path.exists(agent_id_file):
                with open(agent_id_file, "r") as f:
                    saved = f.read().strip()
                if saved:
                    return saved
        except Exception:
            pass
        new_id = f"agent-{uuid.uuid4().hex[:8]}"
        try:
            with open(agent_id_file, "w") as f:
                f.write(new_id)
        except Exception:
            pass
        return new_id

    def _ensure_redis_connection(self):
        """确保 Redis 连接健康，断连时自动重连。

        重连策略：
        - 最多重试 AGENT_REDIS_MAX_RETRIES 次
        - 每次重试间隔 AGENT_REDIS_RETRY_DELAY 秒
        - 全部重试失败后返回 False，Agent 将停止运行

        为什么需要重连：
        Redis 短暂不可用（如重启、网络抖动）后会自动恢复，
        如果 Agent 不重连就会变成"僵尸节点"——在线但无法接收命令。
        """
        for attempt in range(AGENT_REDIS_MAX_RETRIES):
            try:
                self.redis.ping()
                return True
            except Exception:
                self.logger.warning("Redis 连接异常，%ds 后重试 (%d/%d)",
                                    AGENT_REDIS_RETRY_DELAY, attempt + 1, AGENT_REDIS_MAX_RETRIES)
                time.sleep(AGENT_REDIS_RETRY_DELAY)
        self.logger.error("Redis 连接失败，超过最大重试次数")
        return False

    def _ensure_stream_consumer_group(self):
        """确保 Redis Stream 消费者组存在。

        每个 Agent 使用自己的 agent_id 作为消费者组名称，
        这样 Manager 下发的命令会被路由到对应的 Agent。
        mkstream=True 表示 Stream 不存在时自动创建。
        BUSYGROUP 错误表示消费者组已存在，属于正常情况。
        """
        for stream_key in [REDIS_CHANNEL_COMMAND]:
            try:
                self.redis.xgroup_create(stream_key, self.agent_id, id="0", mkstream=True)
            except redis.exceptions.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    self.logger.warning("创建消费者组失败: %s", e)

    def start(self):
        """启动 Agent。订阅 Redis Stream 命令通道、注册到 Agent 列表并进入心跳循环。"""
        self.logger.info(f"Agent 启动: {self.host}:{self.port}")
        self.logger.info(f"JMeter 路径: {JMETER_HOME}")
        self.logger.info(f"脚本目录: {SCRIPTS_DIR}")
        self.logger.info(f"报告目录: {REPORTS_DIR}")

        self._ensure_stream_consumer_group()

        self.redis.sadd("jmeter:agents", self.agent_id)
        self.redis.hset(f"jmeter:agent:{self.agent_id}", mapping=self.info.to_dict())
        self.logger.info(f"Agent 已注册到 Redis，ID: {self.agent_id}")

        self._heartbeat_loop()

    def _on_command_message(self, data):
        """处理从 Redis Stream 收到的命令消息数据。

        支持的命令类型：
        - EXECUTE: 执行 JMeter 压测任务
        - STOP: 停止正在运行的任务
        - ADJUST_LOAD: 动态调整压力（加压/减压）
        """
        try:
            command = TaskCommand.from_json(data)
            if command.target_agent_id and command.target_agent_id != self.agent_id:
                return
            self.logger.debug(f"收到命令: {command.command} for task {command.task_id}")

            if command.command == CommandType.EXECUTE:
                self._handle_execute(command)
            elif command.command == CommandType.STOP:
                self._handle_stop(command)
            elif command.command == CommandType.ADJUST_LOAD:
                self._handle_adjust_load(command)
        except Exception as e:
            log_error(self.logger, e, "命令处理")

    def _handle_execute(self, command: TaskCommand):
        """处理任务执行指令。任务在线程中运行，避免阻塞后续调压/停止命令。"""
        with self._task_lock:
            if self.current_task or self.runner.is_running:
                self.logger.warning(f"Agent 忙碌，无法执行任务 {command.task_id}")
                self._send_result(TaskResult(
                    task_id=command.task_id,
                    agent_id=self.agent_id,
                    status=TaskStatus.FAILED,
                    error_message="Agent busy with another task",
                ))
                return

            self.current_task = command
            self._base_task_completed = False
            self._segment_results = {}
            self.adjust_runners = {}
            self.info.current_task_id = command.task_id
            self.info.status = "busy"
            self._update_info()

            self._task_thread = threading.Thread(
                target=self._run_execute,
                args=(command,),
                daemon=True,
            )
            self._task_thread.start()

    def _safe_int(self, value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _make_progress_callback(self, task_id: str, segment_id: str, threads: int, start_time: float):
        state = {
            "last_total": 0,
            "last_time": time.time(),
            "all_elapsed_times": [],
        }

        def on_progress(raw):
            now = time.time()
            elapsed = int(now - start_time)
            total = int(raw.get("total_samples", 0) or 0)
            errors = int(raw.get("error_count", 0) or 0)
            times = raw.get("elapsed_times", []) or []
            bytes_recv = int(raw.get("bytes_received", 0) or 0)

            interval = now - state["last_time"]
            interval_count = max(0, total - state["last_total"])
            current_tps = round(interval_count / interval, 2) if interval > 0 else 0

            if times:
                state["all_elapsed_times"].extend(times)
                if len(state["all_elapsed_times"]) > 5000:
                    state["all_elapsed_times"] = state["all_elapsed_times"][-5000:]

            elapsed_times = state["all_elapsed_times"]
            avg_rt = round(sum(elapsed_times) / len(elapsed_times), 2) if elapsed_times else 0
            error_rate = fmt_pct(errors / total * 100) if total > 0 else 0
            success_rate = fmt_pct((total - errors) / total * 100) if total > 0 else 100.0
            bytes_per_sec = round(bytes_recv / interval) if interval > 0 else 0

            state["last_total"] = total
            state["last_time"] = now

            update = ProgressUpdate(
                task_id=task_id,
                agent_id=self.agent_id,
                timestamp=time.time(),
                elapsed=elapsed,
                active_threads=max(0, int(threads or 0)),
                throughput=current_tps,
                avg_response_time=avg_rt,
                error_rate=error_rate,
                success_rate=success_rate,
                total_samples=total,
                bytes_received=bytes_recv,
                bytes_sent=bytes_per_sec,
                avg_latency=raw.get("avg_latency", 0),
                avg_connect_time=raw.get("avg_connect_time", 0),
                segment_id=segment_id,
            )
            self._stream_publish(REDIS_CHANNEL_PROGRESS, update.to_json())

        return on_progress

    def _send_final_progress(self, task_id: str, segment_id: str, summary: dict, start_time: float):
        total = int((summary or {}).get("total_samples", 0) or 0)
        errors = int((summary or {}).get("error_count", 0) or 0)
        error_rate = (summary or {}).get("error_rate")
        if error_rate in (None, ""):
            error_rate = fmt_pct(errors / total * 100) if total > 0 else 0
        success_rate = (summary or {}).get("success_rate")
        if success_rate in (None, ""):
            success_rate = fmt_pct((total - errors) / total * 100) if total > 0 else 100.0

        update = ProgressUpdate(
            task_id=task_id,
            agent_id=self.agent_id,
            timestamp=time.time(),
            elapsed=int(time.time() - start_time) if start_time else 0,
            active_threads=0,
            throughput=0,
            avg_response_time=float((summary or {}).get("avg_response_time", 0) or 0),
            error_rate=float(error_rate or 0),
            success_rate=float(success_rate or 100.0),
            total_samples=total,
            bytes_received=int((summary or {}).get("total_bytes_received", 0) or 0),
            bytes_sent=int((summary or {}).get("total_bytes_sent", 0) or 0),
            avg_latency=float((summary or {}).get("avg_latency", 0) or 0),
            avg_connect_time=float((summary or {}).get("avg_connect_time", 0) or 0),
            segment_id=segment_id,
        )
        self._stream_publish(REDIS_CHANNEL_PROGRESS, update.to_json())

    def _run_execute(self, command: TaskCommand):
        """执行基础任务，并在结束前等待动态压力段归并到同一个逻辑任务。"""
        start_time = time.time()
        result = {"status": "failed", "summary": {}, "error": None}
        try:
            log_task_event(self.task_logger, command.task_id, "开始执行",
                           {"agent": self.agent_id, "script": command.script_path})

            script_path = self._prepare_script(command)
            result_dir = os.path.join(REPORTS_DIR, command.task_id, self.agent_id)
            os.makedirs(result_dir, exist_ok=True)

            base_threads = self._safe_int((command.jmeter_args or {}).get("threads"), 0)
            result = self.runner.execute(
                script_path=script_path,
                result_dir=result_dir,
                jmeter_args=command.jmeter_args,
                on_progress=self._make_progress_callback(command.task_id, "base", base_threads, start_time),
                timeout=command.timeout,
                distributed=command.distributed,
                remote_hosts=command.remote_hosts,
                csv_file=command.csv_file,
                csv_variable_names=command.csv_variable_names,
                csv_delimiter=command.csv_delimiter,
                csv_recycle=command.csv_recycle,
                csv_stop_on_eof=command.csv_stop_on_eof,
            )

            self._send_final_progress(command.task_id, "base", result.get("summary", {}), start_time)
            with self._task_lock:
                self._base_task_completed = True

            self._wait_for_adjust_segments(command.task_id)

            if self._stopping_task_id == command.task_id:
                status = TaskStatus.STOPPED
            else:
                status = TaskStatus.COMPLETED if result.get("status") == "completed" else TaskStatus.FAILED

            summary = self._aggregate_segment_summaries(result.get("summary", {}))
            task_result = TaskResult(
                task_id=command.task_id,
                agent_id=self.agent_id,
                status=status,
                start_time=start_time,
                end_time=time.time(),
                report_path=result.get("report_path"),
                error_message=result.get("error"),
                summary=summary,
            )

            self._send_result(task_result)

            log_task_event(self.task_logger, command.task_id, "执行完成",
                           {"agent": self.agent_id, "status": task_result.status,
                            "samples": task_result.summary.get("total_samples", 0)})
        except Exception as e:
            log_error(self.logger, e, f"任务执行 {command.task_id}")
            self._send_result(TaskResult(
                task_id=command.task_id,
                agent_id=self.agent_id,
                status=TaskStatus.FAILED,
                start_time=start_time,
                end_time=time.time(),
                error_message=str(e),
                summary=result.get("summary", {}),
            ))
        finally:
            self._stop_adjust_segments(command.task_id)
            with self._task_lock:
                self.current_task = None
                self._base_task_completed = False
                self.adjust_runners = {}
                self._segment_results = {}
                self._task_thread = None
                if self._stopping_task_id == command.task_id:
                    self._stopping_task_id = None
                self.info.current_task_id = None
                self.info.status = "online"
                self._update_info()

    def _handle_adjust_load(self, command: TaskCommand):
        """处理动态调压指令。加压启动增量压力段，减压回收动态压力段。"""
        args = command.jmeter_args or {}
        action = str(args.get("action", "increase")).strip().lower()
        threads = self._safe_int(args.get("threads"), 0)
        ramp_time = max(0, self._safe_int(args.get("ramp_time"), 1))
        duration = self._safe_int(args.get("duration"), 60)

        if threads <= 0:
            self.logger.warning(f"动态调压参数无效: task={command.task_id}, threads={threads}")
            return

        with self._task_lock:
            current = self.current_task
            base_done = self._base_task_completed

        if not current or current.task_id != command.task_id:
            self.logger.warning(f"当前无可调压任务: {command.task_id}")
            return

        if action == "increase":
            if base_done:
                self.logger.warning(f"基础任务已结束，忽略动态加压: {command.task_id}")
                return
            if duration <= 0:
                self.logger.warning(f"动态加压持续时间无效: task={command.task_id}, duration={duration}")
                return
            self._start_adjust_segment(current, command, threads, ramp_time, duration)
        elif action == "decrease":
            self._decrease_load(command.task_id, threads)
        else:
            self.logger.warning(f"未知动态调压动作: {action}")

    def _start_adjust_segment(
        self,
        base_command: TaskCommand,
        adjust_command: TaskCommand,
        threads: int,
        ramp_time: int,
        duration: int,
    ):
        segment_id = (
            adjust_command.segment_id
            or (adjust_command.jmeter_args or {}).get("segment_id")
            or f"dyn-{uuid.uuid4().hex[:8]}"
        )
        runner = JMeterRunner(JMETER_HOME)
        segment_args = dict(base_command.jmeter_args or {})
        segment_args.update({
            "threads": str(threads),
            "ramp_time": str(ramp_time),
            "duration": str(duration),
        })

        thread = threading.Thread(
            target=self._run_adjust_segment,
            args=(segment_id, runner, base_command, segment_args, threads, duration + ramp_time + 60),
            daemon=True,
        )

        with self._task_lock:
            self.adjust_runners[segment_id] = {
                "runner": runner,
                "thread": thread,
                "threads": threads,
                "created_at": time.time(),
            }

        log_task_event(self.task_logger, base_command.task_id, "动态加压段启动",
                       {"agent": self.agent_id, "segment": segment_id,
                        "threads": threads, "ramp_time": ramp_time, "duration": duration})
        thread.start()

    def _run_adjust_segment(
        self,
        segment_id: str,
        runner: JMeterRunner,
        base_command: TaskCommand,
        jmeter_args: dict,
        threads: int,
        timeout: int,
    ):
        start_time = time.time()
        result = {"status": "failed", "summary": {}}
        try:
            base_script_path = self._prepare_script(base_command)
            safe_segment_id = "".join(
                ch if ch.isalnum() or ch in {"-", "_"} else "_"
                for ch in segment_id
            )
            script_path = os.path.join(SCRIPTS_DIR, f"{base_command.task_id}_{safe_segment_id}.jmx")
            import shutil
            shutil.copy2(base_script_path, script_path)

            result_dir = os.path.join(
                REPORTS_DIR,
                base_command.task_id,
                self.agent_id,
                "segments",
                segment_id,
            )
            os.makedirs(result_dir, exist_ok=True)

            result = runner.execute(
                script_path=script_path,
                result_dir=result_dir,
                jmeter_args=jmeter_args,
                on_progress=self._make_progress_callback(base_command.task_id, segment_id, threads, start_time),
                timeout=max(timeout, 120),
                distributed=base_command.distributed,
                remote_hosts=base_command.remote_hosts,
                csv_file=base_command.csv_file,
                csv_variable_names=base_command.csv_variable_names,
                csv_delimiter=base_command.csv_delimiter,
                csv_recycle=base_command.csv_recycle,
                csv_stop_on_eof=base_command.csv_stop_on_eof,
            )
            summary = result.get("summary", {}) or {}
            self._send_final_progress(base_command.task_id, segment_id, summary, start_time)
            with self._task_lock:
                self._segment_results[segment_id] = summary

            log_task_event(self.task_logger, base_command.task_id, "动态加压段结束",
                           {"agent": self.agent_id, "segment": segment_id,
                            "status": result.get("status"), "samples": summary.get("total_samples", 0)})
        except Exception as e:
            log_error(self.logger, e, f"动态加压段 {segment_id}")
            with self._task_lock:
                self._segment_results[segment_id] = result.get("summary", {}) or {}
        finally:
            with self._task_lock:
                self.adjust_runners.pop(segment_id, None)

    def _decrease_load(self, task_id: str, threads: int):
        with self._task_lock:
            candidates = sorted(
                self.adjust_runners.items(),
                key=lambda item: item[1].get("created_at", 0),
                reverse=True,
            )

        if not candidates:
            self.logger.warning(f"没有可回收的动态压力段: {task_id}")
            return

        remaining = threads
        stopped = []
        for segment_id, meta in candidates:
            if remaining <= 0:
                break
            runner = meta.get("runner")
            segment_threads = int(meta.get("threads", 0) or 0)
            if runner:
                runner.stop()
                stopped.append({"segment": segment_id, "threads": segment_threads})
                remaining -= max(segment_threads, 1)

        log_task_event(self.task_logger, task_id, "动态减压",
                       {"agent": self.agent_id, "requested_threads": threads,
                        "stopped_segments": stopped,
                        "unreduced_threads": max(0, remaining)})

    def _wait_for_adjust_segments(self, task_id: str):
        while True:
            with self._task_lock:
                segments = list(self.adjust_runners.items())
            if not segments:
                return
            for _, meta in segments:
                thread = meta.get("thread")
                if thread and thread.is_alive():
                    thread.join(timeout=0.5)

    def _stop_adjust_segments(self, task_id: str):
        with self._task_lock:
            runners = [(segment_id, meta.get("runner")) for segment_id, meta in self.adjust_runners.items()]
        for segment_id, runner in runners:
            if runner:
                self.logger.info(f"停止动态压力段: task={task_id}, segment={segment_id}")
                runner.stop()

    def _aggregate_segment_summaries(self, base_summary: dict) -> dict:
        base_summary = dict(base_summary or {})
        with self._task_lock:
            segment_summaries = {
                segment_id: dict(summary or {})
                for segment_id, summary in self._segment_results.items()
            }

        summaries = [base_summary] + [
            summary for summary in segment_summaries.values()
            if int(summary.get("total_samples", 0) or 0) > 0
        ]
        if len(summaries) <= 1:
            return base_summary

        total_samples = sum(int(s.get("total_samples", 0) or 0) for s in summaries)
        error_count = sum(int(s.get("error_count", 0) or 0) for s in summaries)
        bytes_received = sum(int(s.get("total_bytes_received", 0) or 0) for s in summaries)
        bytes_sent = sum(int(s.get("total_bytes_sent", 0) or 0) for s in summaries)

        def weighted_avg(key: str) -> float:
            if total_samples <= 0:
                return 0.0
            return round(
                sum(float(s.get(key, 0) or 0) * int(s.get("total_samples", 0) or 0) for s in summaries)
                / total_samples,
                2,
            )

        combined = dict(base_summary)
        combined.update({
            "total_samples": total_samples,
            "error_count": error_count,
            "success_count": max(0, total_samples - error_count),
            "error_rate": fmt_pct(error_count / total_samples * 100) if total_samples > 0 else 0,
            "success_rate": fmt_pct((total_samples - error_count) / total_samples * 100) if total_samples > 0 else 100.0,
            "avg_response_time": weighted_avg("avg_response_time"),
            "avg_latency": weighted_avg("avg_latency"),
            "avg_connect_time": weighted_avg("avg_connect_time"),
            "p50": weighted_avg("p50"),
            "p90": weighted_avg("p90"),
            "p95": weighted_avg("p95"),
            "p99": weighted_avg("p99"),
            "throughput": round(sum(float(s.get("throughput", 0) or 0) for s in summaries), 2),
            "total_bytes_received": bytes_received,
            "total_bytes_sent": bytes_sent,
            "avg_bytes_per_request": round(bytes_received / total_samples) if total_samples > 0 else 0,
            "dynamic_segments": segment_summaries,
        })

        min_values = [int(s.get("min_response_time", 0) or 0) for s in summaries if int(s.get("min_response_time", 0) or 0) > 0]
        max_values = [int(s.get("max_response_time", 0) or 0) for s in summaries]
        if min_values:
            combined["min_response_time"] = min(min_values)
        if max_values:
            combined["max_response_time"] = max(max_values)
        return combined

    def _handle_stop(self, command: TaskCommand):
        """处理任务停止指令。终止正在执行的 JMeter 进程。"""
        with self._task_lock:
            should_stop = self.current_task and self.current_task.task_id == command.task_id

        if should_stop:
            log_task_event(self.task_logger, command.task_id, "收到停止指令")
            self._stopping_task_id = command.task_id
            self.runner.stop()
            self._stop_adjust_segments(command.task_id)

    def _prepare_script(self, command: TaskCommand) -> str:
        """准备 JMX 脚本文件。根据指令写入脚本内容或复制外部脚本到执行目录。"""
        script_path = os.path.join(SCRIPTS_DIR, f"{command.task_id}.jmx")

        if command.script_content:
            os.makedirs(os.path.dirname(script_path), exist_ok=True)
            with open(script_path, "w") as f:
                f.write(command.script_content)
            self.logger.debug(f"脚本已写入: {script_path}")
        elif command.script_path and os.path.exists(command.script_path):
            import shutil
            os.makedirs(os.path.dirname(script_path), exist_ok=True)
            shutil.copy2(command.script_path, script_path)
            self.logger.debug(f"脚本已复制: {command.script_path} -> {script_path}")

        return script_path

    def _stream_publish(self, stream_key: str, payload: str):
        """通过 Redis Stream 发送消息，带重试机制。

        替代原来的 redis.publish()，提供消息持久化能力。
        用于上报任务结果、进度更新和心跳信息。

        Args:
            stream_key: Stream 频道名（如 jmeter:result、jmeter:progress）
            payload: JSON 序列化的消息内容
        """
        for attempt in range(3):
            try:
                self.redis.xadd(
                    stream_key, {"data": payload},
                    maxlen=REDIS_STREAM_MAX_LEN,
                )
                return
            except Exception as e:
                self.logger.warning("Redis XADD attempt %d failed: %s", attempt + 1, e)
                time.sleep(0.5 * (attempt + 1))
        self.logger.error("Redis XADD failed after 3 attempts for stream %s", stream_key)

    def _send_result(self, result: TaskResult):
        """发送测试结果。通过 Redis Stream 发送并存储到 Hash 中。"""
        self._stream_publish(REDIS_CHANNEL_RESULT, result.to_json())
        self.redis.hset(
            f"jmeter:task:{result.task_id}:result:{self.agent_id}",
            mapping={"data": result.to_json()},
        )
        self.logger.debug(f"结果已发送: task={result.task_id}, status={result.status}")

    def _heartbeat_loop(self):
        """心跳循环 - Agent 的主运行循环。

        同时承担两个职责：
        1. 心跳上报：定时向 Redis Stream 发送 CPU/内存使用率和状态信息
        2. 命令消费：从 Redis Stream 读取 Manager 下发的命令（执行/停止/调压）

        使用 XREADGROUP 消费者组模式读取命令，确保：
        - 命令持久化：Agent 重启后未处理的命令不会丢失
        - 可靠消费：处理完成后 XACK 确认
        - 断线重连：Redis 连接断开时自动重试

        心跳频率：每 AGENT_HEARTBEAT_INTERVAL 秒上报一次
        命令轮询：每 0.1 秒检查一次新命令
        """
        self.logger.info("心跳循环已启动")
        last_command_id = "0"
        poll_interval = 0.1

        while self._running:
            if not self._ensure_redis_connection():
                self.logger.error("Redis 不可用，Agent 将停止")
                break

            try:
                self.info.cpu_usage = psutil.cpu_percent(interval=None)
                self.info.memory_usage = psutil.virtual_memory().percent
                self.info.last_heartbeat = time.time()
                self._update_info()
            except Exception as e:
                self.logger.warning("心跳上报失败: %s", e)

            try:
                results = self.redis.xreadgroup(
                    self.agent_id, self.agent_id,
                    {REDIS_CHANNEL_COMMAND: ">"},
                    count=10,
                    block=int(poll_interval * 1000),
                )
                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        data = fields.get("data", "")
                        self._on_command_message(data)
                        self.redis.xack(REDIS_CHANNEL_COMMAND, self.agent_id, msg_id)
                        last_command_id = msg_id
            except redis.exceptions.ConnectionError:
                self.logger.warning("Redis Stream 读取连接断开")
                time.sleep(AGENT_REDIS_RETRY_DELAY)
            except Exception as e:
                self.logger.warning("Redis Stream 读取异常: %s", e)
                time.sleep(poll_interval)

        self.redis.srem("jmeter:agents", self.agent_id)
        self.redis.delete(f"jmeter:agent:{self.agent_id}")
        self.logger.info("Agent 已停止")

    def _update_info(self):
        """更新 Agent 状态信息到 Redis 并通过 Stream 发布心跳消息。"""
        self.redis.hset(f"jmeter:agent:{self.agent_id}", mapping=self.info.to_dict())
        self._stream_publish(REDIS_CHANNEL_HEARTBEAT, self.info.to_json())

    def _get_local_ip(self) -> str:
        """获取本机局域网 IP 地址。"""
        discovery_target = os.getenv("IP_DISCOVERY_TARGET")
        try:
            if discovery_target:
                discovery_port = int(os.getenv("IP_DISCOVERY_PORT", "80"))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect((discovery_target, discovery_port))
                    return s.getsockname()[0]

            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                candidate = info[4][0]
                if candidate and not candidate.startswith("127."):
                    return candidate
        except Exception:
            pass

        return "127.0.0.1"

    def _shutdown(self, signum, frame):
        """信号处理函数。捕获 SIGINT/SIGTERM 信号，优雅关闭 Agent。"""
        self.logger.info("收到关闭信号，正在停止...")
        self._running = False


if __name__ == "__main__":
    agent = JMeterAgent()
    agent.start()
