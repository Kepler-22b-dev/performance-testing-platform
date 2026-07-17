from unittest.mock import MagicMock

import pytest

from common.artifacts import FilesystemArtifactStore
from manager.core import variables


def test_upload_csv_validates_and_persists_artifact(monkeypatch, tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
    mock_db = MagicMock()
    monkeypatch.setattr(variables, "CSV_DIR", str(tmp_path / "csv"))
    monkeypatch.setattr(variables, "get_artifact_store", lambda: store)
    monkeypatch.setattr(variables, "get_sync_db", lambda: mock_db)
    monkeypatch.setattr(variables, "db_create_csv", lambda db, meta: meta)

    result = variables.upload_csv(
        "users.csv",
        "username,password\nuser-1,pass-1\n".encode("utf-8"),
    )

    assert result["csv_id"].startswith("csv-")
    assert result["csv"]["artifact_id"].startswith("csv-")
    assert len(result["csv"]["sha256"]) == 64
    assert result["csv"]["headers"] == ["username", "password"]
    assert result["csv"]["row_count"] == 1
    assert result["csv"]["encoding"] in {"utf-8-sig", "utf-8"}


def test_upload_csv_detects_semicolon_delimiter(monkeypatch, tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
    monkeypatch.setattr(variables, "CSV_DIR", str(tmp_path / "csv"))
    monkeypatch.setattr(variables, "get_artifact_store", lambda: store)
    monkeypatch.setattr(variables, "get_sync_db", MagicMock)
    monkeypatch.setattr(variables, "db_create_csv", lambda db, meta: meta)

    result = variables.upload_csv(
        "users.csv",
        b"username;city\nuser-1;Shanghai\n",
    )

    assert result["delimiter"] == ";"


def test_legacy_csv_is_backfilled_with_artifact_metadata(monkeypatch, tmp_path):
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_bytes(b"username\nuser-1\n")
    store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
    mock_db = MagicMock()
    update = MagicMock()
    monkeypatch.setattr(variables, "get_artifact_store", lambda: store)
    monkeypatch.setattr(variables, "get_sync_db", lambda: mock_db)
    monkeypatch.setattr(variables, "db_update_csv_artifact", update)
    monkeypatch.setattr(variables, "get_csv", lambda csv_id: {
        "csv_id": csv_id,
        "filename": "legacy.csv",
        "filepath": str(csv_path),
        "size": csv_path.stat().st_size,
    })

    artifact, meta = variables.get_csv_artifact("csv-legacy")

    assert artifact.sha256 == meta["sha256"]
    update.assert_called_once()
    assert update.call_args.args[1] == "csv-legacy"


def test_csv_shards_are_balanced_and_non_overlapping(monkeypatch, tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
    monkeypatch.setattr(variables, "CSV_DIR", str(tmp_path / "csv"))
    monkeypatch.setattr(variables, "get_artifact_store", lambda: store)
    monkeypatch.setattr(variables, "get_sync_db", MagicMock)
    monkeypatch.setattr(variables, "db_create_csv", lambda db, meta: meta)
    uploaded = variables.upload_csv(
        "users.csv",
        b"username\nuser-1\nuser-2\nuser-3\nuser-4\nuser-5\n",
    )["csv"]
    monkeypatch.setattr(variables, "get_csv", lambda csv_id: dict(uploaded))

    artifacts, partitions, _ = variables.prepare_csv_distribution(
        uploaded["csv_id"],
        "task-1",
        ["agent-001", "agent-002"],
        "shard",
    )

    assert [partitions[a]["row_count"] for a in ["agent-001", "agent-002"]] == [3, 2]
    shard_rows = []
    for agent_id in ["agent-001", "agent-002"]:
        destination = tmp_path / f"{agent_id}.csv"
        store.materialize(artifacts[agent_id], str(destination))
        rows = destination.read_text(encoding="utf-8").strip().splitlines()
        assert rows[0] == "username"
        shard_rows.extend(rows[1:])
    assert shard_rows == ["user-1", "user-2", "user-3", "user-4", "user-5"]
    assert len(set(shard_rows)) == 5


def test_csv_sharding_requires_at_least_one_row_per_agent(monkeypatch, tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
    monkeypatch.setattr(variables, "CSV_DIR", str(tmp_path / "csv"))
    monkeypatch.setattr(variables, "get_artifact_store", lambda: store)
    monkeypatch.setattr(variables, "get_sync_db", MagicMock)
    monkeypatch.setattr(variables, "db_create_csv", lambda db, meta: meta)
    uploaded = variables.upload_csv(
        "users.csv",
        b"username\nuser-1\n",
    )["csv"]
    monkeypatch.setattr(variables, "get_csv", lambda csv_id: dict(uploaded))

    with pytest.raises(ValueError, match="少于 Agent 数量"):
        variables.prepare_csv_distribution(
            uploaded["csv_id"],
            "task-1",
            ["agent-001", "agent-002"],
            "shard",
        )


def test_upload_csv_rejects_inconsistent_columns(monkeypatch, tmp_path):
    monkeypatch.setattr(variables, "CSV_DIR", str(tmp_path / "csv"))

    with pytest.raises(ValueError, match="列数不一致"):
        variables.upload_csv(
            "broken.csv",
            b"username,password\nuser-1\n",
        )


def test_upload_csv_rejects_duplicate_headers(monkeypatch, tmp_path):
    monkeypatch.setattr(variables, "CSV_DIR", str(tmp_path / "csv"))

    with pytest.raises(ValueError, match="重复列名"):
        variables.upload_csv(
            "broken.csv",
            b"username,username\nuser-1,user-2\n",
        )


def test_upload_csv_rejects_header_only_file(monkeypatch, tmp_path):
    monkeypatch.setattr(variables, "CSV_DIR", str(tmp_path / "csv"))

    with pytest.raises(ValueError, match="至少需要一行"):
        variables.upload_csv("empty.csv", b"username,password\n")
