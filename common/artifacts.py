"""Content-addressed artifact storage shared by Manager and Agent."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

from common.config import (
    ARTIFACT_LOCAL_DIR,
    ARTIFACT_STORE_BACKEND,
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_KEY,
    S3_SESSION_TOKEN,
    S3_USE_SSL,
)


_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_component(value: str, fallback: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("-", os.path.basename(str(value or ""))).strip(".-")
    return cleaned or fallback


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    kind: str
    version: str
    storage_key: str
    sha256: str
    size: int
    filename: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "ArtifactRef":
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})


class ArtifactIntegrityError(RuntimeError):
    """Raised when downloaded content does not match its immutable manifest."""


class ArtifactStore:
    def put_bytes(self, *, kind: str, logical_id: str, filename: str, content: bytes) -> ArtifactRef:
        digest = _sha256_bytes(content)
        safe_kind = _safe_component(kind, "file")
        safe_id = _safe_component(logical_id, digest[:16])
        safe_name = _safe_component(filename, "artifact.bin")
        key = f"{safe_kind}/{safe_id}/{digest}/{safe_name}"
        self._put(key, content)
        return ArtifactRef(
            artifact_id=f"{safe_kind}-{digest[:24]}",
            kind=safe_kind,
            version=digest,
            storage_key=key,
            sha256=digest,
            size=len(content),
            filename=safe_name,
        )

    def materialize(self, artifact: ArtifactRef, destination: str) -> str:
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{destination_path.name}.",
            dir=str(destination_path.parent),
        )
        os.close(fd)
        try:
            with open(temporary_path, "wb") as target:
                self._download(artifact.storage_key, target)
            actual_size = os.path.getsize(temporary_path)
            actual_digest = _sha256_file(temporary_path)
            if actual_size != artifact.size or actual_digest != artifact.sha256:
                raise ArtifactIntegrityError(
                    f"制品校验失败: expected={artifact.sha256}/{artifact.size}, "
                    f"actual={actual_digest}/{actual_size}"
                )
            os.replace(temporary_path, destination_path)
            return str(destination_path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def _put(self, key: str, content: bytes) -> None:
        raise NotImplementedError

    def _download(self, key: str, target: BinaryIO) -> None:
        raise NotImplementedError


class FilesystemArtifactStore(ArtifactStore):
    def __init__(self, root: str = ARTIFACT_LOCAL_DIR):
        self.root = Path(root).resolve()

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("非法制品路径")
        return path

    def _put(self, key: str, content: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and _sha256_file(str(path)) == _sha256_bytes(content):
            return
        fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def _download(self, key: str, target: BinaryIO) -> None:
        with open(self._resolve(key), "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                target.write(chunk)


class S3ArtifactStore(ArtifactStore):
    def __init__(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("S3 制品存储需要安装 boto3") from exc

        kwargs = {
            "service_name": "s3",
            "region_name": S3_REGION,
            "endpoint_url": S3_ENDPOINT_URL,
            "use_ssl": S3_USE_SSL,
        }
        if S3_ACCESS_KEY:
            kwargs["aws_access_key_id"] = S3_ACCESS_KEY
        if S3_SECRET_KEY:
            kwargs["aws_secret_access_key"] = S3_SECRET_KEY
        if S3_SESSION_TOKEN:
            kwargs["aws_session_token"] = S3_SESSION_TOKEN
        self.client = boto3.client(**kwargs)
        self.bucket = S3_BUCKET

    def _put(self, key: str, content: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            Metadata={"sha256": _sha256_bytes(content)},
        )

    def _download(self, key: str, target: BinaryIO) -> None:
        self.client.download_fileobj(self.bucket, key, target)


def get_artifact_store() -> ArtifactStore:
    if ARTIFACT_STORE_BACKEND in {"filesystem", "local"}:
        return FilesystemArtifactStore()
    if ARTIFACT_STORE_BACKEND in {"s3", "minio"}:
        return S3ArtifactStore()
    raise ValueError(f"不支持的制品存储后端: {ARTIFACT_STORE_BACKEND}")
