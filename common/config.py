import os

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

JMETER_HOME = os.getenv(
    "JMETER_HOME",
    "/tmp/jmeter" if os.path.exists("/tmp/jmeter") else os.path.join(_PROJECT_ROOT, "apache-jmeter-5.6.3"),
)

SCRIPTS_DIR = os.getenv(
    "SCRIPTS_DIR",
    os.path.join(_PROJECT_ROOT, "scripts"),
)

REPORTS_DIR = os.getenv(
    "REPORTS_DIR",
    os.path.join(_PROJECT_ROOT, "reports"),
)

AGENT_HEARTBEAT_INTERVAL = int(os.getenv("AGENT_HEARTBEAT_INTERVAL", 5))
TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", 3600))
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", 3))

REDIS_CHANNEL_RESULT = "jmeter:result"
REDIS_CHANNEL_HEARTBEAT = "jmeter:heartbeat"
REDIS_CHANNEL_COMMAND = "jmeter:command"
REDIS_CHANNEL_PROGRESS = "jmeter:progress"

JMETER_SLAVE_HOME = os.getenv(
    "JMETER_SLAVE_HOME",
    "/tmp/jmeter-slave" if os.path.exists("/tmp/jmeter-slave") else os.path.join(_PROJECT_ROOT, "apache-jmeter-5.6.3-slave"),
)

SLAVE_PORT = int(os.getenv("SLAVE_PORT", 1100))
