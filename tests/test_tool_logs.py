import pytest
from fastapi import HTTPException

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
