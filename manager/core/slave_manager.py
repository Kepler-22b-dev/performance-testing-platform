import sys
import os
import subprocess
import signal
import socket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import JMETER_SLAVE_HOME, SLAVE_PORT

_slave_process = None


def get_slave_status() -> dict:
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
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", SLAVE_PORT))
        s.close()
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
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
