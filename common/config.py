"""
配置模块 - 管理平台的全局配置参数
从环境变量读取配置，提供默认值
"""
import os

# Redis 配置
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

# PostgreSQL 配置
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/perftest"
)

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

# JMeter 安装路径
JMETER_HOME = os.getenv(
    "JMETER_HOME",
    "/tmp/jmeter" if os.path.exists("/tmp/jmeter") else os.path.join(_PROJECT_ROOT, "apache-jmeter-5.6.3"),
)

# 脚本存储目录
SCRIPTS_DIR = os.getenv(
    "SCRIPTS_DIR",
    os.path.join(_PROJECT_ROOT, "scripts"),
)

# 报告输出目录
REPORTS_DIR = os.getenv(
    "REPORTS_DIR",
    os.path.join(_PROJECT_ROOT, "reports"),
)

# Agent 心跳间隔(秒)
AGENT_HEARTBEAT_INTERVAL = int(os.getenv("AGENT_HEARTBEAT_INTERVAL", 5))
# 任务超时时间(秒)
TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", 3600))
# 最大并发任务数
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", 3))

# Redis 频道配置
REDIS_CHANNEL_RESULT = "jmeter:result"       # 压测结果频道
REDIS_CHANNEL_HEARTBEAT = "jmeter:heartbeat"  # Agent 心跳频道
REDIS_CHANNEL_COMMAND = "jmeter:command"       # 命令下发频道
REDIS_CHANNEL_PROGRESS = "jmeter:progress"     # 进度更新频道

# JMeter Slave 配置
JMETER_SLAVE_HOME = os.getenv(
    "JMETER_SLAVE_HOME",
    "/tmp/jmeter-slave" if os.path.exists("/tmp/jmeter-slave") else os.path.join(_PROJECT_ROOT, "apache-jmeter-5.6.3-slave"),
)

# Slave 默认端口
SLAVE_PORT = int(os.getenv("SLAVE_PORT", 1100))

# SOLOX 移动端性能监控配置
SOLOX_HOST = os.getenv("SOLOX_HOST", "127.0.0.1")
SOLOX_PORT = int(os.getenv("SOLOX_PORT", 50001))
SOLOX_BASE_URL = f"http://{SOLOX_HOST}:{SOLOX_PORT}"
