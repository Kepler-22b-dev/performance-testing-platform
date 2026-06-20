"""节点注册与验证模块。

提供 JMeter Slave 节点的持久化管理功能，包括节点的增删查改、
连接验证（端口可达性、SSL 配置、Master 配置同步）以及
JMeter remote_hosts 配置文件的自动同步。
"""

import sys
import os
import json
import socket
import subprocess
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import JMETER_HOME, SCRIPTS_DIR

NODES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config", "nodes.json",
)


def _ensure_config_dir():
    """确保配置文件所在目录存在。"""
    os.makedirs(os.path.dirname(NODES_FILE), exist_ok=True)


def _load_nodes() -> list:
    """从 JSON 文件加载节点列表。"""
    if not os.path.exists(NODES_FILE):
        return []
    with open(NODES_FILE, "r") as f:
        return json.load(f)


def _save_nodes(nodes: list):
    """将节点列表持久化到 JSON 文件并同步 JMeter 配置。"""
    _ensure_config_dir()
    with open(NODES_FILE, "w") as f:
        json.dump(nodes, f, indent=2, ensure_ascii=False)
    _sync_jmeter_config(nodes)


def _sync_jmeter_config(nodes: list):
    """将已验证节点同步到 jmeter.properties 的 remote_hosts 配置。"""
    jmeter_props = os.path.join(JMETER_HOME, "bin", "jmeter.properties")
    if not os.path.exists(jmeter_props):
        return

    verified_hosts = [f"{n['ip']}:{n['port']}" for n in nodes if n.get("status") == "verified"]
    remote_line = f"remote_hosts={','.join(verified_hosts)}" if verified_hosts else "remote_hosts="

    with open(jmeter_props, "r") as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.startswith("remote_hosts="):
            new_lines.append(remote_line + "\n")
        else:
            new_lines.append(line)

    with open(jmeter_props, "w") as f:
        f.writelines(new_lines)


def get_all_nodes() -> list:
    """获取所有已注册的节点列表。"""
    return _load_nodes()


def get_node(node_id: str) -> Optional[dict]:
    """根据节点 ID 获取单个节点信息。"""
    nodes = _load_nodes()
    for n in nodes:
        if n["node_id"] == node_id:
            return n
    return None


def add_node(ip: str, port: int = 1100, name: str = "") -> dict:
    """添加一个新的 JMeter Slave 节点。"""
    nodes = _load_nodes()

    for n in nodes:
        if n["ip"] == ip and n["port"] == port:
            return {"status": "error", "message": f"节点已存在: {ip}:{port}"}

    node_id = f"slave-{ip.replace('.', '-')}-{port}"
    node = {
        "node_id": node_id,
        "ip": ip,
        "port": port,
        "name": name or f"{ip}:{port}",
        "status": "pending",
        "last_check": None,
        "created_at": time.time(),
    }

    nodes.append(node)
    _save_nodes(nodes)

    return {"status": "added", "node": node}


def remove_node(node_id: str) -> dict:
    """删除指定节点。"""
    nodes = _load_nodes()
    original_len = len(nodes)
    nodes = [n for n in nodes if n["node_id"] != node_id]

    if len(nodes) == original_len:
        return {"status": "error", "message": f"节点不存在: {node_id}"}

    _save_nodes(nodes)
    return {"status": "removed", "node_id": node_id}


def verify_node(node_id: str) -> dict:
    """验证指定节点的连接性、SSL 配置和 Master 配置。"""
    node = get_node(node_id)
    if not node:
        return {"status": "error", "message": f"节点不存在: {node_id}"}

    ip = node["ip"]
    port = node["port"]
    results = {"ip": ip, "port": port, "checks": []}

    check_port = _check_port(ip, port)
    results["checks"].append({"name": "端口可达", "passed": check_port["ok"], "detail": check_port["detail"]})

    check_ssl = _check_ssl_config()
    results["checks"].append({"name": "SSL配置", "passed": check_ssl["ok"], "detail": check_ssl["detail"]})

    all_passed = check_port["ok"] and check_ssl["ok"]

    nodes = _load_nodes()
    for n in nodes:
        if n["node_id"] == node_id:
            n["status"] = "verified" if all_passed else "failed"
            n["last_check"] = time.time()
            break
    _save_nodes(nodes)

    check_config = _check_remote_hosts(ip, port)
    results["checks"].append({"name": "Master配置", "passed": check_config["ok"], "detail": check_config["detail"]})

    all_passed = all(c["passed"] for c in results["checks"])
    results["overall"] = "verified" if all_passed else "failed"

    for n in nodes:
        if n["node_id"] == node_id:
            n["status"] = results["overall"]
            n["last_check"] = time.time()
            break
    _save_nodes(nodes)

    return results


def _check_port(ip: str, port: int) -> dict:
    """检测指定 IP 和端口的 TCP 连接可达性。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        result = s.connect_ex((ip, port))
        s.close()
        if result == 0:
            return {"ok": True, "detail": f"{ip}:{port} 可达"}
        else:
            return {"ok": False, "detail": f"{ip}:{port} 不可达 (连接被拒绝)"}
    except socket.timeout:
        return {"ok": False, "detail": f"{ip}:{port} 连接超时"}
    except Exception as e:
        return {"ok": False, "detail": f"{ip}:{port} 连接失败: {str(e)}"}


def _check_ssl_config() -> dict:
    """检查 jmeter.properties 中 SSL 是否已正确禁用。"""
    jmeter_props = os.path.join(JMETER_HOME, "bin", "jmeter.properties")
    if not os.path.exists(jmeter_props):
        return {"ok": False, "detail": "jmeter.properties 不存在"}

    with open(jmeter_props, "r") as f:
        content = f.read()

    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("server.rmi.ssl.disable=") and not line.startswith("#"):
            value = line.split("=", 1)[1].strip()
            if value.lower() == "true":
                return {"ok": True, "detail": "SSL 已禁用"}
            else:
                return {"ok": False, "detail": "SSL 未禁用，可能导致连接失败"}

    return {"ok": True, "detail": "SSL 默认禁用"}


def _check_remote_hosts(ip: str, port: int) -> dict:
    """检查指定节点是否已配置在 jmeter.properties 的 remote_hosts 中。"""
    jmeter_props = os.path.join(JMETER_HOME, "bin", "jmeter.properties")
    if not os.path.exists(jmeter_props):
        return {"ok": False, "detail": "jmeter.properties 不存在"}

    with open(jmeter_props, "r") as f:
        content = f.read()

    target = f"{ip}:{port}"
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("remote_hosts=") and not line.startswith("#"):
            hosts = line.split("=", 1)[1].strip()
            if target in hosts.split(","):
                return {"ok": True, "detail": f"已配置在 remote_hosts 中"}
            else:
                return {"ok": False, "detail": f"未在 remote_hosts 中，当前: {hosts}"}

    return {"ok": False, "detail": "remote_hosts 未配置"}


def verify_all() -> list:
    """批量验证所有已注册节点。"""
    nodes = _load_nodes()
    results = []
    for node in nodes:
        result = verify_node(node["node_id"])
        results.append(result)
    return results


def get_verified_nodes() -> list:
    """获取所有验证通过的节点列表。"""
    nodes = _load_nodes()
    return [n for n in nodes if n.get("status") == "verified"]
