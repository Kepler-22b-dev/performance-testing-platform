import json
from unittest.mock import patch

from manager.api import results as results_api
from manager.api.results import (
    _build_segment_stats,
    _build_summary_from_samples,
    _build_time_series,
    get_task_summary,
)
from manager.core import sample_cache


def _sample(timestamp_ms, bytes_received=100, sent_bytes=20):
    return {
        "timestamp": timestamp_ms,
        "elapsed": 10,
        "label": "api",
        "success": True,
        "bytes": bytes_received,
        "sent_bytes": sent_bytes,
    }


def test_summary_includes_qps_and_traffic_metrics():
    samples = []
    base = 1_700_000_000_000
    for second in range(10):
        for _ in range(second + 1):
            samples.append(_sample(base + second * 1000))

    summary = _build_summary_from_samples(samples)

    assert summary["max_qps"] == 10
    assert summary["stable_qps"] == 5.5
    assert summary["stable_window"] == "middle_80_percent"
    assert summary["total_network_bytes"] == 6600
    assert summary["total_bytes_received"] == 5500
    assert summary["total_bytes_sent"] == 1100
    assert summary["peak_network_bytes_per_sec"] == 1200


def test_time_series_includes_network_bytes_per_second():
    base = 1_700_000_000_000
    samples = [
        _sample(base, bytes_received=100, sent_bytes=10),
        _sample(base + 100, bytes_received=200, sent_bytes=20),
        _sample(base + 1000, bytes_received=300, sent_bytes=30),
    ]

    series = _build_time_series(samples)

    assert series["tps"] == [2, 1]
    assert series["bytes_received"] == [300, 300]
    assert series["bytes_sent"] == [30, 30]
    assert series["network_bytes"] == [330, 330]


def test_time_series_sums_active_threads_by_segment_scope():
    base = 1_700_000_000_000
    samples = [
        {
            **_sample(base),
            "source_agent": "agent-a",
            "source_segment": "base",
            "thread_name": "Thread Group 1-100",
            "all_threads": 100,
        },
        {
            **_sample(base + 100),
            "source_agent": "agent-a",
            "source_segment": "dyn-001",
            "thread_name": "Thread Group 1-25",
            "all_threads": 25,
        },
    ]

    series = _build_time_series(samples)

    assert series["active_threads"] == [125]


def test_build_segment_stats_marks_dynamic_segments():
    base = 1_700_000_000_000
    samples = [
        {
            **_sample(base),
            "source_agent": "agent-a",
            "source_segment": "base",
            "all_threads": 100,
        },
        {
            **_sample(base + 1000),
            "source_agent": "agent-a",
            "source_segment": "dyn-001",
            "all_threads": 50,
            "success": False,
        },
    ]

    segments = _build_segment_stats(samples)

    assert [s["type"] for s in segments] == ["base", "dynamic"]
    assert segments[1]["segment_id"] == "dyn-001"
    assert segments[1]["label"] == "动态调压段 1"
    assert segments[1]["sample_count"] == 1
    assert segments[1]["error_rate"] == 100.0
    assert segments[1]["max_threads"] == 50


def test_sample_cache_includes_dynamic_segment_results(monkeypatch, tmp_path):
    reports_dir = tmp_path / "reports"
    base_dir = reports_dir / "task-20260704-001" / "agent-local"
    segment_dir = base_dir / "segments" / "dyn-001"
    segment_dir.mkdir(parents=True)
    base_dir.mkdir(parents=True, exist_ok=True)

    (base_dir / "result.jtl").write_text(
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,URL,Latency,Connect\n"
        "1700000000000,100,base api,200,OK,tg 1,true,100,10,http://example.test/base,80,5\n",
        encoding="utf-8",
    )
    (segment_dir / "result.jtl").write_text(
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,URL,Latency,Connect\n"
        "1700000001000,120,\"segment, api\",500,ERR,tg 2,false,200,20,http://example.test/seg,90,6\n",
        encoding="utf-8",
    )
    (segment_dir / "error_responses.jsonl").write_text(
        json.dumps({
            "captureVersion": 2,
            "ts": 1700000001000,
            "label": "segment, api",
            "threadName": "tg 2",
            "requestMethod": "POST",
            "requestUrl": "http://example.test/seg?source=capture",
            "requestHeaders": "Content-Type: application/json",
            "responseHeaders": "X-Request-Id: req-1",
            "samplerData": "{\"item\":\"demo\"}",
            "responseData": "segment failure body",
            "responseDataTruncated": True,
        }) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sample_cache, "REPORTS_DIR", str(reports_dir))
    sample_cache.invalidate_cache("task-20260704-001")

    samples = sample_cache.get_cached_samples("task-20260704-001")

    assert [s["index"] for s in samples] == [1, 2]
    assert [s["label"] for s in samples] == ["base api", "segment, api"]
    assert samples[1]["source_segment"] == "dyn-001"
    assert samples[1]["response_data"] == "segment failure body"
    assert samples[1]["request_method"] == "POST"
    assert samples[1]["url"] == "http://example.test/seg?source=capture"
    assert samples[1]["request_headers"] == "Content-Type: application/json"
    assert samples[1]["response_headers"] == "X-Request-Id: req-1"
    assert samples[1]["sampler_data"] == "{\"item\":\"demo\"}"
    assert samples[1]["response_data_truncated"] is True
    assert samples[1]["error_details_captured"] is True


def test_sample_cache_parses_jtl_thread_counts(monkeypatch, tmp_path):
    reports_dir = tmp_path / "reports"
    result_dir = reports_dir / "task-20260704-002" / "agent-local"
    result_dir.mkdir(parents=True)
    (result_dir / "result.jtl").write_text(
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,grpThreads,allThreads,URL,Latency,Connect\n"
        "1700000000000,100,api,200,OK,tg 1,true,100,10,7,9,http://example.test/base,80,5\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sample_cache, "REPORTS_DIR", str(reports_dir))
    sample_cache.invalidate_cache("task-20260704-002")

    samples = sample_cache.get_cached_samples("task-20260704-002")

    assert samples[0]["grp_threads"] == 7
    assert samples[0]["all_threads"] == 9



def test_summary_endpoint_does_not_return_raw_time_series_data():
    samples = [
        _sample(1_700_000_000_000),
        _sample(1_700_000_001_000),
    ]

    with patch("manager.core.sample_cache.get_cached_samples", return_value=samples):
        data = get_task_summary("task-20260704-001")

    assert "time_series_data" not in data
    assert data["summary"]["total_samples"] == 2
    assert data["time_series"]["tps"] == [1, 1]
    assert data["segment_stats"][0]["type"] == "base"


def test_label_timeseries_accepts_display_task_id(monkeypatch, tmp_path):
    reports_dir = tmp_path / "reports"
    result_dir = reports_dir / "task-20260704-008" / "agent-local"
    result_dir.mkdir(parents=True)
    (result_dir / "result.jtl").write_text(
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,URL,Latency,Connect\n"
        "1700000000000,100,product api,200,OK,tg 1,true,100,10,http://example.test/products,80,5\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(results_api, "REPORTS_DIR", str(reports_dir))
    monkeypatch.setattr(sample_cache, "REPORTS_DIR", str(reports_dir))
    sample_cache.invalidate_cache("task-20260704-008")

    data = results_api.get_task_label_timeseries("20260704-008", "product api")

    assert data["task_id"] == "task-20260704-008"
    assert data["time_series"]["tps"] == [1]
