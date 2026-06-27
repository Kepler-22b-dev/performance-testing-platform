"""测试结果分析与报告 API 模块。

提供压测结果的查询、汇总统计、时序分析、响应时间分布、标签统计、
错误分析、多任务对比、HTML/PDF 报告导出以及性能趋势追踪等接口。
"""

import sys
import os
import json
from fastapi import APIRouter, HTTPException
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import REPORTS_DIR, SCRIPTS_DIR

router = APIRouter(prefix="/api/results", tags=["results"])


def _build_summary_from_samples(samples: list) -> dict:
    """从样本列表构建统计摘要。"""
    if not samples:
        return {}
    elapsed_times = sorted([s["elapsed"] for s in samples])
    error_count = sum(1 for s in samples if not s["success"])
    total = len(samples)
    return {
        "total_samples": total,
        "error_count": error_count,
        "error_rate": round(error_count / total * 100, 2) if total > 0 else 0,
        "avg_response_time": round(sum(elapsed_times) / len(elapsed_times), 2),
        "min_response_time": min(elapsed_times),
        "max_response_time": max(elapsed_times),
        "p50": _percentile(elapsed_times, 50),
        "p90": _percentile(elapsed_times, 90),
        "p95": _percentile(elapsed_times, 95),
        "p99": _percentile(elapsed_times, 99),
    }


