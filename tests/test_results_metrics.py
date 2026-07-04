import json
from unittest.mock import patch

from manager.api import results as results_api
from manager.api.results import _build_summary_from_samples, _build_time_series, get_task_summary
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
            "ts": 1700000001000,
            "label": "segment, api",
            "responseData": "segment failure body",
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
