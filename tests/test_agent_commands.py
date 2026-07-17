import json
import logging
import time
from unittest.mock import MagicMock

from agent.main import JMeterAgent
from common.artifacts import FilesystemArtifactStore
from common.config import get_agent_command_stream
from common.protocol import CommandType, TaskCommand


class FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)


def _build_agent():
    agent = JMeterAgent.__new__(JMeterAgent)
    agent.agent_id = "agent-001"
    agent.redis = FakeRedis()
    agent.logger = logging.getLogger("test-agent-command")
    agent._handle_execute = MagicMock()
    agent._handle_stop = MagicMock()
    agent._handle_adjust_load = MagicMock()
    agent._send_result = MagicMock()
    return agent


def test_agent_command_stream_is_targeted():
    assert get_agent_command_stream("agent-001") == "jmeter:command:agent-001"


def test_legacy_command_receives_reliability_metadata():
    command = TaskCommand.from_json(json.dumps({
        "command": "execute",
        "task_id": "task-1",
        "script_path": "script.jmx",
        "target_agent_id": "agent-001",
    }))

    assert command.command_id.startswith("cmd-")
    assert command.protocol_version == 1
    assert command.created_at > 0


def test_duplicate_command_is_only_accepted_once():
    agent = _build_agent()
    command = TaskCommand(
        command=CommandType.EXECUTE,
        task_id="task-1",
        script_path="script.jmx",
        command_id="cmd-fixed",
        target_agent_id="agent-001",
        expires_at=time.time() + 60,
    )

    assert agent._on_command_message(command.to_json()) is True
    assert agent._on_command_message(command.to_json()) is True
    agent._handle_execute.assert_called_once()


def test_expired_command_is_acknowledged_without_execution():
    agent = _build_agent()
    command = TaskCommand(
        command=CommandType.EXECUTE,
        task_id="task-1",
        script_path="script.jmx",
        target_agent_id="agent-001",
        expires_at=time.time() - 1,
    )

    assert agent._on_command_message(command.to_json()) is True
    agent._handle_execute.assert_not_called()
    agent._send_result.assert_called_once()


def test_malformed_command_is_acknowledged_as_poison_message():
    agent = _build_agent()

    assert agent._on_command_message("not-json") is True
    agent._handle_execute.assert_not_called()


def test_prepare_script_downloads_verified_artifact(monkeypatch, tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "store"))
    artifact = store.put_bytes(
        kind="scripts",
        logical_id="1",
        filename="load.jmx",
        content=b"<jmeterTestPlan />",
    )
    agent = _build_agent()
    monkeypatch.setattr("agent.main.SCRIPTS_DIR", str(tmp_path / "scripts"))
    monkeypatch.setattr("agent.main.get_artifact_store", lambda: store)
    command = TaskCommand(
        command=CommandType.EXECUTE,
        task_id="task-artifact",
        script_path="/manager-only/load.jmx",
        script_artifact=artifact.to_dict(),
    )

    path = agent._prepare_script(command)

    assert open(path, "rb").read() == b"<jmeterTestPlan />"


def test_prepare_script_falls_back_to_inline_content(monkeypatch, tmp_path):
    broken_store = MagicMock()
    broken_store.materialize.side_effect = RuntimeError("s3 unavailable")
    agent = _build_agent()
    monkeypatch.setattr("agent.main.SCRIPTS_DIR", str(tmp_path / "scripts"))
    monkeypatch.setattr("agent.main.get_artifact_store", lambda: broken_store)
    command = TaskCommand(
        command=CommandType.EXECUTE,
        task_id="task-fallback",
        script_path="/manager-only/load.jmx",
        script_content="<fallback />",
        script_artifact={
            "artifact_id": "scripts-test",
            "kind": "scripts",
            "version": "abc",
            "storage_key": "scripts/1/abc/load.jmx",
            "sha256": "abc",
            "size": 3,
            "filename": "load.jmx",
        },
    )

    path = agent._prepare_script(command)

    assert open(path, encoding="utf-8").read() == "<fallback />"


def test_prepare_csv_downloads_to_task_isolated_directory(monkeypatch, tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "store"))
    artifact = store.put_bytes(
        kind="csv",
        logical_id="csv-1",
        filename="users.csv",
        content=b"username\nuser-1\n",
    )
    agent = _build_agent()
    monkeypatch.setattr("agent.main.SCRIPTS_DIR", str(tmp_path / "scripts"))
    monkeypatch.setattr("agent.main.get_artifact_store", lambda: store)
    command = TaskCommand(
        command=CommandType.EXECUTE,
        task_id="task-csv",
        script_path="load.jmx",
        csv_file="csv-1",
        csv_artifact=artifact.to_dict(),
    )

    path = agent._prepare_csv(command)

    assert path.endswith("task-csv/data/users.csv")
    assert open(path, "rb").read() == b"username\nuser-1\n"


def test_prepare_csv_rejects_unresolved_csv_id(tmp_path, monkeypatch):
    agent = _build_agent()
    monkeypatch.setattr("agent.main.SCRIPTS_DIR", str(tmp_path / "scripts"))
    command = TaskCommand(
        command=CommandType.EXECUTE,
        task_id="task-csv",
        script_path="load.jmx",
        csv_file="csv-missing",
    )

    try:
        agent._prepare_csv(command)
        assert False, "Should have rejected unresolved CSV id"
    except FileNotFoundError as exc:
        assert "CSV 不可用" in str(exc)
