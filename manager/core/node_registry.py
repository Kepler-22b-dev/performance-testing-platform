"""节点注册与验证模块。"""

import sys
import os
import json
import socket
import subprocess
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import JMETER_HOME, SCRIPTS_DIR
from common.database import get_sync_db
from manager.core.db_sync import (
    db_get_all_nodes, db_get_node, db_create_node,
    db_update_node, db_delete_node,
)


def _load_nodes() -> list:
    db = get_sync_db()
    try:
        return db_get_all_nodes(db)
    finally:
        db.close()


def _save_node(node: dict):
    db = get_sync_db()
    try:
        existing = db_get_node(db, node["node_id"])
        if existing:
            db_update_node(db, node["node_id"],
                status=node.get("status"),
                last_check=node.get("last_check"),
            )
        else:
            db_create_node(db, node)
    finally:
        db.close()


def _sync_jmeter_config(nodes: list):
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
    return _load_nodes()


def get_node(node_id: str) -> Optional[dict]:
    nodes = _load_nodes()
    for n in nodes:
        if n["node_id"] == node_id:
            return n
    return None


def add_node(ip: str, port: int = 1100, name: str = "") -> dict:
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
    _save_node(node)
    return {"status": "added", "node": node}


def remove_node(node_id: str) -> dict:
    db = get_sync_db()
    try:
        db_delete_node(db, node_id)
    finally:
        db.close()
    return {"status": "removed", "node_id": node_id}


def verify_node(node_id: str) -> dict:
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

    node["status"] = "verified" if all_passed else "failed"
    node["last_check"] = time.time()
    _save_node(node)

    check_config = _check_remote_hosts(ip, port)
    results["checks"].append({"name": "Master配置", "passed": check_config["ok"], "detail": check_config["detail"]})

    all_passed = all(c["passed"] for c in results["checks"])
    results["overall"] = "verified" if all_passed else "failed"

    node["status"] = results["overall"]
    node["last_check"] = time.time()
    _save_node(node)

    nodes = _load_nodes()
    _sync_jmeter_config(nodes)

    return results


def _check_port(ip: str, port: int) -> dict:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            result = s.connect_ex((ip, port))
            if result == 0:
                return {"ok": True, "detail": f"{ip}:{port} 可达"}
            else:
                return {"ok": False, "detail": f"{ip}:{port} 不可达 (连接被拒绝)"}
    except socket.timeout:
        return {"ok": False, "detail": f"{ip}:{port} 连接超时"}
    except Exception as e:
        return {"ok": False, "detail": f"{ip}:{port} 连接失败: {str(e)}"}


def _check_ssl_config() -> dict:
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
    nodes = _load_nodes()
    results = []
    for node in nodes:
        result = verify_node(node["node_id"])
        results.append(result)
    return results


def get_verified_nodes() -> list:
    nodes = _load_nodes()
    return [n for n in nodes if n.get("status") == "verified"]
