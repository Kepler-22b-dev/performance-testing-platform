import sys
import os
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from manager.core.variables import (
    get_all_vars, add_var, update_var, delete_var,
    get_all_csvs, upload_csv, get_csv, get_csv_data,
    delete_csv, get_csv_preview,
)

router = APIRouter(prefix="/api/data", tags=["data"])


class VarCreateRequest(BaseModel):
    name: str
    value: str
    description: str = ""
    scope: str = "global"


class VarUpdateRequest(BaseModel):
    name: Optional[str] = None
    value: Optional[str] = None
    description: Optional[str] = None


# ===== 变量 =====

@router.get("/vars")
def list_vars():
    return {"total": len(get_all_vars()), "vars": get_all_vars()}


@router.post("/vars")
def create_var(req: VarCreateRequest):
    result = add_var(name=req.name, value=req.value, description=req.description, scope=req.scope)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.put("/vars/{var_id}")
def update_var_endpoint(var_id: str, req: VarUpdateRequest):
    result = update_var(var_id, name=req.name, value=req.value, description=req.description)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.delete("/vars/{var_id}")
def delete_var_endpoint(var_id: str):
    result = delete_var(var_id)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


# ===== CSV =====

@router.get("/csv")
def list_csvs():
    return {"total": len(get_all_csvs()), "csvs": get_all_csvs()}


@router.post("/csv/upload")
async def upload_csv_endpoint(file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="只支持 .csv 文件")

    content = await file.read()
    result = upload_csv(filename=file.filename, content=content)
    return result


@router.get("/csv/{csv_id}")
def get_csv_info(csv_id: str):
    meta = get_csv(csv_id)
    if not meta:
        raise HTTPException(status_code=404, detail="CSV不存在")
    return meta


@router.get("/csv/{csv_id}/data")
def get_csv_data_endpoint(csv_id: str, offset: int = 0, limit: int = 100):
    result = get_csv_data(csv_id, offset=offset, limit=limit)
    if not result:
        raise HTTPException(status_code=404, detail="CSV不存在或读取失败")
    return result


@router.get("/csv/{csv_id}/preview")
def get_csv_preview_endpoint(csv_id: str):
    result = get_csv_preview(csv_id)
    if not result:
        raise HTTPException(status_code=404, detail="CSV不存在")
    return result


@router.delete("/csv/{csv_id}")
def delete_csv_endpoint(csv_id: str):
    result = delete_csv(csv_id)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result
