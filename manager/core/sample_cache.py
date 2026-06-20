"""测试结果采样数据缓存模块。

提供 JMeter 测试结果（XML/JTL 格式）的解析和带 TTL 的内存缓存，
避免重复解析磁盘文件，提升报告查询性能。
"""

import sys
import os
import time
import threading
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REPORTS_DIR

_cache = {}
_cache_lock = threading.Lock()
_cache_ttl = 300


def get_cached_samples(task_id: str) -> list:
    """获取指定任务的采样数据（带缓存）。

    优先从内存缓存读取，缓存过期或未命中时从磁盘重新加载。
    """
    with _cache_lock:
        if task_id in _cache:
            entry = _cache[task_id]
            if time.time() - entry["time"] < _cache_ttl:
                return entry["samples"]

    samples = _load_samples(task_id)

    with _cache_lock:
        _cache[task_id] = {"samples": samples, "time": time.time()}

    _cleanup_cache()
    return samples


def _load_samples(task_id: str) -> list:
    """从磁盘加载指定任务的所有采样数据。

    遍历任务目录下各 Agent 的结果文件，解析 XML 和 JTL 格式。
    """
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.exists(task_path):
        return []

    all_samples = []
    for agent_dir in os.listdir(task_path):
        agent_path = os.path.join(task_path, agent_dir)
        if not os.path.isdir(agent_path):
            continue

        # 检查所有可能的结果文件
        for filename in os.listdir(agent_path):
            filepath = os.path.join(agent_path, filename)

            if filename.endswith(".xml"):
                samples = _parse_xml_result(filepath)
                all_samples.extend(samples)
            elif filename.endswith(".jtl"):
                # 检测文件实际格式
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        first_line = f.readline().strip()
                    if first_line.startswith("<?xml") or first_line.startswith("<testResults"):
                        samples = _parse_xml_result(filepath)
                    else:
                        samples = _parse_jtl_fast(filepath)
                    all_samples.extend(samples)
                except Exception:
                    pass

    all_samples.sort(key=lambda x: x["timestamp"])
    return all_samples


def _parse_xml_result(xml_path: str) -> list:
    """解析 JMeter XML 格式的测试结果文件。"""
    samples = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        for elem in root:
            if elem.tag in ("httpSample", "sample"):
                attrs = elem.attrib

                response_header = _get_element_text(elem, "responseHeader")
                request_header = _get_element_text(elem, "requestHeader")
                method = _get_element_text(elem, "method")
                query_string = _get_element_text(elem, "queryString")
                url_elem = elem.find("java.net.URL")
                url = url_elem.text if url_elem is not None else ""

                request_body = ""
                if method in ("POST", "PUT", "PATCH"):
                    request_body = query_string

                sample = {
                    "index": len(samples) + 1,
                    "timestamp": int(attrs.get("ts", 0)),
                    "elapsed": int(attrs.get("t", 0)),
                    "label": attrs.get("lb", ""),
                    "response_code": attrs.get("rc", ""),
                    "response_message": attrs.get("rm", ""),
                    "thread_name": attrs.get("tn", ""),
                    "success": attrs.get("s", "true") == "true",
                    "failure_message": "",
                    "bytes": int(attrs.get("by", 0)),
                    "sent_bytes": int(attrs.get("sby", 0)),
                    "url": url,
                    "latency": int(attrs.get("lt", 0)),
                    "connect_time": int(attrs.get("ct", 0)),
                    "sampler_data": request_body,
                    "response_data": "",
                    "request_headers": request_header,
                    "response_headers": response_header,
                }
                samples.append(sample)
    except Exception as e:
        pass

    return samples


def _get_element_text(parent, tag):
    """安全获取 XML 元素的文本内容。"""
    elem = parent.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    return ""


def _parse_jtl_fast(jtl_path: str) -> list:
    """快速解析 JMeter CSV/JTL 格式的测试结果文件。"""
    samples = []
    try:
        with open(jtl_path, "r", encoding="utf-8", errors="replace") as f:
            header_line = f.readline()
            if not header_line:
                return samples

            header = header_line.strip().split(",")
            field_map = {name: i for i, name in enumerate(header)}

            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 7:
                    continue

                try:
                    sample = {
                        "index": len(samples) + 1,
                        "timestamp": int(parts[field_map.get("timeStamp", 0)]),
                        "elapsed": int(parts[field_map.get("elapsed", 1)]),
                        "label": parts[field_map.get("label", 2)],
                        "response_code": parts[field_map.get("responseCode", 3)],
                        "response_message": _safe_get(parts, field_map, "responseMessage", ""),
                        "thread_name": _safe_get(parts, field_map, "threadName", ""),
                        "success": _safe_get(parts, field_map, "success", "true") == "true",
                        "failure_message": _safe_get(parts, field_map, "failureMessage", ""),
                        "bytes": _safe_int(parts, field_map, "bytes", 0),
                        "sent_bytes": _safe_int(parts, field_map, "sentBytes", 0),
                        "url": _safe_get(parts, field_map, "URL", ""),
                        "latency": _safe_int(parts, field_map, "Latency", 0),
                        "connect_time": _safe_int(parts, field_map, "Connect", 0),
                        "sampler_data": _safe_get(parts, field_map, "samplerData", ""),
                        "response_data": _safe_get(parts, field_map, "responseData", ""),
                        "request_headers": _safe_get(parts, field_map, "requestHeaders", ""),
                        "response_headers": _safe_get(parts, field_map, "responseHeaders", ""),
                    }
                    samples.append(sample)
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass

    return samples


def _safe_get(parts, field_map, key, default=""):
    """从 CSV 行中安全提取指定字段值。"""
    idx = field_map.get(key, -1)
    if idx >= 0 and idx < len(parts):
        return parts[idx]
    return default


def _safe_int(parts, field_map, key, default=0):
    """从 CSV 行中安全提取指定字段的整数值。"""
    val = _safe_get(parts, field_map, key, "")
    try:
        return int(val) if val.isdigit() else default
    except (ValueError, AttributeError):
        return default


def _cleanup_cache():
    """清理内存缓存中已过期的条目。"""
    now = time.time()
    expired = [k for k, v in _cache.items() if now - v["time"] > _cache_ttl]
    for k in expired:
        del _cache[k]


def invalidate_cache(task_id: str):
    """手动使指定任务的缓存失效。"""
    with _cache_lock:
        _cache.pop(task_id, None)