def _percentile(data: list, p: int) -> int:
    """计算数据列表的第 p 百分位值。"""
    if not data:
        return 0
    k = (len(data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(data):
        return data[f]
    return int(data[f] + (k - f) * (data[c] - data[f]))


def _build_time_series(samples: list) -> dict:
    """将样本数据按秒聚合，构建 TPS、平均响应时间和错误率的时序数据。"""
    if not samples:
        return {"timestamps": [], "tps": [], "avg_rt": [], "error_rate": [], "active_threads": []}

    bucket_size = 1000
    buckets = {}

    for s in samples:
        bucket_key = (s["timestamp"] // bucket_size) * bucket_size
        if bucket_key not in buckets:
            buckets[bucket_key] = {"count": 0, "errors": 0, "total_elapsed": 0, "elapsed_times": []}
        buckets[bucket_key]["count"] += 1
        buckets[bucket_key]["total_elapsed"] += s["elapsed"]
        buckets[bucket_key]["elapsed_times"].append(s["elapsed"])
        if not s["success"]:
            buckets[bucket_key]["errors"] += 1

    sorted_keys = sorted(buckets.keys())
    timestamps = []
    tps = []
    avg_rt = []
    error_rate = []
    active_threads = []

    for key in sorted_keys:
        b = buckets[key]
        timestamps.append(key)
        tps.append(b["count"])
        avg_rt.append(round(b["total_elapsed"] / b["count"], 2) if b["count"] > 0 else 0)
        error_rate.append(round(b["errors"] / b["count"] * 100, 2) if b["count"] > 0 else 0)
        active_threads.append(0)

    return {
        "timestamps": timestamps,
        "tps": tps,
        "avg_rt": avg_rt,
        "error_rate": error_rate,
        "active_threads": active_threads,
    }


def _build_response_time_distribution(samples: list) -> dict:
    """构建响应时间分布统计，按预设时间区间分桶计数。"""
    ranges = [
        (0, 100, "0-100ms"),
        (100, 200, "100-200ms"),
        (200, 500, "200-500ms"),
        (500, 1000, "500-1s"),
        (1000, 2000, "1-2s"),
        (2000, 5000, "2-5s"),
        (5000, float("inf"), ">5s"),
    ]

    distribution = {r[2]: 0 for r in ranges}

    for s in samples:
        for low, high, label in ranges:
            if low <= s["elapsed"] < high:
                distribution[label] += 1
                break

    return {
        "labels": [r[2] for r in ranges],
        "values": [distribution[r[2]] for r in ranges],
    }


def _build_label_stats(samples: list) -> dict:
    """按接口标签聚合统计，计算每个接口的 TPS、响应时间和错误率。"""
    label_data = {}
    for s in samples:
        label = s["label"]
        if label not in label_data:
            label_data[label] = {"count": 0, "errors": 0, "elapsed_times": [], "timestamps": []}
        label_data[label]["count"] += 1
        label_data[label]["elapsed_times"].append(s["elapsed"])
        label_data[label]["timestamps"].append(s["timestamp"])
        if not s["success"]:
            label_data[label]["errors"] += 1

    result = []
    for label, data in label_data.items():
        times = sorted(data["elapsed_times"])
        ts = data["timestamps"]
        duration = (max(ts) - min(ts)) / 1000 if len(ts) > 1 else 1
        tps = round(data["count"] / duration, 2) if duration > 0 else 0
        result.append({
            "label": label,
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

    return result


@router.get("/tasks")
def list_completed_tasks():
    """获取所有已完成测试任务的列表。"""
    tasks = []
    if not os.path.exists(REPORTS_DIR):
        return {"total": 0, "tasks": []}

    # 从数据库获取任务元数据
    task_meta = {}
    try:
        from common.database import get_sync_db
        from manager.core.db_sync import db_get_all_tasks
        db = get_sync_db()
        try:
            db_tasks = db_get_all_tasks(db)
            for t in db_tasks:
                task_meta[t["task_id"]] = t
        finally:
            db.close()
    except Exception:
        pass

    for task_dir in os.listdir(REPORTS_DIR):
        task_path = os.path.join(REPORTS_DIR, task_dir)
        if not os.path.isdir(task_path):
            continue

        stat = os.stat(task_path)
        meta = task_meta.get(task_dir, {})
        tasks.append({
            "task_id": task_dir,
            "script_id": meta.get("script_id", ""),
            "created_at": stat.st_ctime,
            "modified_at": stat.st_mtime,
        })

    tasks.sort(key=lambda x: x["modified_at"], reverse=True)
    return {"total": len(tasks), "tasks": tasks}


@router.get("/tasks/{task_id}/summary")
def get_task_summary(task_id: str):
    """获取指定任务的完整结果摘要，包括汇总统计、时序数据和标签统计。"""
    from manager.core.sample_cache import get_cached_samples
    all_samples = get_cached_samples(task_id)
    if not all_samples:
        raise HTTPException(status_code=404, detail="No result data found")

    merged_summary = _build_summary_from_samples(all_samples)
    time_series = _build_time_series(all_samples)
    distribution = _build_response_time_distribution(all_samples)
    label_stats = _build_label_stats(all_samples)
    time_series_data = [{"label": s["label"], "timestamp": s["timestamp"], "elapsed": s["elapsed"], "success": s["success"]} for s in all_samples]

    return {
        "task_id": task_id,
        "summary": merged_summary,
        "time_series": time_series,
        "distribution": distribution,
        "label_stats": label_stats,
        "time_series_data": time_series_data,
    }


@router.get("/tasks/{task_id}/timeseries")
def get_task_timeseries(task_id: str):
    """获取指定任务的时序数据（TPS、平均响应时间、错误率）。"""
    from manager.core.sample_cache import get_cached_samples
    all_samples = get_cached_samples(task_id)
    if not all_samples:
        raise HTTPException(status_code=404, detail="Task result not found")
    return _build_time_series(all_samples)


@router.get("/tasks/{task_id}/samples")
def get_task_samples(task_id: str, offset: int = 0, limit: int = 50, label: str = None, errors_only: bool = False):
    """分页获取测试样本数据，支持按标签和错误状态过滤。"""
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.exists(task_path):
        raise HTTPException(status_code=404, detail="Task not found")

    from manager.core.sample_cache import get_cached_samples
    all_samples = get_cached_samples(task_id)

    filtered = all_samples
    if label:
        filtered = [s for s in filtered if s["label"] == label]
    if errors_only:
        filtered = [s for s in filtered if not s["success"]]

    total = len(filtered)
    page = filtered[offset:offset + limit]

    return {
        "task_id": task_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "samples": page,
    }


@router.get("/tasks/{task_id}/sample/{index}")
def get_sample_detail(task_id: str, index: int):
    """获取指定样本的详细信息。"""
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.exists(task_path):
        raise HTTPException(status_code=404, detail="Task not found")

    from manager.core.sample_cache import get_cached_samples
    all_samples = get_cached_samples(task_id)

    if index < 1 or index > len(all_samples):
        raise HTTPException(status_code=404, detail="Sample not found")

    return {"task_id": task_id, "sample": all_samples[index - 1]}


@router.get("/tasks/{task_id}/errors")
def get_task_errors(task_id: str):
    """获取指定任务的错误汇总和错误样本列表。"""
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.exists(task_path):
        raise HTTPException(status_code=404, detail="Task not found")

    from manager.core.sample_cache import get_cached_samples
    all_samples = get_cached_samples(task_id)

    errors = [s for s in all_samples if not s["success"]]

    error_summary = {}
    for e in errors:
        key = f"{e['label']} - {e['response_code']}"
        if key not in error_summary:
            error_summary[key] = {"label": e["label"], "response_code": e["response_code"], "count": 0, "avg_elapsed": 0, "total_elapsed": 0}
        error_summary[key]["count"] += 1
        error_summary[key]["total_elapsed"] += e["elapsed"]

    for v in error_summary.values():
        v["avg_elapsed"] = round(v["total_elapsed"] / v["count"], 1) if v["count"] > 0 else 0
        del v["total_elapsed"]

    return {
        "task_id": task_id,
        "total_errors": len(errors),
        "error_summary": list(error_summary.values()),
        "samples": errors[:100],
    }


@router.get("/tasks/{task_id}/full-report")
def get_full_report(task_id: str):
    """获取指定任务的完整报告数据，包含汇总、时序、分布、标签统计和 Agent 详情。"""
    from manager.core.sample_cache import get_cached_samples
    all_samples = get_cached_samples(task_id)
    if not all_samples:
        raise HTTPException(status_code=404, detail="Task not found")

    merged_summary = _build_summary_from_samples(all_samples)
    time_series = _build_time_series(all_samples)
    distribution = _build_response_time_distribution(all_samples)
    label_stats = _build_label_stats(all_samples)
    errors = [s for s in all_samples if not s["success"]]
    labels = list(set(s["label"] for s in all_samples))

    return {
        "task_id": task_id,
        "summary": merged_summary,
        "time_series": time_series,
        "distribution": distribution,
        "label_stats": label_stats,
        "labels": labels,
        "total_requests": len(all_samples),
        "total_errors": len(errors),
    }


@router.get("/tasks/{task_id}/logs")
def get_task_logs(task_id: str):
    """获取指定任务所有 Agent 的 JMeter 日志。"""
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.exists(task_path):
        raise HTTPException(status_code=404, detail="Task not found")

    logs = {}
    for agent_dir in os.listdir(task_path):
        log_path = os.path.join(task_path, agent_dir, "jmeter.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, "r") as f:
                    content = f.read()
                logs[agent_dir] = content
            except Exception:
                logs[agent_dir] = "(读取日志失败)"

    if not logs:
        raise HTTPException(status_code=404, detail="No logs found")

    return {"task_id": task_id, "agents": logs}


@router.get("/tasks/{task_id}/logs/{agent_id}")
def get_agent_log(task_id: str, agent_id: str):
    """获取指定 Agent 的 JMeter 日志内容。"""
    log_path = os.path.join(REPORTS_DIR, task_id, agent_id, "jmeter.log")
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="Log not found")

    try:
        with open(log_path, "r") as f:
            content = f.read()
        return {"task_id": task_id, "agent_id": agent_id, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/compare")
def compare_tasks(task_ids: str):
    """对比多个任务的测试结果，返回各任务的汇总统计和时序数据。"""
    from manager.core.sample_cache import get_cached_samples
    ids = [t.strip() for t in task_ids.split(",") if t.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="需要至少两个任务ID进行对比")

    results = []
    for task_id in ids:
        all_samples = get_cached_samples(task_id)
        if not all_samples:
            continue

        merged = _build_summary_from_samples(all_samples)
        time_series = _build_time_series(all_samples)
        label_stats = _build_label_stats(all_samples)

        task_path = os.path.join(REPORTS_DIR, task_id)
        stat = os.stat(task_path) if os.path.exists(task_path) else None
        results.append({
            "task_id": task_id,
            "created_at": stat.st_ctime if stat else 0,
            "summary": merged,
            "time_series": time_series,
            "label_stats": label_stats,
        })

    if len(results) < 2:
        raise HTTPException(status_code=404, detail="未找到足够的任务数据进行对比")

    return {"tasks": results}


@router.get("/tasks/{task_id}/export")
def export_report(task_id: str):
    """导出指定任务的 HTML 格式测试报告。"""
    from manager.core.sample_cache import get_cached_samples
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.exists(task_path):
        raise HTTPException(status_code=404, detail="Task not found")

    all_samples = get_cached_samples(task_id)
    if not all_samples:
        raise HTTPException(status_code=404, detail="No result data found")

    merged_summary = _build_summary_from_samples(all_samples)
    label_stats = _build_label_stats(all_samples)
    distribution = _build_response_time_distribution(all_samples)

    total_time = 0
    if all_samples:
        total_time = (all_samples[-1]["timestamp"] - all_samples[0]["timestamp"]) / 1000
    tps = round(merged_summary.get("total_samples", 0) / total_time, 2) if total_time > 0 else 0

    time_series = _build_time_series(all_samples)

    import json as _json
    ts_timestamps = time_series.get("timestamps", [])
    ts_labels = [__import__('datetime').datetime.fromtimestamp(t / 1000).strftime('%H:%M:%S') for t in ts_timestamps]
    ts_tps_data = _json.dumps(time_series.get("tps", []))
    ts_rt_data = _json.dumps(time_series.get("avg_rt", []))
    ts_err_data = _json.dumps(time_series.get("error_rate", []))

    dist_labels = _json.dumps(distribution.get("labels", []))
    dist_values = _json.dumps(distribution.get("values", []))

    pct_labels = _json.dumps(["P50", "P90", "P95", "P99"])
    pct_values = _json.dumps([
        merged_summary.get("p50", 0),
        merged_summary.get("p90", 0),
        merged_summary.get("p95", 0),
        merged_summary.get("p99", 0),
    ])

    label_rows = ""
    for l in label_stats:
        err_color = "#dc2626" if l["error_rate"] > 0 else "#16a34a"
        label_rows += f"""<tr>
<td>{l['label']}</td><td>{l['count']}</td><td style="color:#2563eb;font-weight:500">{l.get('tps', 0)}</td>
<td>{l['avg']}ms</td><td>{l['p50']}ms</td><td>{l['p90']}ms</td><td>{l['p99']}ms</td>
<td style="color:{err_color}">{l['error_rate']}%</td>
</tr>"""

    err_rate_color = "#dc2626" if merged_summary.get("error_rate", 0) > 0 else "#16a34a"

    # 缓存 ECharts 脚本
    echarts_path = os.path.join(os.path.dirname(__file__), "..", "static", "echarts.min.js")
    if not os.path.exists(echarts_path):
        import urllib.request
        urllib.request.urlretrieve("https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js", echarts_path)
    with open(echarts_path, "r", encoding="utf-8") as f:
        echarts_js = f.read()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>压测报告 - {task_id}</title>
<script>{echarts_js}</script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f5f5f5; color: #1a1a1a; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
.header {{ background: #fff; padding: 20px 24px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #e0e0e0; }}
.header h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
.header .meta {{ font-size: 13px; color: #888; }}
.summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.card {{ background: #fff; border-radius: 8px; padding: 16px; border: 1px solid #e0e0e0; text-align: center; }}
.card .label {{ font-size: 12px; color: #888; margin-bottom: 4px; }}
.card .value {{ font-size: 22px; font-weight: 600; }}
.card .value.green {{ color: #16a34a; }}
.card .value.red {{ color: #dc2626; }}
.card .value.blue {{ color: #2563eb; }}
.card .value.yellow {{ color: #d97706; }}
.chart-section {{ background: #fff; border-radius: 8px; padding: 20px; border: 1px solid #e0e0e0; margin-bottom: 16px; }}
.chart-section h2 {{ font-size: 15px; font-weight: 600; margin-bottom: 16px; color: #333; }}
.chart-container {{ height: 320px; }}
.charts-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; font-size: 13px; }}
th {{ color: #888; font-weight: 500; background: #fafafa; }}
tr:hover {{ background: #fafafa; }}
.footer {{ margin-top: 40px; padding: 20px; text-align: center; font-size: 12px; color: #999; border-top: 1px solid #e0e0e0; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>压测报告</h1>
        <div class="meta">任务 ID: {task_id} | 总耗时: {round(total_time, 1)}s | 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
    </div>

    <div class="summary-cards">
        <div class="card"><div class="label">总请求数</div><div class="value blue">{merged_summary.get('total_samples', 0)}</div></div>
        <div class="card"><div class="label">吞吐量 (TPS)</div><div class="value blue">{tps}</div></div>
        <div class="card"><div class="label">平均响应时间</div><div class="value green">{merged_summary.get('avg_response_time', 0)}ms</div></div>
        <div class="card"><div class="label">P50</div><div class="value green">{merged_summary.get('p50', 0)}ms</div></div>
        <div class="card"><div class="label">P90</div><div class="value yellow">{merged_summary.get('p90', 0)}ms</div></div>
        <div class="card"><div class="label">P95</div><div class="value yellow">{merged_summary.get('p95', 0)}ms</div></div>
        <div class="card"><div class="label">P99</div><div class="value red">{merged_summary.get('p99', 0)}ms</div></div>
        <div class="card"><div class="label">最小 / 最大</div><div class="value" style="font-size:16px">{merged_summary.get('min_response_time', 0)} / {merged_summary.get('max_response_time', 0)}ms</div></div>
        <div class="card"><div class="label">错误数</div><div class="value" style="color:#dc2626">{merged_summary.get('error_count', 0)}</div></div>
        <div class="card"><div class="label">错误率</div><div class="value" style="color:{err_rate_color}">{merged_summary.get('error_rate', 0)}%</div></div>
    </div>

    <div class="chart-section">
        <h2>TPS / 响应时间 / 错误率 时序图</h2>
        <div class="chart-container" id="chart-ts"></div>
    </div>

    <div class="charts-grid">
        <div class="chart-section">
            <h2>响应时间分布</h2>
            <div class="chart-container" id="chart-dist"></div>
        </div>
        <div class="chart-section">
            <h2>百分位响应时间</h2>
            <div class="chart-container" id="chart-pct"></div>
        </div>
    </div>

    <div class="chart-section">
        <h2>接口统计</h2>
        <table>
        <thead><tr><th>接口名称</th><th>请求数</th><th>TPS</th><th>平均耗时</th><th>P50</th><th>P90</th><th>P99</th><th>错误率</th></tr></thead>
        <tbody>{label_rows}</tbody>
        </table>
    </div>

    <div class="footer">
        <p>由性能测试平台自动生成</p>
    </div>
</div>

<script>
(function() {{
    const timestamps = {ts_labels.__repr__().replace("'", '"')};
    const tpsData = {ts_tps_data};
    const rtData = {ts_rt_data};
    const errData = {ts_err_data};

    if (timestamps.length > 0) {{
        const chartTs = echarts.init(document.getElementById('chart-ts'));
        chartTs.setOption({{
            tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }}, formatter: function(params) {{
                let html = '<div style="font-size:12px;margin-bottom:4px;color:#888">' + params[0].axisValue + '</div>';
                params.forEach(function(p) {{
                    var unit = p.seriesName.includes('RT') ? 'ms' : p.seriesName.includes('错误') ? '%' : '/s';
                    html += '<div style="font-size:12px">' + p.marker + ' ' + p.seriesName + ': <strong>' + p.value + '</strong>' + unit + '</div>';
                }});
                return html;
            }} }},
            legend: {{ data: ['TPS', '平均RT(ms)', '错误率(%)'], top: 0 }},
            xAxis: {{ type: 'category', data: timestamps, boundaryGap: false }},
            yAxis: [
                {{ type: 'value', name: 'TPS' }},
                {{ type: 'value', name: 'ms / %' }}
            ],
            series: [
                {{ name: 'TPS', type: 'line', data: tpsData, smooth: true, lineStyle: {{ width: 2.5, color: '#2563eb' }}, itemStyle: {{ color: '#2563eb' }}, symbol: 'circle', symbolSize: 4, areaStyle: {{ color: 'rgba(37,99,235,0.08)' }}, yAxisIndex: 0 }},
                {{ name: '平均RT(ms)', type: 'line', data: rtData, smooth: true, lineStyle: {{ width: 2.5, color: '#16a34a' }}, itemStyle: {{ color: '#16a34a' }}, symbol: 'circle', symbolSize: 4, areaStyle: {{ color: 'rgba(22,163,74,0.06)' }}, yAxisIndex: 1 }},
                {{ name: '错误率(%)', type: 'line', data: errData, smooth: true, lineStyle: {{ width: 2, color: '#dc2626' }}, itemStyle: {{ color: '#dc2626' }}, symbol: 'circle', symbolSize: 4, yAxisIndex: 1 }}
            ],
            grid: {{ left: 60, right: 60, top: 40, bottom: 30 }}
        }});
        window.addEventListener('resize', () => chartTs.resize());
    }}

    const distLabels = {dist_labels};
    const distValues = {dist_values};
    if (distLabels.length > 0) {{
        const chartDist = echarts.init(document.getElementById('chart-dist'));
        chartDist.setOption({{
            tooltip: {{ trigger: 'axis', formatter: function(p) {{ return p[0].name + '<br/>' + p[0].marker + ' 请求数: <b>' + p[0].value + '</b>'; }} }},
            xAxis: {{ type: 'category', data: distLabels, boundaryGap: false }},
            yAxis: {{ type: 'value' }},
            series: [{{ data: distValues, type: 'line', smooth: true, lineStyle: {{ width: 2.5, color: '#2563eb' }}, itemStyle: {{ color: '#2563eb' }}, symbol: 'circle', symbolSize: 6, areaStyle: {{ color: 'rgba(37,99,235,0.1)' }}, label: {{ show: true, position: 'top', color: '#666', fontSize: 11 }} }}],
            grid: {{ left: 50, right: 20, top: 20, bottom: 30 }}
        }});
        window.addEventListener('resize', () => chartDist.resize());
    }}

    const pctLabels = {pct_labels};
    const pctValues = {pct_values};
    if (pctValues.some(v => v > 0)) {{
        const chartPct = echarts.init(document.getElementById('chart-pct'));
        chartPct.setOption({{
            tooltip: {{ trigger: 'axis', formatter: function(p) {{ return p[0].name + '<br/>' + p[0].marker + ' 响应时间: <b>' + p[0].value + 'ms</b>'; }} }},
            xAxis: {{ type: 'category', data: pctLabels, boundaryGap: false }},
            yAxis: {{ type: 'value', name: 'ms' }},
            series: [{{ data: pctValues, type: 'line', smooth: true, lineStyle: {{ width: 2.5, color: '#d97706' }}, itemStyle: {{ color: '#d97706' }}, symbol: 'circle', symbolSize: 8, areaStyle: {{ color: 'rgba(217,119,6,0.1)' }}, label: {{ show: true, position: 'top', color: '#333', formatter: '{{c}}ms' }} }}],
            grid: {{ left: 50, right: 20, top: 20, bottom: 30 }}
        }});
        window.addEventListener('resize', () => chartPct.resize());
    }}
}})();
// 确保 ECharts 渲染完成
setTimeout(() => {{ window.__echartsReady = true; }}, 1000);
</script>
</body>
</html>"""

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@router.get("/tasks/{task_id}/export-pdf")
def export_report_pdf(task_id: str):
    """导出指定任务的 PDF 格式测试报告（使用 Chrome headless 渲染）。"""
    task_path = os.path.join(REPORTS_DIR, task_id)
    if not os.path.exists(task_path):
        raise HTTPException(status_code=404, detail="Task not found")

    html_resp = export_report(task_id)
    html_content = html_resp.body.decode("utf-8")

    pdf_path = os.path.join(task_path, f"{task_id}_report.pdf")
    html_path = os.path.join(task_path, f"{task_id}_report.html")

    # 先保存 HTML，再用 Chrome 渲染 PDF
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome_path):
        chrome_path = "chromium"

    import subprocess
    import tempfile

    screenshot_path = pdf_path.replace('.pdf', '.png')

    try:
        subprocess.run([
            chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--screenshot={screenshot_path}",
            "--window-size=1200,2000",
            "--virtual-time-budget=15000",
            f"file://{os.path.abspath(html_path)}",
        ], timeout=30, capture_output=True, check=True)

        # 将截图转为 PDF
        from PIL import Image
        img = Image.open(screenshot_path)
        img.save(pdf_path, "PDF", resolution=150)
        os.remove(screenshot_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 生成失败: {str(e)}")

    from fastapi.responses import FileResponse
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"report_{task_id}.pdf",
    )


_trend_cache = {"data": None, "time": 0}
_TREND_CACHE_TTL = 60  # 缓存 60 秒

@router.get("/trend")
def get_performance_trend(label: str = None, limit: int = 20):
    """获取性能趋势数据，展示多个任务的关键指标变化趋势。"""
    import time
    now = time.time()

    # 检查缓存（无 label 且 limit=30 时使用缓存）
    cache_key = f"{label}_{limit}"
    if not label and limit == 30 and _trend_cache["data"] and now - _trend_cache["time"] < _TREND_CACHE_TTL:
        cached = _trend_cache["data"]
        return {"tasks": cached.get("tasks", []), "labels": cached.get("labels", [])}

    from manager.core.sample_cache import get_cached_samples
    if not os.path.exists(REPORTS_DIR):
        return {"tasks": [], "labels": []}

    # 获取所有任务目录并按修改时间排序
    task_dirs = []
    for task_dir in os.listdir(REPORTS_DIR):
        task_path = os.path.join(REPORTS_DIR, task_dir)
        if not os.path.isdir(task_path):
            continue
        stat = os.stat(task_path)
        task_dirs.append((task_dir, task_path, stat.st_mtime))

    # 只处理最近的任务（限制数量避免慢查询）
    task_dirs.sort(key=lambda x: x[2], reverse=True)
    task_dirs = task_dirs[:limit]

    # 获取脚本名称映射
    script_map = {}
    try:
        from common.database import get_sync_db
        from manager.core.db_sync import db_get_all_tasks
        db = get_sync_db()
        try:
            db_tasks = db_get_all_tasks(db)
            for t in db_tasks:
                script_map[t["task_id"]] = t.get("script_id", "")
        finally:
            db.close()
    except Exception:
        pass

    # 获取脚本ID到名称的映射
    script_name_map = {}
    try:
        from manager.core.db_sync import db_get_all_scripts
        db = get_sync_db()
        try:
            scripts = db_get_all_scripts(db)
            for s in scripts:
                script_name_map[str(s["script_id"])] = s.get("original_name") or s.get("filename") or str(s["script_id"])
        finally:
            db.close()
    except Exception:
        pass

    task_data = []
    label_stats = {}
    label_set = set()

    for task_dir, task_path, mtime in reversed(task_dirs):
        try:
            all_samples = get_cached_samples(task_dir)
            if not all_samples:
                continue

            # 收集所有 label
            for s in all_samples:
                lbl = s.get("label", "")
                if lbl:
                    label_set.add(lbl)

            # 如果指定了 label，过滤
            filtered = all_samples
            if label:
                filtered = [s for s in all_samples if s.get("label") == label]

            if not filtered:
                continue

            # 按 label 分组统计
            by_label = {}
            for s in filtered:
                lbl = s.get("label", "unknown")
                if lbl not in by_label:
                    by_label[lbl] = []
                by_label[lbl].append(s)

            for lbl, samples in by_label.items():
                if lbl not in label_stats:
                    label_stats[lbl] = {"samples": [], "errors": 0, "total": 0}
                label_stats[lbl]["samples"].extend([s["elapsed"] for s in samples])
                label_stats[lbl]["errors"] += sum(1 for s in samples if not s["success"])
                label_stats[lbl]["total"] += len(samples)

        except Exception:
            continue

    # 按接口维度汇总
    for lbl, stats in label_stats.items():
        if not stats["samples"]:
            continue
        elapsed = sorted(stats["samples"])
        total = stats["total"]
        errors = stats["errors"]
        label_stats_entry = {
            "label": lbl,
            "total_samples": total,
            "error_count": errors,
            "error_rate": round(errors / total * 100, 2) if total > 0 else 0,
            "avg_response_time": round(sum(elapsed) / len(elapsed), 2),
            "p50": _percentile(elapsed, 50),
            "p90": _percentile(elapsed, 90),
            "p99": _percentile(elapsed, 99),
        }
        task_data.append(label_stats_entry)

    task_data.sort(key=lambda x: x["total_samples"], reverse=True)

    result = {
        "tasks": task_data,
        "labels": sorted(label_set - {""}),
    }

    # 更新缓存
    if not label and limit == 30:
        _trend_cache["data"] = result
        _trend_cache["time"] = now

    return result
