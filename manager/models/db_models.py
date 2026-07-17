"""
ORM 模型 - PostgreSQL 数据库表定义
"""
import time
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, JSON, Index,
    ForeignKey, UniqueConstraint,
)
from common.database import Base


class Script(Base):
    """脚本表 - 存储 JMeter JMX 脚本"""
    __tablename__ = "scripts"

    script_id = Column(String(64), primary_key=True)
    original_name = Column(String(255), nullable=False)
    filename = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    size = Column(Integer, default=0)
    created_at = Column(Float, default=time.time)
    modified_at = Column(Float, default=time.time)


class Task(Base):
    """任务表 - 存储压测任务"""
    __tablename__ = "tasks"

    task_id = Column(String(64), primary_key=True)
    script_id = Column(String(64), nullable=False)
    target_agents = Column(JSON, default=list)
    jmeter_args = Column(JSON, default=dict)
    timeout = Column(Integer, default=3600)
    distributed = Column(Boolean, default=False)
    remote_hosts = Column(String(512), nullable=True)
    csv_file = Column(String(512), nullable=True)
    csv_variable_names = Column(String(512), nullable=True)
    csv_delimiter = Column(String(8), default=",")
    csv_recycle = Column(Boolean, default=True)
    csv_stop_on_eof = Column(Boolean, default=False)
    csv_distribution = Column(String(16), default="replicate")
    status = Column(String(20), default="pending")
    created_at = Column(Float, default=time.time)
    start_time = Column(Float, nullable=True)
    end_time = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_created_at", "created_at"),
    )


class TaskResult(Base):
    """任务结果表 - 存储每个 Agent 的执行结果"""
    __tablename__ = "task_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        String(64),
        ForeignKey(
            "tasks.task_id",
            name="fk_task_results_task_id_tasks",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    agent_id = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False)
    start_time = Column(Float, nullable=True)
    end_time = Column(Float, nullable=True)
    report_path = Column(String(512), nullable=True)
    error_message = Column(Text, nullable=True)
    summary = Column(JSON, default=dict)

    __table_args__ = (
        Index("idx_task_results_task_id", "task_id"),
        UniqueConstraint("task_id", "agent_id", name="uq_task_results_task_agent"),
    )


class Variable(Base):
    """变量表 - 存储全局变量"""
    __tablename__ = "variables"

    id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    value = Column(Text, default="")
    description = Column(Text, default="")
    scope = Column(String(32), default="global")
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time)


class CsvFile(Base):
    """CSV 文件表 - 存储 CSV 元数据"""
    __tablename__ = "csv_files"

    csv_id = Column(String(64), primary_key=True)
    filename = Column(String(255), nullable=False)
    filepath = Column(String(512), nullable=False)
    artifact_id = Column(String(64), nullable=True)
    artifact_version = Column(String(64), nullable=True)
    storage_key = Column(String(1024), nullable=True)
    sha256 = Column(String(64), nullable=True)
    encoding = Column(String(32), nullable=True)
    delimiter = Column(String(8), nullable=True)
    headers = Column(JSON, default=list)
    row_count = Column(Integer, default=0)
    preview = Column(JSON, default=list)
    size = Column(Integer, default=0)
    created_at = Column(Float, default=time.time)


class Template(Base):
    """模板表 - 存储自定义压测模板"""
    __tablename__ = "templates"

    template_id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    category = Column(String(64), default="自定义")
    config = Column(JSON, default=dict)
    builtin = Column(Boolean, default=False)
    created_at = Column(Float, nullable=True)


class Environment(Base):
    """环境表 - 存储测试环境配置"""
    __tablename__ = "environments"

    env_id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    base_url = Column(String(512), nullable=False)
    description = Column(Text, default="")
    variables = Column(JSON, default=dict)
    headers = Column(JSON, default=dict)
    auth_token = Column(String(512), nullable=True)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time)


class AlertRule(Base):
    """告警规则表"""
    __tablename__ = "alert_rules"

    rule_id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    metric = Column(String(64), default="avg_response_time")
    operator = Column(String(8), default=">")
    threshold = Column(Float, default=1000)
    enabled = Column(Boolean, default=True)
    notify_webhook = Column(Boolean, default=True)
    description = Column(Text, default="")
    created_at = Column(Float, default=time.time)
    triggered_count = Column(Integer, default=0)
    last_triggered = Column(Float, nullable=True)


class Schedule(Base):
    """调度表 - 存储定时调度配置"""
    __tablename__ = "schedules"

    schedule_id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    script_id = Column(String(64), nullable=False)
    target_agents = Column(JSON, default=list)
    jmeter_args = Column(JSON, default=dict)
    timeout = Column(Integer, default=3600)
    distributed = Column(Boolean, default=False)
    remote_hosts = Column(String(512), nullable=True)
    cron_expr = Column(String(128), default="")
    interval_seconds = Column(Integer, default=0)
    run_at = Column(Float, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(Float, default=time.time)
    last_run = Column(Float, nullable=True)
    next_run = Column(Float, nullable=True)
    run_count = Column(Integer, default=0)


class Notification(Base):
    """通知配置表"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enabled = Column(Boolean, default=True)
    webhooks = Column(JSON, default=list)


class NodeRegistry(Base):
    """节点注册表 - 存储 JMeter Slave 节点"""
    __tablename__ = "node_registry"

    node_id = Column(String(128), primary_key=True)
    ip = Column(String(64), nullable=False)
    port = Column(Integer, default=1100)
    name = Column(String(255), default="")
    status = Column(String(32), default="pending")
    last_check = Column(Float, nullable=True)
    created_at = Column(Float, default=time.time)


class ScriptCounter(Base):
    """脚本 ID 计数器"""
    __tablename__ = "script_counter"

    id = Column(Integer, primary_key=True, default=1)
    counter = Column(Integer, default=0)
