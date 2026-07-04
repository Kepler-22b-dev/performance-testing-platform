"""测试数据管理 API 模块。

提供全局变量的增删改查以及 CSV 数据文件的上传、预览、分页读取和删除接口，
用于管理压测过程中使用的参数化数据。
"""

import sys
import os
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from manager.core.variables import (
    get_all_vars, add_var, update_var, delete_var,
    get_all_csvs, upload_csv, get_csv, get_csv_data,
    delete_csv, get_csv_preview, get_csvs_page,
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
    """获取所有全局变量的列表。"""
    vars_list = get_all_vars()
    return {"total": len(vars_list), "vars": vars_list}


@router.post("/vars")
def create_var(req: VarCreateRequest):
    """创建一个新的全局变量。"""
    result = add_var(name=req.name, value=req.value, description=req.description, scope=req.scope)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.put("/vars/{var_id}")
def update_var_endpoint(var_id: str, req: VarUpdateRequest):
    """更新指定全局变量的值和描述。"""
    result = update_var(var_id, name=req.name, value=req.value, description=req.description)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.delete("/vars/{var_id}")
def delete_var_endpoint(var_id: str):
    """删除指定的全局变量。"""
    result = delete_var(var_id)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


# ===== CSV =====

@router.get("/csv")
def list_csvs(offset: int = 0, limit: int = 100):
    """获取所有已上传 CSV 文件的列表。"""
    offset = max(0, int(offset or 0))
    limit = max(1, min(500, int(limit or 100)))
    total, csvs_list = get_csvs_page(offset=offset, limit=limit)
    return {"total": total, "offset": offset, "limit": limit, "csvs": csvs_list}


@router.post("/csv/upload")
async def upload_csv_endpoint(file: UploadFile = File(...)):
    """上传 CSV 数据文件用于压测参数化。"""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="只支持 .csv 文件")

    content = await file.read()
    result = upload_csv(filename=file.filename, content=content)
    return result


@router.get("/csv/{csv_id}")
def get_csv_info(csv_id: str):
    """获取指定 CSV 文件的元数据信息。"""
    meta = get_csv(csv_id)
    if not meta:
        raise HTTPException(status_code=404, detail="CSV不存在")
    return meta


@router.get("/csv/{csv_id}/data")
def get_csv_data_endpoint(csv_id: str, offset: int = 0, limit: int = 100):
    """分页获取 CSV 文件的数据内容。"""
    result = get_csv_data(csv_id, offset=offset, limit=limit)
    if not result:
        raise HTTPException(status_code=404, detail="CSV不存在或读取失败")
    return result


@router.get("/csv/{csv_id}/preview")
def get_csv_preview_endpoint(csv_id: str):
    """预览 CSV 文件的前几行数据。"""
    result = get_csv_preview(csv_id)
    if not result:
        raise HTTPException(status_code=404, detail="CSV不存在")
    return result


@router.delete("/csv/{csv_id}")
def delete_csv_endpoint(csv_id: str):
    """删除指定的 CSV 数据文件。"""
    result = delete_csv(csv_id)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result
