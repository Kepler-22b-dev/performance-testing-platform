"""测试结果采样数据缓存模块。

提供 JMeter 测试结果（XML/JTL 格式）的解析和带 TTL 的内存缓存，
避免重复解析磁盘文件，提升报告查询性能。
"""

import sys
import os
import time
import threading
import csv
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REPORTS_DIR

_cache = {}
_cache_lock = threading.Lock()
_cache_ttl = 300
_max_cache_size = 100


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
    for filepath, source in _iter_result_files(task_path):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline().strip()
            if first_line.startswith("<?xml") or first_line.startswith("<testResults"):
                samples = _parse_xml_result(filepath)
            else:
                samples = _parse_jtl_fast(filepath)
            for sample in samples:
                sample.update(source)
            all_samples.extend(samples)
        except Exception:
            pass

    # 加载错误响应数据并合并到对应的样本中，动态调压 segments 目录也会被纳入。
    _merge_error_response_data(all_samples, task_path)

    all_samples.sort(key=lambda x: x["timestamp"])
    for index, sample in enumerate(all_samples, start=1):
        sample["index"] = index
    return all_samples


def _iter_result_files(task_path: str):
    """递归扫描任务结果文件，包含动态调压产生的 segments 子目录。"""
    if not os.path.isdir(task_path):
        return

    for root, dirs, files in os.walk(task_path):
        dirs[:] = [d for d in dirs if d not in {"html-report", "__pycache__"}]
        for filename in files:
            lower = filename.lower()
            if not (
                lower in {"result.xml", "result.jtl"}
                or lower.endswith(".jtl")
                or lower.endswith(".xml")
            ):
                continue
            if lower in {"jmeter.log"}:
                continue

            filepath = os.path.join(root, filename)
            rel_parts = os.path.relpath(root, task_path).split(os.sep)
            source = {
                "source_agent": rel_parts[0] if rel_parts and rel_parts[0] != "." else "",
                "source_segment": "base",
            }
            if "segments" in rel_parts:
                seg_idx = rel_parts.index("segments")
                if seg_idx + 1 < len(rel_parts):
                    source["source_segment"] = rel_parts[seg_idx + 1]
            yield filepath, source


def _merge_error_response_data(samples: list, task_path: str):
    """将 error_responses.jsonl 中的响应体数据合并到对应的样本中。"""
    import json as json_mod

    # 建立 (timestamp, label) -> sample 的索引
    sample_index = {}
    for s in samples:
        key = (s["timestamp"], s["label"])
        sample_index[key] = s

    for root, dirs, files in os.walk(task_path):
        dirs[:] = [d for d in dirs if d not in {"html-report", "__pycache__"}]
        if "error_responses.jsonl" not in files:
            continue

        error_file = os.path.join(root, "error_responses.jsonl")
        try:
            with open(error_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json_mod.loads(line)
                        key = (entry.get("ts", 0), entry.get("label", ""))
                        sample = sample_index.get(key)
                        if sample:
                            sample["response_data"] = entry.get("responseData", "")
                    except Exception:
                        pass
        except Exception:
            pass


def _parse_xml_result(xml_path: str) -> list:
    """使用流式解析 JMeter XML 格式的测试结果文件，支持大文件。"""
    samples = []
    try:
        for event, elem in ET.iterparse(xml_path, events=("end",)):
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
                elem.clear()
    except ET.ParseError:
        samples = _parse_xml_regex(xml_path)
    except Exception:
        pass

    return samples


def _parse_xml_regex(xml_path: str) -> list:
    """使用正则表达式解析 JMeter XML 文件（当标准 XML 解析失败时的回退方案）。"""
    import re
    samples = []
    try:
        with open(xml_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        pattern = r'<(?:httpSample|sample)\s+([^>]+)>'
        for match in re.finditer(pattern, content):
            attrs_str = match.group(1)
            attrs = {}
            for attr_match in re.finditer(r'(\w+)="([^"]*)"', attrs_str):
                attrs[attr_match.group(1)] = attr_match.group(2)

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
                "url": "",
                "latency": int(attrs.get("lt", 0)),
                "connect_time": int(attrs.get("ct", 0)),
                "sampler_data": "",
                "response_data": "",
                "request_headers": "",
                "response_headers": "",
            }
            samples.append(sample)
    except Exception:
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
        with open(jtl_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return samples

            for row in reader:
                try:
                    sample = {
                        "index": len(samples) + 1,
                        "timestamp": _safe_row_int(row, "timeStamp", 0),
                        "elapsed": _safe_row_int(row, "elapsed", 0),
                        "label": _safe_row_get(row, "label", ""),
                        "response_code": _safe_row_get(row, "responseCode", ""),
                        "response_message": _safe_row_get(row, "responseMessage", ""),
                        "thread_name": _safe_row_get(row, "threadName", ""),
                        "success": _safe_row_get(row, "success", "true").strip().lower() == "true",
                        "failure_message": _safe_row_get(row, "failureMessage", ""),
                        "bytes": _safe_row_int(row, "bytes", 0),
                        "sent_bytes": _safe_row_int(row, "sentBytes", 0),
                        "url": _safe_row_get(row, "URL", ""),
                        "latency": _safe_row_int(row, "Latency", 0),
                        "connect_time": _safe_row_int(row, "Connect", 0),
                        "sampler_data": _safe_row_get(row, "samplerData", ""),
                        "response_data": _safe_row_get(row, "responseData", ""),
                        "request_headers": _safe_row_get(row, "requestHeaders", ""),
                        "response_headers": _safe_row_get(row, "responseHeaders", ""),
                    }
                    samples.append(sample)
                except (ValueError, IndexError, TypeError):
                    continue
    except Exception:
        pass

    return samples


def _safe_row_get(row: dict, key: str, default=""):
    value = row.get(key)
    if value is not None:
        return value
    lower_key = key.lower()
    for row_key, row_value in row.items():
        if str(row_key).lower() == lower_key:
            return row_value if row_value is not None else default
    return default


def _safe_row_int(row: dict, key: str, default=0):
    val = _safe_row_get(row, key, "")
    try:
        return int(float(val)) if str(val).strip() else default
    except (ValueError, AttributeError):
        return default


def _cleanup_cache():
    """清理内存缓存中已过期的条目，并限制总缓存大小。"""
    now = time.time()
    expired = [k for k, v in _cache.items() if now - v["time"] > _cache_ttl]
    for k in expired:
        del _cache[k]

    if len(_cache) > _max_cache_size:
        sorted_keys = sorted(_cache.keys(), key=lambda k: _cache[k]["time"])
        excess = len(_cache) - _max_cache_size
        for k in sorted_keys[:excess]:
            del _cache[k]


def invalidate_cache(task_id: str):
    """手动使指定任务的缓存失效。"""
    with _cache_lock:
        _cache.pop(task_id, None)
