import pytest
from fastapi import HTTPException
import os
import time

from manager.api import tool_logs


def test_platform_log_listing_and_tail(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "manager.log").write_text(
        "INFO manager started\nWARN slow request\nERROR crashed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tool_logs, "LOG_DIR", str(log_dir))

    listed = tool_logs.list_platform_logs()
    manager_log = next(item for item in listed["logs"] if item["key"] == "manager")
    assert manager_log["exists"] is True
    assert manager_log["filename"] == "manager.log"

    content = tool_logs.read_platform_log("manager", lines=2)
    assert content["content"] == "WARN slow request\nERROR crashed"
    assert content["description"] == "Manager 服务日志"


def test_task_tool_log_listing_and_tail(monkeypatch, tmp_path):
    reports_dir = tmp_path / "reports"
    agent_dir = reports_dir / "task-001" / "agent-001"
    agent_dir.mkdir(parents=True)
    (agent_dir / "jmeter.log").write_text(
        "line 1\nline 2\nline 3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tool_logs, "REPORTS_DIR", str(reports_dir))

    listed = tool_logs.list_task_tool_logs("task-001")
    assert listed["task_id"] == "task-001"
    assert listed["agents"][0]["agent_id"] == "agent-001"
    jmeter_log = next(item for item in listed["agents"][0]["logs"] if item["key"] == "jmeter")
    assert jmeter_log["exists"] is True

    content = tool_logs.read_task_tool_log("task-001", "agent-001", "jmeter", lines=2)
    assert content["content"] == "line 2\nline 3"
    assert content["agent_id"] == "agent-001"


def test_safe_join_blocks_path_traversal(tmp_path):
    with pytest.raises(HTTPException) as exc:
        tool_logs._safe_join(str(tmp_path), "..", "outside.log")

    assert exc.value.status_code == 400


def test_log_analysis_counts_levels_and_top_issues(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    log_dir.mkdir()
    reports_dir.mkdir()
    (log_dir / "manager.log").write_text(
        "\n".join([
            "2026-07-04 10:00:00 [INFO] manager: started",
            "2026-07-04 10:01:00 [WARN] manager: slow cleanup",
            "2026-07-04 10:02:00 [ERROR] manager: RuntimeError: failed task-abc123 in 120ms",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool_logs, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(tool_logs, "REPORTS_DIR", str(reports_dir))

    analysis = tool_logs.analyze_logs(lines_per_file=100, include_tasks=False)

    assert analysis["summary"]["files_scanned"] == 1
    assert analysis["summary"]["level_counts"]["INFO"] == 1
    assert analysis["summary"]["level_counts"]["WARN"] == 1
    assert analysis["summary"]["level_counts"]["ERROR"] == 1
    assert analysis["top_issues"][0]["count"] == 1
    assert any("RuntimeError" in issue["signature"] for issue in analysis["top_issues"])


def test_cleanup_expired_logs_keeps_active_logs_and_reports(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    agent_dir = reports_dir / "task-001" / "agent-001"
    agent_dir.mkdir(parents=True)
    log_dir.mkdir()

    active_log = log_dir / "manager.log"
    rotated_log = log_dir / "manager.log.1"
    task_log = agent_dir / "jmeter.log"
    report_file = agent_dir / "result.xml"
    active_log.write_text("active", encoding="utf-8")
    rotated_log.write_text("old backup", encoding="utf-8")
    task_log.write_text("old task log", encoding="utf-8")
    report_file.write_text("<result/>", encoding="utf-8")

    old_time = time.time() - 120 * 86400
    os.utime(active_log, (old_time, old_time))
    os.utime(rotated_log, (old_time, old_time))
    os.utime(task_log, (old_time, old_time))
    os.utime(report_file, (old_time, old_time))

    monkeypatch.setattr(tool_logs, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(tool_logs, "REPORTS_DIR", str(reports_dir))
    monkeypatch.setattr(tool_logs, "PLATFORM_LOG_RETENTION_DAYS", 90)
    monkeypatch.setattr(tool_logs, "TASK_LOG_RETENTION_DAYS", 90)

    result = tool_logs.cleanup_expired_logs()

    assert result["deleted_files"] == 2
    assert active_log.exists()
    assert not rotated_log.exists()
    assert not task_log.exists()
    assert report_file.exists()
