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
)
from common.protocol import (
    AgentInfo, TaskCommand, TaskResult, ProgressUpdate,
    CommandType, TaskStatus,
)
from common.logger import get_agent_logger, get_task_logger, log_task_event, log_error
from jmeter_runner import JMeterRunner


class JMeterAgent:
    """JMeter 性能测试代理节点。

    负责连接 Redis 进行任务通信，执行 JMeter 压测脚本，
    并通过 Redis 发布/订阅机制上报测试结果和进度。
    """

    def __init__(self):
        """初始化 Agent 实例。生成唯一 ID、获取本机 IP、建立 Redis 连接、注册信号处理。"""
        self.agent_id = f"agent-{uuid.uuid4().hex[:8]}"
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

        self.info = AgentInfo(
            agent_id=self.agent_id,
            host=self.host,
            port=self.port,
            jmeter_home=JMETER_HOME,
        )

        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def start(self):
        """启动 Agent。订阅 Redis 命令通道、注册到 Agent 列表并进入心跳循环。"""
        self.logger.info(f"Agent 启动: {self.host}:{self.port}")
        self.logger.info(f"JMeter 路径: {JMETER_HOME}")
        self.logger.info(f"脚本目录: {SCRIPTS_DIR}")
        self.logger.info(f"报告目录: {REPORTS_DIR}")

        self.pubsub.subscribe(**{
            REDIS_CHANNEL_COMMAND: self._on_command,
        })

        self.redis.sadd("jmeter:agents", self.agent_id)
        self.redis.hset(f"jmeter:agent:{self.agent_id}", mapping=self.info.to_dict())
        self.logger.info(f"Agent 已注册到 Redis，ID: {self.agent_id}")

        self._heartbeat_loop()

    def _on_command(self, message):
        """Redis 命令消息回调。解析消息并分发到对应的处理方法。"""
        try:
            data = message["data"]
            command = TaskCommand.from_json(data)
            self.logger.debug(f"收到命令: {command.command.value} for task {command.task_id}")

            if command.command == CommandType.EXECUTE:
                self._handle_execute(command)
            elif command.command == CommandType.STOP:
                self._handle_stop(command)
        except Exception as e:
            log_error(self.logger, e, "命令处理")

    def _handle_execute(self, command: TaskCommand):
        """处理任务执行指令。准备脚本、执行 JMeter 并实时上报进度。"""
        if self.runner.is_running:
            self.logger.warning(f"Agent 忙碌，无法执行任务 {command.task_id}")
            self._send_result(TaskResult(
                task_id=command.task_id,
                agent_id=self.agent_id,
                status=TaskStatus.FAILED,
                error_message="Agent busy with another task",
            ))
            return

        self.current_task = command
        self.info.current_task_id = command.task_id
        self.info.status = "busy"
        self._update_info()

        log_task_event(self.task_logger, command.task_id, "开始执行",
                       {"agent": self.agent_id, "script": command.script_path})

        script_path = self._prepare_script(command)
        result_dir = os.path.join(REPORTS_DIR, command.task_id, self.agent_id)
        os.makedirs(result_dir, exist_ok=True)

        last_total = 0
        last_time = time.time()

        def on_progress(raw):
            nonlocal last_total, last_time
            now = time.time()
            elapsed = int(now - start_time)
            total = raw.get("total_samples", 0)
            errors = raw.get("error_count", 0)
            times = raw.get("elapsed_times", [])

            interval = now - last_time
            interval_count = total - last_total if total >= last_total else total
            current_tps = round(interval_count / interval, 2) if interval > 0 else 0

            avg_rt = round(sum(times) / len(times), 2) if times else 0
            error_rate = round(errors / total * 100, 2) if total > 0 else 0

            last_total = total
            last_time = now

            update = ProgressUpdate(
                task_id=command.task_id,
                agent_id=self.agent_id,
                timestamp=time.time(),
                elapsed=elapsed,
                active_threads=command.jmeter_args.get("threads", 0),
                throughput=current_tps,
                avg_response_time=avg_rt,
                error_rate=error_rate,
                total_samples=total,
            )
            self.redis.publish(REDIS_CHANNEL_PROGRESS, update.to_json())

        start_time = time.time()
        result = self.runner.execute(
            script_path=script_path,
            result_dir=result_dir,
            jmeter_args=command.jmeter_args,
            on_progress=on_progress,
            timeout=command.timeout,
            distributed=command.distributed,
            remote_hosts=command.remote_hosts,
            csv_file=command.csv_file,
            csv_variable_names=command.csv_variable_names,
            csv_delimiter=command.csv_delimiter,
            csv_recycle=command.csv_recycle,
            csv_stop_on_eof=command.csv_stop_on_eof,
        )

        task_result = TaskResult(
            task_id=command.task_id,
            agent_id=self.agent_id,
            status=TaskStatus.COMPLETED if result["status"] == "completed" else TaskStatus.FAILED,
            start_time=start_time,
            end_time=time.time(),
            report_path=result.get("report_path"),
            error_message=result.get("error"),
            summary=result.get("summary", {}),
        )

        self._send_result(task_result)

        log_task_event(self.task_logger, command.task_id, "执行完成",
                       {"agent": self.agent_id, "status": task_result.status,
                        "samples": task_result.summary.get("total_samples", 0)})

        self.current_task = None
        self.info.current_task_id = None
        self.info.status = "online"
        self._update_info()

    def _handle_stop(self, command: TaskCommand):
        """处理任务停止指令。终止正在执行的 JMeter 进程。"""
        if self.current_task and self.current_task.task_id == command.task_id:
            log_task_event(self.task_logger, command.task_id, "收到停止指令")
            self.runner.stop()
            self._send_result(TaskResult(
                task_id=command.task_id,
                agent_id=self.agent_id,
                status=TaskStatus.STOPPED,
            ))

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

    def _send_result(self, result: TaskResult):
        """发送测试结果。通过 Redis 发布并存储到 Hash 中。"""
        self.redis.publish(REDIS_CHANNEL_RESULT, result.to_json())
        self.redis.hset(
            f"jmeter:task:{result.task_id}:result:{self.agent_id}",
            mapping={"data": result.to_json()},
        )
        self.logger.debug(f"结果已发送: task={result.task_id}, status={result.status}")

    def _heartbeat_loop(self):
        """心跳循环。周期性上报 CPU/内存使用率和 Agent 状态，直到收到关闭信号。"""
        thread = self.pubsub.run_in_thread(sleep_time=0.1)
        self.logger.info("心跳循环已启动")

        while self._running:
            self.info.cpu_usage = psutil.cpu_percent(interval=None)
            self.info.memory_usage = psutil.virtual_memory().percent
            self.info.last_heartbeat = time.time()
            self._update_info()

            self.redis.publish(REDIS_CHANNEL_HEARTBEAT, self.info.to_json())

            for _ in range(AGENT_HEARTBEAT_INTERVAL * 10):
                if not self._running:
                    break
                time.sleep(0.1)

        thread.stop()
        self.redis.srem("jmeter:agents", self.agent_id)
        self.redis.delete(f"jmeter:agent:{self.agent_id}")
        self.logger.info("Agent 已停止")

    def _update_info(self):
        """更新 Agent 状态信息到 Redis 并发布心跳消息。"""
        self.redis.hset(f"jmeter:agent:{self.agent_id}", mapping=self.info.to_dict())
        self.redis.publish(REDIS_CHANNEL_HEARTBEAT, self.info.to_json())

    def _get_local_ip(self) -> str:
        """获取本机局域网 IP 地址。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _shutdown(self, signum, frame):
        """信号处理函数。捕获 SIGINT/SIGTERM 信号，优雅关闭 Agent。"""
        self.logger.info("收到关闭信号，正在停止...")
        self._running = False


if __name__ == "__main__":
    agent = JMeterAgent()
    agent.start()
