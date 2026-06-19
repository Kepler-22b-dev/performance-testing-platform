import sys
import os
import json
import time
import csv
import io
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import SCRIPTS_DIR

VARS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config", "variables.json",
)

CSV_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config", "csv",
)


def _ensure_dirs():
    os.makedirs(os.path.dirname(VARS_FILE), exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)


def _load_vars() -> list:
    if not os.path.exists(VARS_FILE):
        return []
    with open(VARS_FILE, "r") as f:
        return json.load(f)


def _save_vars(vars_list: list):
    _ensure_dirs()
    with open(VARS_FILE, "w") as f:
        json.dump(vars_list, f, indent=2, ensure_ascii=False)


# ===== 变量管理 =====

def get_all_vars() -> list:
    return _load_vars()


def add_var(name: str, value: str, description: str = "", scope: str = "global") -> dict:
    vars_list = _load_vars()

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

    vars_list.append(var)
    _save_vars(vars_list)
    return {"status": "added", "var": var}


def update_var(var_id: str, name: str = None, value: str = None, description: str = None) -> dict:
    vars_list = _load_vars()

    for v in vars_list:
        if v["id"] == var_id:
            if name is not None:
                v["name"] = name
            if value is not None:
                v["value"] = value
            if description is not None:
                v["description"] = description
            v["updated_at"] = time.time()
            _save_vars(vars_list)
            return {"status": "updated", "var": v}

    return {"status": "error", "message": f"变量不存在: {var_id}"}


def delete_var(var_id: str) -> dict:
    vars_list = _load_vars()
    original_len = len(vars_list)
    vars_list = [v for v in vars_list if v["id"] != var_id]

    if len(vars_list) == original_len:
        return {"status": "error", "message": f"变量不存在: {var_id}"}

    _save_vars(vars_list)
    return {"status": "deleted", "var_id": var_id}


def get_vars_dict() -> dict:
    vars_list = _load_vars()
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

    meta_path = os.path.join(CSV_DIR, f"{csv_id}.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return {"status": "uploaded", "csv": meta}


def get_all_csvs() -> list:
    _ensure_dirs()
    csvs = []
    for f in os.listdir(CSV_DIR):
        if f.endswith(".json"):
            meta_path = os.path.join(CSV_DIR, f)
            try:
                with open(meta_path, "r") as meta_f:
                    meta = json.load(meta_f)
                if os.path.exists(meta.get("filepath", "")):
                    csvs.append(meta)
            except Exception:
                pass
    csvs.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return csvs


def get_csv(csv_id: str) -> Optional[dict]:
    meta_path = os.path.join(CSV_DIR, f"{csv_id}.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path, "r") as f:
        return json.load(f)


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

    meta_path = os.path.join(CSV_DIR, f"{csv_id}.json")
    if os.path.exists(meta_path):
        os.remove(meta_path)

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
