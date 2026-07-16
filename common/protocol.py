"""
通信协议模块 - 定义 Manager 和 Agent 之间的数据结构
包括任务状态、命令类型、Agent信息、任务结果等

所有数据结构通过 JSON 序列化/反序列化进行网络传输。
from_json 方法支持向前兼容：新增字段不影响旧版本解析。
"""
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import time
import uuid

# 协议版本号，用于未来兼容性检查
PROTOCOL_VERSION = 1


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"      # 待执行
    RUNNING = "running"      # 运行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    STOPPED = "stopped"      # 已停止


class CommandType(str, Enum):
    """命令类型枚举"""
    EXECUTE = "execute"  # 执行任务
    STOP = "stop"        # 停止任务
    ADJUST_LOAD = "adjust_load"  # 动态调整运行中的压力
    HEARTBEAT = "heartbeat"  # 心跳


@dataclass
class AgentInfo:
    """
    Agent 节点信息
    包含 Agent 的基本配置和运行时状态
    """
    agent_id: str          # Agent 唯一标识
    host: str              # IP 地址
    port: int              # 端口号
    jmeter_home: str       # JMeter 安装路径
    cpu_usage: float = 0.0      # CPU 使用率
    memory_usage: float = 0.0   # 内存使用率
    status: str = "online"      # 节点状态: online/busy/offline
    last_heartbeat: float = field(default_factory=time.time)  # 最后心跳时间
    current_task_id: Optional[str] = None  # 当前执行的任务ID

    def to_dict(self):
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class TaskCommand:
    """
    任务命令 - Manager 发送给 Agent 的执行指令
    包含脚本内容、JMeter 参数、分布式配置等
    """
    command: CommandType          # 命令类型
    task_id: str                  # 任务ID
    script_path: str              # 脚本路径
    target_agent_id: Optional[str] = None  # 目标 Agent；为空表示广播兼容旧命令
    script_content: Optional[str] = None  # 脚本内容(可选)
    jmeter_args: dict = field(default_factory=dict)  # JMeter 参数
    timeout: int = 3600           # 超时时间(秒)
    distributed: bool = False     # 是否分布式模式
    remote_hosts: Optional[str] = None  # 远程主机列表
    csv_file: Optional[str] = None       # CSV 数据文件路径
    csv_variable_names: Optional[str] = None  # CSV 变量名
    csv_delimiter: str = ","      # CSV 分隔符
    csv_recycle: bool = True      # 是否循环读取 CSV
    csv_stop_on_eof: bool = False # CSV 读完是否停止
    segment_id: Optional[str] = None  # 动态调压压力段 ID

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str):
        """从 JSON 字符串反序列化为 TaskCommand 实例。

        支持向前兼容：JSON 中的未知字段会被忽略，不会导致解析失败。
        这样新版本 Agent 发送的额外字段不会影响旧版本 Manager 的解析。
        """
        loaded = json.loads(data)
        # 过滤掉当前类不识别的字段，实现向前兼容
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in loaded.items() if k in known}
        return cls(**filtered)


@dataclass
class TaskResult:
    """
    任务结果 - Agent 返回给 Manager 的执行结果
    包含执行状态、耗时、报告路径等
    """
    task_id: str                              # 任务ID
    agent_id: str                             # Agent ID
    status: TaskStatus                        # 执行状态
    start_time: Optional[float] = None        # 开始时间
    end_time: Optional[float] = None          # 结束时间
    report_path: Optional[str] = None         # 报告文件路径
    error_message: Optional[str] = None       # 错误信息
    summary: dict = field(default_factory=dict)  # 汇总数据

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str):
        """从 JSON 字符串反序列化为 TaskResult 实例（向前兼容）。"""
        loaded = json.loads(data)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in loaded.items() if k in known}
        return cls(**filtered)


@dataclass
class ProgressUpdate:
    """
    进度更新 - Agent 实时上报的执行进度
    包含 TPS、响应时间、错误率等实时指标
    """
    task_id: str                              # 任务ID
    agent_id: str                             # Agent ID
    timestamp: float                          # 时间戳
    elapsed: int = 0                          # 已运行时间(秒)
    active_threads: int = 0                   # 活跃线程数
    throughput: float = 0.0                   # 吞吐量(TPS)
    avg_response_time: float = 0.0            # 平均响应时间(ms)
    error_rate: float = 0.0                   # 错误率(%)
    success_rate: float = 100.0               # 成功率(%)
    total_samples: int = 0                    # 总样本数
    bytes_received: int = 0                   # 接收字节数
    bytes_sent: int = 0                       # 发送字节数
    avg_latency: float = 0.0                  # 平均延迟(ms)
    avg_connect_time: float = 0.0             # 平均连接时间(ms)
    segment_id: str = "base"                  # 压力段 ID，base 表示基础任务

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str):
        """从 JSON 字符串反序列化为 ProgressUpdate 实例（向前兼容）。"""
        loaded = json.loads(data)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in loaded.items() if k in known}
        return cls(**filtered)
