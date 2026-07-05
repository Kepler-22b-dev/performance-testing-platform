from manager.api import tasks
from common.protocol import TaskStatus


def _write_jtl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,sentBytes,grpThreads,allThreads,URL,Latency,Connect\n"
        + "".join(rows),
        encoding="utf-8",
    )


def test_recover_progress_history_from_jtl_merges_base_and_dynamic_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "REPORTS_DIR", str(tmp_path))
    tasks._jtl_progress_cache.clear()

    start_ms = 1700000000000
    _write_jtl(
        tmp_path / "task-1" / "agent-a" / "result.jtl",
        [
            f"{start_ms + 100},10,api,200,OK,tg,true,100,10,1,5,http://example.test,8,1\n",
            f"{start_ms + 1100},20,api,500,ERR,tg,false,200,20,5,5,http://example.test,18,2\n",
        ],
    )
    _write_jtl(
        tmp_path / "task-1" / "agent-a" / "segments" / "dyn-1" / "result.jtl",
        [
            f"{start_ms + 1200},30,api,200,OK,tg,true,300,30,3,3,http://example.test,28,3\n",
        ],
    )

    history = tasks._recover_progress_history_from_jtl(
        "task-1",
        {"start_time": start_ms / 1000, "status": TaskStatus.RUNNING},
    )

    assert len(history) == 2
    assert history[-1]["elapsed"] == 1
    assert history[-1]["throughput"] == 2
    assert history[-1]["active_threads"] == 8
    assert history[-1]["total_samples"] == 3
    assert history[-1]["error_rate"] == 33.33
    assert history[-1]["success_rate"] == 66.67
    assert history[-1]["bytes_received"] == 600


def test_recovered_completed_progress_reports_zero_active_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "REPORTS_DIR", str(tmp_path))
    tasks._jtl_progress_cache.clear()

    start_ms = 1700000000000
    _write_jtl(
        tmp_path / "task-2" / "agent-a" / "result.jtl",
        [
            f"{start_ms + 100},10,api,200,OK,tg,true,100,10,1,5,http://example.test,8,1\n",
        ],
    )

    progress = tasks._recover_progress_from_jtl(
        "task-2",
        {"start_time": start_ms / 1000, "status": TaskStatus.COMPLETED},
    )

    assert progress["task_id"] == "task-2"
    assert progress["agent_id"] == "jtl-recovered"
    assert progress["active_threads"] == 0
    assert progress["throughput"] == 0
    assert progress["total_samples"] == 1
