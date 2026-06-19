import sys
import os
import time
import uuid
import xml.etree.ElementTree as ET
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import SCRIPTS_DIR, REPORTS_DIR

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


class ScriptSaveRequest(BaseModel):
    content: str
    name: Optional[str] = None


@router.post("/upload")
async def upload_script(file: UploadFile = File(...), name: Optional[str] = None):
    if not file.filename.endswith(".jmx"):
        raise HTTPException(status_code=400, detail="Only .jmx files are supported")

    script_id = f"script-{uuid.uuid4().hex[:8]}"
    filename = f"{script_id}.jmx"
    filepath = os.path.join(SCRIPTS_DIR, filename)

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    return {
        "script_id": script_id,
        "name": name or file.filename,
        "filename": filename,
        "size": len(content),
        "created_at": time.time(),
    }


@router.get("/")
def list_scripts():
    scripts = []
    if os.path.exists(SCRIPTS_DIR):
        for f in os.listdir(SCRIPTS_DIR):
            if f.endswith(".jmx"):
                filepath = os.path.join(SCRIPTS_DIR, f)
                stat = os.stat(filepath)
                script_id = f.replace(".jmx", "")
                scripts.append({
                    "script_id": script_id,
                    "filename": f,
                    "size": stat.st_size,
                    "created_at": stat.st_ctime,
                    "modified_at": stat.st_mtime,
                })
    return {"total": len(scripts), "scripts": scripts}


@router.get("/{script_id}")
def get_script(script_id: str):
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    with open(filepath, "r") as f:
        content = f.read()

    stat = os.stat(filepath)

    return {
        "script_id": script_id,
        "content": content,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }


@router.get("/{script_id}/structure")
def get_script_structure(script_id: str):
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        tree = ET.parse(filepath)
        root = tree.getroot()

        elements = []
        _parse_jmx_tree(root, elements, depth=0)

        return {"script_id": script_id, "elements": elements}
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML解析错误: {str(e)}")


def _parse_jmx_tree(element, elements, depth):
    tag = element.tag
    attrs = element.attrib

    testclass = attrs.get("testclass", "")
    testname = attrs.get("testname", "")

    if testclass and testname:
        elem_type = "unknown"
        if "ThreadGroup" in testclass:
            elem_type = "thread_group"
        elif "HTTPSampler" in testclass:
            elem_type = "http_sampler"
        elif "TestPlan" in testclass:
            elem_type = "test_plan"
        elif "LoopController" in testclass:
            elem_type = "loop_controller"
        elif "Arguments" in testclass:
            elem_type = "arguments"
        elif "HeaderManager" in testclass:
            elem_type = "header_manager"
        elif "Timer" in testclass or "timer" in testclass.lower():
            elem_type = "timer"
        elif "Assertion" in testclass or "assertion" in testclass.lower():
            elem_type = "assertion"
        elif "Extractor" in testclass or "extractor" in testclass.lower():
            elem_type = "extractor"
        elif "Listener" in testclass or "listener" in testclass.lower():
            elem_type = "listener"

        elements.append({
            "depth": depth,
            "type": elem_type,
            "testclass": testclass,
            "testname": testname,
            "tag": tag,
        })

    for child in element:
        _parse_jmx_tree(child, elements, depth + 1)


@router.post("/{script_id}/save")
def save_script_content(script_id: str, req: ScriptSaveRequest):
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        ET.fromstring(req.content)
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML格式错误: {str(e)}")

    with open(filepath, "w") as f:
        f.write(req.content)

    stat = os.stat(filepath)
    return {
        "status": "saved",
        "script_id": script_id,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }


@router.put("/{script_id}")
async def update_script(script_id: str, file: UploadFile = File(...)):
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    return {"message": "Script updated", "size": len(content)}


@router.post("/create")
def create_script_from_content(req: ScriptSaveRequest):
    try:
        ET.fromstring(req.content)
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML格式错误: {str(e)}")

    script_id = f"script-{uuid.uuid4().hex[:8]}"
    filename = f"{script_id}.jmx"
    filepath = os.path.join(SCRIPTS_DIR, filename)

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(req.content)

    stat = os.stat(filepath)
    return {
        "status": "created",
        "script_id": script_id,
        "filename": filename,
        "size": stat.st_size,
        "created_at": stat.st_ctime,
    }


@router.delete("/{script_id}")
def delete_script(script_id: str):
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    os.remove(filepath)
    return {"message": "Script deleted"}
