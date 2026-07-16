"""变量与 CSV 数据文件管理模块。"""

import sys
import os
import json
import time
import csv
import io
import re
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import SCRIPTS_DIR
from common.database import get_sync_db
from manager.core.db_sync import (
    db_get_all_vars, db_create_var, db_update_var, db_delete_var,
    db_get_all_csvs, db_get_csv, db_create_csv, db_delete_csv,
    db_get_csvs_page,
)

CSV_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config", "csv",
)
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _ensure_dirs():
    os.makedirs(CSV_DIR, exist_ok=True)


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

    csv_id = f"csv-{int(time.time()*1000)}"
    safe_name = filename.replace(" ", "_").replace("/", "_")
    filepath = os.path.join(CSV_DIR, f"{csv_id}_{safe_name}")

    with open(filepath, "wb") as f:
        f.write(content)

    headers = []
    preview = []
    row_count = 0

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk", errors="replace")

    try:
        reader = csv.reader(io.StringIO(text))
        for i, row in enumerate(reader):
            if i == 0:
                headers = row
            elif i <= 5:
                preview.append(row)
            row_count = i
    except Exception:
        pass

    meta = {
        "csv_id": csv_id,
        "filename": filename,
        "filepath": filepath,
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

    return {"status": "uploaded", "csv": meta}


def get_all_csvs() -> list:
    db = get_sync_db()
    try:
        csvs = db_get_all_csvs(db)
        return [c for c in csvs if os.path.exists(c.get("filepath", ""))]
    finally:
        db.close()


def get_csvs_page(offset: int = 0, limit: int = 100) -> tuple[int, list]:
    db = get_sync_db()
    try:
        total, csvs = db_get_csvs_page(db, offset=offset, limit=limit)
        return total, [c for c in csvs if os.path.exists(c.get("filepath", ""))]
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

    filepath = meta.get("filepath", "")
    if not os.path.exists(filepath):
        return None

    headers = []
    rows = []
    total = 0

    try:
        encodings = ["utf-8", "gbk", "latin-1"]
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

        reader = csv.reader(f)
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
