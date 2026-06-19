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
from jmeter_runner import JMeterRunner


class JMeterAgent:
    def __init__(self):
        self.agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        self.host = self._get_local_ip()
        self.port = int(os.getenv("AGENT_PORT", 9999))

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
        print(f"[{self.agent_id}] Starting agent on {self.host}:{self.port}")
        print(f"[{self.agent_id}] JMeter home: {JMETER_HOME}")
        print(f"[{self.agent_id}] Scripts dir: {SCRIPTS_DIR}")
        print(f"[{self.agent_id}] Reports dir: {REPORTS_DIR}")

        self.pubsub.subscribe(**{
            REDIS_CHANNEL_COMMAND: self._on_command,
        })

        self.redis.sadd("jmeter:agents", self.agent_id)
        self.redis.hset(f"jmeter:agent:{self.agent_id}", mapping=self.info.to_dict())

        self._heartbeat_loop()

    def _on_command(self, message):
        try:
            data = message["data"]
            command = TaskCommand.from_json(data)

            if command.command == CommandType.EXECUTE:
                self._handle_execute(command)
            elif command.command == CommandType.STOP:
                self._handle_stop(command)
        except Exception as e:
            print(f"[{self.agent_id}] Command error: {e}")

    def _handle_execute(self, command: TaskCommand):
        if self.runner.is_running:
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

        print(f"[{self.agent_id}] Executing task {command.task_id}")

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

        self.current_task = None
        self.info.current_task_id = None
        self.info.status = "online"
        self._update_info()

    def _handle_stop(self, command: TaskCommand):
        if self.current_task and self.current_task.task_id == command.task_id:
            self.runner.stop()
            self._send_result(TaskResult(
                task_id=command.task_id,
                agent_id=self.agent_id,
                status=TaskStatus.STOPPED,
            ))

    def _prepare_script(self, command: TaskCommand) -> str:
        script_path = os.path.join(SCRIPTS_DIR, f"{command.task_id}.jmx")

        if command.script_content:
            os.makedirs(os.path.dirname(script_path), exist_ok=True)
            with open(script_path, "w") as f:
                f.write(command.script_content)
        elif command.script_path and os.path.exists(command.script_path):
            import shutil
            os.makedirs(os.path.dirname(script_path), exist_ok=True)
            shutil.copy2(command.script_path, script_path)

        return script_path

    def _send_result(self, result: TaskResult):
        self.redis.publish(REDIS_CHANNEL_RESULT, result.to_json())
        self.redis.hset(
            f"jmeter:task:{result.task_id}:result:{self.agent_id}",
            mapping={"data": result.to_json()},
        )

    def _heartbeat_loop(self):
        thread = self.pubsub.run_in_thread(sleep_time=0.1)

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
        print(f"[{self.agent_id}] Agent stopped")

    def _update_info(self):
        self.redis.hset(f"jmeter:agent:{self.agent_id}", mapping=self.info.to_dict())
        self.redis.publish(REDIS_CHANNEL_HEARTBEAT, self.info.to_json())

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _shutdown(self, signum, frame):
        print(f"[{self.agent_id}] Shutting down...")
        self._running = False


if __name__ == "__main__":
    agent = JMeterAgent()
    agent.start()
