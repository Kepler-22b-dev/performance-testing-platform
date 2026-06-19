from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import time
import uuid


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class CommandType(str, Enum):
    EXECUTE = "execute"
    STOP = "stop"
    HEARTBEAT = "heartbeat"


@dataclass
class AgentInfo:
    agent_id: str
    host: str
    port: int
    jmeter_home: str
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    status: str = "online"
    last_heartbeat: float = field(default_factory=time.time)
    current_task_id: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class TaskCommand:
    command: CommandType
    task_id: str
    script_path: str
    script_content: Optional[str] = None
    jmeter_args: dict = field(default_factory=dict)
    timeout: int = 3600
    distributed: bool = False
    remote_hosts: Optional[str] = None
    csv_file: Optional[str] = None
    csv_variable_names: Optional[str] = None
    csv_delimiter: str = ","
    csv_recycle: bool = True
    csv_stop_on_eof: bool = False

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str):
        return cls(**json.loads(data))


@dataclass
class TaskResult:
    task_id: str
    agent_id: str
    status: TaskStatus
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    report_path: Optional[str] = None
    error_message: Optional[str] = None
    summary: dict = field(default_factory=dict)

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str):
        return cls(**json.loads(data))


@dataclass
class ProgressUpdate:
    task_id: str
    agent_id: str
    timestamp: float
    elapsed: int = 0
    active_threads: int = 0
    throughput: float = 0.0
    avg_response_time: float = 0.0
    error_rate: float = 0.0
    total_samples: int = 0

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str):
        return cls(**json.loads(data))
