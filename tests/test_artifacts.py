import dataclasses

import pytest

from common.artifacts import (
    ArtifactIntegrityError,
    ArtifactRef,
    FilesystemArtifactStore,
)


def test_filesystem_artifact_round_trip_is_content_addressed(tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "store"))
    first = store.put_bytes(
        kind="scripts",
        logical_id="script/../../1",
        filename="../../load.jmx",
        content=b"<jmeterTestPlan />",
    )
    second = store.put_bytes(
        kind="scripts",
        logical_id="script/../../1",
        filename="../../load.jmx",
        content=b"<jmeterTestPlan />",
    )

    destination = tmp_path / "agent" / "task.jmx"
    store.materialize(first, str(destination))

    assert first == second
    assert destination.read_bytes() == b"<jmeterTestPlan />"
    assert ".." not in first.storage_key
    assert first.version == first.sha256


def test_materialize_rejects_tampered_artifact(tmp_path):
    store = FilesystemArtifactStore(str(tmp_path / "store"))
    artifact = store.put_bytes(
        kind="scripts",
        logical_id="1",
        filename="load.jmx",
        content=b"trusted",
    )
    stored_path = store._resolve(artifact.storage_key)
    stored_path.write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError):
        store.materialize(artifact, str(tmp_path / "task.jmx"))


def test_artifact_manifest_requires_all_integrity_fields():
    artifact = ArtifactRef(
        artifact_id="scripts-abc",
        kind="scripts",
        version="abc",
        storage_key="scripts/1/abc/load.jmx",
        sha256="abc",
        size=3,
        filename="load.jmx",
    )
    value = dataclasses.asdict(artifact)

    assert ArtifactRef.from_dict(value) == artifact
    value.pop("sha256")
    with pytest.raises(KeyError):
        ArtifactRef.from_dict(value)
