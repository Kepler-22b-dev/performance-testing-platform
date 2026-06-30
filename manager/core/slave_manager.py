"""JMeter Slave 进程管理模块。

提供本地 JMeter Slave（jmeter-server）进程的启停管理功能，
包括进程状态查询、启动、停止以及本地 IP 获取。
"""

import sys
import os
import subprocess
import signal
import socket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import JMETER_SLAVE_HOME, SLAVE_PORT

_slave_process = None


def get_slave_status() -> dict:
    """获取本地 JMeter Slave 进程的运行状态。

    先检查内部管理的子进程，再通过端口探测确认外部启动的进程。
    """
    global _slave_process

    if _slave_process and _slave_process.poll() is None:
        return {
            "status": "running",
            "port": SLAVE_PORT,
            "pid": _slave_process.pid,
            "jmeter_home": JMETER_SLAVE_HOME,
        }

    _slave_process = None

    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", SLAVE_PORT))
            if result == 0:
                return {
                    "status": "running",
                    "port": SLAVE_PORT,
                    "pid": None,
                    "jmeter_home": JMETER_SLAVE_HOME,
                }
    except Exception:
        pass

    return {
        "status": "stopped",
        "port": SLAVE_PORT,
        "pid": None,
        "jmeter_home": JMETER_SLAVE_HOME,
    }


def start_slave(port: int = None) -> dict:
    """启动本地 JMeter Slave 进程。

    如果 Slave 已在运行则返回 already_running，否则启动新的 jmeter-server 进程。
    """
    global _slave_process

    if _slave_process and _slave_process.poll() is None:
        return {"status": "already_running", "pid": _slave_process.pid}

    current_status = get_slave_status()
    if current_status["status"] == "running":
        return {"status": "already_running", "pid": current_status.get("pid")}

    target_port = port or SLAVE_PORT
    jmeter_server = os.path.join(JMETER_SLAVE_HOME, "bin", "jmeter-server")

    if not os.path.exists(jmeter_server):
        return {"status": "error", "message": f"jmeter-server not found at {jmeter_server}"}

    local_ip = _get_local_ip()

    try:
        _slave_process = subprocess.Popen(
            [
                jmeter_server,
                f"-Dserver_port={target_port}",
                "-Dserver.rmi.ssl.disable=true",
                f"-Djava.rmi.server.hostname={local_ip}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        import time
        time.sleep(2)

        if _slave_process.poll() is not None:
            stderr = _slave_process.stderr.read().decode()
            return {"status": "error", "message": stderr}

        return {
            "status": "started",
            "port": target_port,
            "pid": _slave_process.pid,
            "jmeter_home": JMETER_SLAVE_HOME,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


def stop_slave() -> dict:
    """停止本地 JMeter Slave 进程。

    先发送 SIGINT 信号优雅关闭，超时后强制终止。
    """
    global _slave_process

    if _slave_process and _slave_process.poll() is None:
        try:
            _slave_process.send_signal(signal.SIGINT)
            _slave_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _slave_process.kill()
            _slave_process.wait()
        except ProcessLookupError:
            pass
        finally:
            _slave_process = None
            return {"status": "stopped"}

    _slave_process = None
    return {"status": "already_stopped"}


def _get_local_ip() -> str:
    """获取本机局域网 IP 地址。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
