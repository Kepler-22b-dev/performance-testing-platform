"""
配置模块 - 管理平台的全局配置参数
从环境变量读取配置，提供默认值
"""
import os

# Redis 配置
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_USERNAME = os.getenv("REDIS_USERNAME") or None
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() in ("1", "true", "yes", "on")
REDIS_SSL_CERT_REQS = os.getenv("REDIS_SSL_CERT_REQS", "required")
REDIS_SOCKET_TIMEOUT = float(os.getenv("REDIS_SOCKET_TIMEOUT", 5))
REDIS_SOCKET_CONNECT_TIMEOUT = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", 5))

# PostgreSQL 配置
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://localhost:5432/perftest"
)


def get_redis_connection_kwargs(*, decode_responses: bool = True) -> dict:
    """返回同步和异步 Redis 客户端共用的安全连接参数。"""
    kwargs = {
        "host": REDIS_HOST,
        "port": REDIS_PORT,
        "db": REDIS_DB,
        "decode_responses": decode_responses,
        "socket_timeout": REDIS_SOCKET_TIMEOUT,
        "socket_connect_timeout": REDIS_SOCKET_CONNECT_TIMEOUT,
        "ssl": REDIS_SSL,
    }
    if REDIS_USERNAME:
        kwargs["username"] = REDIS_USERNAME
    if REDIS_PASSWORD:
        kwargs["password"] = REDIS_PASSWORD
    if REDIS_SSL:
        kwargs["ssl_cert_reqs"] = REDIS_SSL_CERT_REQS
    return kwargs

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

# 制品存储。filesystem 适合单机开发，s3 适合 MinIO/AWS S3 等分布式部署。
ARTIFACT_STORE_BACKEND = os.getenv("ARTIFACT_STORE_BACKEND", "filesystem").lower()
ARTIFACT_LOCAL_DIR = os.getenv(
    "ARTIFACT_LOCAL_DIR",
    os.path.join(_PROJECT_ROOT, "artifacts"),
)
ARTIFACT_INLINE_FALLBACK = os.getenv(
    "ARTIFACT_INLINE_FALLBACK",
    "true",
).lower() in ("1", "true", "yes", "on")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "performance-testing-platform")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY") or None
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY") or None
S3_SESSION_TOKEN = os.getenv("S3_SESSION_TOKEN") or None
S3_USE_SSL = os.getenv("S3_USE_SSL", "true").lower() in ("1", "true", "yes", "on")
CSV_MAX_UPLOAD_BYTES = int(os.getenv("CSV_MAX_UPLOAD_BYTES", 100 * 1024 * 1024))

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

# Redis Stream 配置（用于替代 PubSub，提供消息持久化和消费者组支持）
REDIS_STREAM_MAX_LEN = 10000       # Stream 最大消息数，超出自动裁剪
REDIS_STREAM_READ_BLOCK_MS = 1000  # Stream 读取阻塞时间(ms)，兼顾实时性和 CPU 占用
AGENT_REDIS_RETRY_DELAY = 3        # Agent Redis 重连延迟(秒)
AGENT_REDIS_MAX_RETRIES = 5        # Agent Redis 最大重试次数
COMMAND_TTL_SECONDS = int(os.getenv("COMMAND_TTL_SECONDS", 300))
COMMAND_CLAIM_IDLE_MS = int(os.getenv("COMMAND_CLAIM_IDLE_MS", 30000))
COMMAND_DEDUP_TTL_SECONDS = int(os.getenv("COMMAND_DEDUP_TTL_SECONDS", 604800))

# Web 安全配置。页面与 API 默认同源；额外前端域名需显式加入白名单。
CORS_ALLOWED_ORIGINS = [
    value.strip()
    for value in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if value.strip()
]


def get_agent_command_stream(agent_id: str) -> str:
    """返回目标 Agent 的独立命令 Stream，避免所有 Agent 扫描同一条命令。"""
    return f"{REDIS_CHANNEL_COMMAND}:{agent_id}"

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
