"""JTL 文件导入对比 API 模块。

支持上传 JTL 结果文件，解析后与历史任务进行性能曲线对比，
可按接口维度筛选和对比，帮助判断调优效果。
"""

import sys
import os
import json
import time
import tempfile
import xml.etree.ElementTree as ET
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REPORTS_DIR
from manager.api.results import _build_time_series, _build_label_stats, _percentile

router = APIRouter(prefix="/api/jtl", tags=["jtl-compare"])


def _parse_jtl_file(file_content: str) -> dict:
    """解析 JTL 文件内容，支持 XML 和 CSV 格式。"""
    content = file_content.strip()

    # XML 格式
    if content.startswith("<?xml") or content.startswith("<testResults"):
        return _parse_xml_content(content)

    # CSV 格式
    return _parse_csv_content(content)


def _parse_xml_content(content: str) -> dict:
    """解析 XML 格式的 JTL 数据。"""
    samples = []
    try:
        import io
        tree = ET.parse(io.StringIO(content))
        root = tree.getroot()

        for elem in root:
            if elem.tag in ("httpSample", "sample"):
                attrs = elem.attrib
                response_header = ""
                request_header = ""
                sampler_data = ""

                for child in elem:
                    if child.tag == "responseHeader" and child.text:
                        response_header = child.text.strip()
                    elif child.tag == "requestHeader" and child.text:
                        request_header = child.text.strip()
                    elif child.tag == "queryString" and child.text:
                        sampler_data = child.text.strip()

                url_elem = elem.find("java.net.URL")
                url = url_elem.text if url_elem is not None else ""

                sample = {
                    "timestamp": int(attrs.get("ts", 0)),
                    "elapsed": int(attrs.get("t", 0)),
                    "label": attrs.get("lb", ""),
                    "response_code": attrs.get("rc", ""),
                    "success": attrs.get("s", "true") == "true",
                    "bytes": int(attrs.get("by", 0)),
                    "url": url,
                    "latency": int(attrs.get("lt", 0)),
                    "connect_time": int(attrs.get("ct", 0)),
                }
                samples.append(sample)
    except Exception:
        pass

    return _build_analysis(samples)


def _parse_csv_content(content: str) -> dict:
    """解析 CSV 格式的 JTL 数据。"""
    samples = []
    try:
        lines = content.split("\n")
        if not lines:
            return {"labels": [], "summary": {}, "time_series": {}}

        header = lines[0].strip().split(",")
        field_map = {name: i for i, name in enumerate(header)}

        for line in lines[1:]:
            parts = line.strip().split(",")
            if len(parts) < 7:
                continue

            try:
                sample = {
                    "timestamp": int(parts[field_map.get("timeStamp", 0)]),
                    "elapsed": int(parts[field_map.get("elapsed", 1)]),
                    "label": parts[field_map.get("label", 2)],
                    "response_code": parts[field_map.get("responseCode", 3)],
                    "success": parts[field_map.get("success", 7)] == "true" if "success" in field_map and field_map["success"] < len(parts) else False,
                    "bytes": int(parts[field_map.get("bytes", 9)]) if "bytes" in field_map and field_map["bytes"] < len(parts) and parts[field_map["bytes"]].isdigit() else 0,
                    "url": parts[field_map.get("URL", 13)] if "URL" in field_map and field_map["URL"] < len(parts) else "",
                    "latency": int(parts[field_map.get("Latency", 14)]) if "Latency" in field_map and field_map["Latency"] < len(parts) and parts[field_map["Latency"]].isdigit() else 0,
                    "connect_time": int(parts[field_map.get("Connect", 16)]) if "Connect" in field_map and field_map["Connect"] < len(parts) and parts[field_map["Connect"]].isdigit() else 0,
                }
                samples.append(sample)
            except (ValueError, IndexError):
                continue

    except Exception:
        pass

    return _build_analysis(samples)


def _build_analysis(samples: list) -> dict:
    """从采样数据构建完整分析结果。"""
    if not samples:
        return {"labels": [], "summary": {}, "time_series": {}, "label_stats": []}

    samples.sort(key=lambda x: x["timestamp"])

    # 按接口分组统计
    label_stats = {}
    for s in samples:
        label = s["label"]
        if label not in label_stats:
            label_stats[label] = {"count": 0, "errors": 0, "elapsed_times": [], "timestamps": [], "url": s.get("url", "")}
        label_stats[label]["count"] += 1
        label_stats[label]["elapsed_times"].append(s["elapsed"])
        label_stats[label]["timestamps"].append(s["timestamp"])
        if not label_stats[label]["url"] and s.get("url"):
            label_stats[label]["url"] = s["url"]
        if not s["success"]:
            label_stats[label]["errors"] += 1

    labels_result = []
    for label, data in label_stats.items():
        times = sorted(data["elapsed_times"])
        ts = data["timestamps"]
        duration = (max(ts) - min(ts)) / 1000 if len(ts) > 1 else 1
        tps = round(data["count"] / duration, 2) if duration > 0 else 0

        labels_result.append({
            "label": label,
            "url": data["url"],
            "count": data["count"],
            "errors": data["errors"],
            "error_rate": round(data["errors"] / data["count"] * 100, 2),
            "tps": tps,
            "avg": round(sum(times) / len(times), 2),
            "min": min(times),
            "max": max(times),
            "p50": _percentile(times, 50),
            "p90": _percentile(times, 90),
            "p99": _percentile(times, 99),
        })

    # 总体统计
    elapsed_times = sorted([s["elapsed"] for s in samples])
    error_count = sum(1 for s in samples if not s["success"])
    total = len(samples)
    ts = [s["timestamp"] for s in samples]
    duration = (max(ts) - min(ts)) / 1000 if len(ts) > 1 else 1

    summary = {
        "total_samples": total,
        "error_count": error_count,
        "error_rate": round(error_count / total * 100, 2) if total > 0 else 0,
        "avg_response_time": round(sum(elapsed_times) / len(elapsed_times), 2) if elapsed_times else 0,
        "min_response_time": min(elapsed_times) if elapsed_times else 0,
        "max_response_time": max(elapsed_times) if elapsed_times else 0,
        "p50": _percentile(elapsed_times, 50),
        "p90": _percentile(elapsed_times, 90),
        "p95": _percentile(elapsed_times, 95),
        "p99": _percentile(elapsed_times, 99),
        "tps": round(total / duration, 2),
        "duration": round(duration, 1),
    }

    # 时序数据
    time_series = _build_time_series(samples)

    return {
        "labels": labels_result,
        "summary": summary,
        "time_series": time_series,
    }


@router.post("/upload")
async def upload_jtl(file: UploadFile = File(...)):
    """上传 JTL 文件并解析。"""
    if not file.filename.endswith((".jtl", ".xml", ".csv")):
        raise HTTPException(status_code=400, detail="只支持 .jtl / .xml / .csv 文件")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk", errors="replace")

    result = _parse_jtl_file(text)
    result["filename"] = file.filename
    result["size"] = len(content)
    result["upload_time"] = time.time()

    return result


@router.post("/compare")
async def compare_jtl_files(files: list[UploadFile] = File(...)):
    """上传多个 JTL 文件进行对比。"""
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="至少需要 2 个文件进行对比")

    results = []
    for file in files:
        content = await file.read()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("gbk", errors="replace")

        result = _parse_jtl_file(text)
        result["filename"] = file.filename
        result["size"] = len(content)
        results.append(result)

    return {"files": results, "count": len(results)}
