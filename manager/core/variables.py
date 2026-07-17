"""变量与 CSV 数据文件管理模块。"""

import sys
import os
import time
import csv
import io
import re
import uuid
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.artifacts import ArtifactRef, get_artifact_store
from common.config import CSV_MAX_UPLOAD_BYTES
from common.database import get_sync_db
from manager.core.db_sync import (
    db_get_all_vars, db_create_var, db_update_var, db_delete_var,
    db_get_all_csvs, db_get_csv, db_create_csv, db_delete_csv,
    db_update_csv_artifact,
    db_get_csvs_page,
)

CSV_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config", "csv",
)
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _ensure_dirs():
    os.makedirs(CSV_DIR, exist_ok=True)


def _decode_csv(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 编码不受支持，请使用 UTF-8 或 GB18030")


def _parse_csv(content: bytes) -> tuple[str, str, str, list, list, int]:
    if not content:
        raise ValueError("CSV 文件不能为空")
    if len(content) > CSV_MAX_UPLOAD_BYTES:
        raise ValueError(f"CSV 文件不能超过 {CSV_MAX_UPLOAD_BYTES // 1024 // 1024} MB")

    text, encoding = _decode_csv(content)
    sample = text[:65536]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise ValueError("CSV 文件没有可读取的数据") from exc

    headers = [str(value).strip() for value in headers]
    if not headers or not any(headers):
        raise ValueError("CSV 表头不能为空")
    if len(set(headers)) != len(headers):
        raise ValueError("CSV 表头存在重复列名")

    preview = []
    row_count = 0
    expected_columns = len(headers)
    for row_number, row in enumerate(reader, start=2):
        if not row or not any(str(value).strip() for value in row):
            continue
        if len(row) != expected_columns:
            raise ValueError(
                f"CSV 第 {row_number} 行列数不一致："
                f"期望 {expected_columns} 列，实际 {len(row)} 列"
            )
        row_count += 1
        if len(preview) < 5:
            preview.append(row)

    if row_count == 0:
        raise ValueError("CSV 至少需要一行参数数据")

    return text, encoding, delimiter, headers, preview, row_count


def _artifact_from_meta(meta: dict) -> Optional[ArtifactRef]:
    required = ("artifact_id", "artifact_version", "storage_key", "sha256", "size", "filename")
    if not all(meta.get(key) not in (None, "") for key in required):
        return None
    return ArtifactRef(
        artifact_id=meta["artifact_id"],
        kind="csv",
        version=meta["artifact_version"],
        storage_key=meta["storage_key"],
        sha256=meta["sha256"],
        size=int(meta["size"]),
        filename=meta["filename"],
    )


def _ensure_csv_local_file(meta: dict) -> Optional[str]:
    filepath = meta.get("filepath", "")
    if filepath and os.path.exists(filepath):
        return filepath
    artifact = _artifact_from_meta(meta)
    if not artifact:
        return None
    _ensure_dirs()
    filepath = filepath or os.path.join(CSV_DIR, f"{meta['csv_id']}_{artifact.filename}")
    get_artifact_store().materialize(artifact, filepath)
    return filepath


# ===== 变量管理 =====

def get_all_vars() -> list:
    db = get_sync_db()
    try:
        return db_get_all_vars(db)
    finally:
        db.close()


def add_var(name: str, value: str, description: str = "", scope: str = "global") -> dict:
    name = (name or "").strip()
    if not VARIABLE_NAME_PATTERN.fullmatch(name):
        return {
            "status": "error",
            "message": "变量名只能包含字母、数字、下划线、点或连字符，且不能以数字开头",
        }
    db = get_sync_db()
    try:
        vars_list = db_get_all_vars(db)
        for v in vars_list:
            if v["name"] == name:
                return {"status": "error", "message": f"变量已存在: {name}"}

        var = {
            "id": f"var-{int(time.time()*1000)}",
            "name": name,
            "value": value,
            "description": description,
            "scope": scope,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        db_create_var(db, var)
        return {"status": "added", "var": var}
    finally:
        db.close()


def update_var(var_id: str, name: str = None, value: str = None, description: str = None) -> dict:
    db = get_sync_db()
    try:
        update_data = {}
        if name is not None:
            name = name.strip()
            if not VARIABLE_NAME_PATTERN.fullmatch(name):
                return {
                    "status": "error",
                    "message": "变量名只能包含字母、数字、下划线、点或连字符，且不能以数字开头",
                }
            update_data["name"] = name
        if value is not None:
            update_data["value"] = value
        if description is not None:
            update_data["description"] = description

        if update_data:
            updated = db_update_var(db, var_id, **update_data)
            if updated:
                return {"status": "updated", "var_id": var_id}
        return {"status": "error", "message": f"变量不存在: {var_id}"}
    finally:
        db.close()


def delete_var(var_id: str) -> dict:
    db = get_sync_db()
    try:
        deleted = db_delete_var(db, var_id)
        if deleted:
            return {"status": "deleted", "var_id": var_id}
        return {"status": "error", "message": f"变量不存在: {var_id}"}
    finally:
        db.close()


def get_vars_dict() -> dict:
    vars_list = get_all_vars()
    return {v["name"]: v["value"] for v in vars_list}


# ===== CSV 数据文件管理 =====

def upload_csv(filename: str, content: bytes) -> dict:
    _ensure_dirs()

    csv_id = f"csv-{uuid.uuid4().hex}"
    safe_name = os.path.basename(filename or "data.csv").replace(" ", "_").replace("/", "_")
    filepath = os.path.join(CSV_DIR, f"{csv_id}_{safe_name}")
    _, encoding, delimiter, headers, preview, row_count = _parse_csv(content)
    artifact = get_artifact_store().put_bytes(
        kind="csv",
        logical_id=csv_id,
        filename=safe_name,
        content=content,
    )

    with open(filepath, "wb") as f:
        f.write(content)

    meta = {
        "csv_id": csv_id,
        "filename": os.path.basename(filename or safe_name),
        "filepath": filepath,
        "artifact_id": artifact.artifact_id,
        "artifact_version": artifact.version,
        "storage_key": artifact.storage_key,
        "sha256": artifact.sha256,
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": headers,
        "row_count": row_count,
        "preview": preview,
        "size": len(content),
        "created_at": time.time(),
    }
    db = get_sync_db()
    try:
        db_create_csv(db, meta)
    finally:
        db.close()

    return {"status": "uploaded", "csv": meta, **meta}


def get_all_csvs() -> list:
    db = get_sync_db()
    try:
        csvs = db_get_all_csvs(db)
        return csvs
    finally:
        db.close()


def get_csvs_page(offset: int = 0, limit: int = 100) -> tuple[int, list]:
    db = get_sync_db()
    try:
        total, csvs = db_get_csvs_page(db, offset=offset, limit=limit)
        return total, csvs
    finally:
        db.close()


def get_csv(csv_id: str) -> Optional[dict]:
    db = get_sync_db()
    try:
        return db_get_csv(db, csv_id)
    finally:
        db.close()


def get_csv_data(csv_id: str, offset: int = 0, limit: int = 100) -> Optional[dict]:
    meta = get_csv(csv_id)
    if not meta:
        return None

    filepath = _ensure_csv_local_file(meta)
    if not filepath:
        return None

    headers = []
    rows = []
    total = 0

    try:
        configured_encoding = meta.get("encoding")
        encodings = [configured_encoding] if configured_encoding else []
        encodings.extend(["utf-8-sig", "utf-8", "gb18030", "latin-1"])
        f = None
        for enc in encodings:
            try:
                f = open(filepath, "r", encoding=enc)
                f.read(1)
                f.seek(0)
                break
            except (UnicodeDecodeError, UnicodeError):
                if f:
                    f.close()
                f = None

        if f is None:
            return None

        reader = csv.reader(f, delimiter=meta.get("delimiter") or ",")
        for i, row in enumerate(reader):
            if i == 0:
                headers = row
            else:
                total += 1
                if offset <= total - 1 < offset + limit:
                    rows.append(row)

        f.close()
    except Exception:
        pass

    return {
        "csv_id": csv_id,
        "headers": headers,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": rows,
    }


def delete_csv(csv_id: str) -> dict:
    meta = get_csv(csv_id)
    if not meta:
        return {"status": "error", "message": f"CSV不存在: {csv_id}"}

    filepath = meta.get("filepath", "")
    if os.path.exists(filepath):
        os.remove(filepath)

    db = get_sync_db()
    try:
        db_delete_csv(db, csv_id)
    finally:
        db.close()

    return {"status": "deleted", "csv_id": csv_id}


def get_csv_artifact(csv_id_or_path: str) -> tuple[ArtifactRef, dict]:
    """解析任务中的 CSV 引用，并为旧数据补建制品元数据。"""
    meta = get_csv(csv_id_or_path)
    persisted = meta is not None
    if meta:
        artifact = _artifact_from_meta(meta)
        if artifact:
            return artifact, meta
        filepath = meta.get("filepath", "")
    else:
        filepath = csv_id_or_path
        meta = {
            "csv_id": f"legacy-{int(time.time() * 1000)}",
            "filename": os.path.basename(filepath or "data.csv"),
            "filepath": filepath,
        }

    if not filepath or not os.path.isfile(filepath):
        raise FileNotFoundError(f"CSV 不存在或不可用: {csv_id_or_path}")

    with open(filepath, "rb") as source:
        content = source.read()
    _, encoding, delimiter, headers, preview, row_count = _parse_csv(content)
    artifact = get_artifact_store().put_bytes(
        kind="csv",
        logical_id=meta["csv_id"],
        filename=meta["filename"],
        content=content,
    )
    artifact_data = {
        "artifact_id": artifact.artifact_id,
        "artifact_version": artifact.version,
        "storage_key": artifact.storage_key,
        "sha256": artifact.sha256,
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": headers,
        "preview": preview,
        "row_count": row_count,
        "size": len(content),
    }
    if persisted:
        db = get_sync_db()
        try:
            db_update_csv_artifact(db, csv_id_or_path, artifact_data)
        finally:
            db.close()
    meta.update(artifact_data)
    return artifact, meta


def prepare_csv_distribution(
    csv_id_or_path: str,
    task_id: str,
    agent_ids: list[str],
    distribution: str = "replicate",
) -> tuple[dict[str, ArtifactRef], dict[str, dict], dict]:
    """为每个 Agent 准备 CSV 制品，支持完整复制和均衡分片。"""
    distribution = str(distribution or "replicate").strip().lower()
    if distribution not in {"replicate", "shard"}:
        raise ValueError("CSV 分发策略只支持 replicate 或 shard")
    if not agent_ids:
        raise ValueError("CSV 分发需要至少一个 Agent")
    if len(set(agent_ids)) != len(agent_ids):
        raise ValueError("CSV 分发的 Agent 列表不能重复")

    source_artifact, meta = get_csv_artifact(csv_id_or_path)
    row_count = int(meta.get("row_count") or 0)
    if distribution == "replicate" or len(agent_ids) == 1:
        artifacts = {agent_id: source_artifact for agent_id in agent_ids}
        partitions = {
            agent_id: {
                "mode": distribution,
                "shard_index": 0,
                "shard_count": 1,
                "row_start": 1 if row_count else 0,
                "row_end": row_count,
                "row_count": row_count,
                "source_sha256": source_artifact.sha256,
            }
            for agent_id in agent_ids
        }
        return artifacts, partitions, meta

    filepath = _ensure_csv_local_file(meta)
    if not filepath:
        raise FileNotFoundError(f"CSV 制品无法在 Manager 上物化: {csv_id_or_path}")
    with open(filepath, "rb") as source:
        content = source.read()
    text, _, delimiter, headers, _, validated_row_count = _parse_csv(content)
    rows = [
        row for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ][1:]
    rows = [row for row in rows if row and any(str(value).strip() for value in row)]
    if len(rows) != validated_row_count:
        raise ValueError("CSV 分片前后行数校验不一致")
    if len(rows) < len(agent_ids):
        raise ValueError(
            f"CSV 数据行数 {len(rows)} 少于 Agent 数量 {len(agent_ids)}，"
            "无法保证每个 Agent 至少获得一行数据"
        )

    base_size, remainder = divmod(len(rows), len(agent_ids))
    artifacts: dict[str, ArtifactRef] = {}
    partitions: dict[str, dict] = {}
    store = get_artifact_store()
    cursor = 0
    source_name = os.path.splitext(source_artifact.filename)[0]
    for index, agent_id in enumerate(agent_ids):
        shard_size = base_size + (1 if index < remainder else 0)
        shard_rows = rows[cursor:cursor + shard_size]
        row_start = cursor + 1
        cursor += shard_size

        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(shard_rows)
        shard_content = output.getvalue().encode("utf-8")
        artifact = store.put_bytes(
            kind="csv-shards",
            logical_id=f"{task_id}-{agent_id}",
            filename=f"{source_name}.part-{index + 1:03d}-of-{len(agent_ids):03d}.csv",
            content=shard_content,
        )
        artifacts[agent_id] = artifact
        partitions[agent_id] = {
            "mode": "shard",
            "shard_index": index,
            "shard_count": len(agent_ids),
            "row_start": row_start,
            "row_end": cursor,
            "row_count": shard_size,
            "source_sha256": source_artifact.sha256,
        }

    return artifacts, partitions, meta


def get_csv_preview(csv_id: str) -> Optional[dict]:
    meta = get_csv(csv_id)
    if not meta:
        return None
    return {
        "csv_id": csv_id,
        "filename": meta.get("filename"),
        "headers": meta.get("headers", []),
        "row_count": meta.get("row_count", 0),
        "preview": meta.get("preview", []),
    }
