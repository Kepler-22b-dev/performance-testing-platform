"""JMeter 脚本管理 API 模块。

提供 JMeter 脚本的上传、列表、搜索、查看、编辑、保存、删除以及
脚本结构解析和元素排序等接口，支持 .jmx 格式的性能测试脚本管理。
"""

import sys
import os
import time
import json
import uuid
import xml.etree.ElementTree as ET
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import SCRIPTS_DIR, REPORTS_DIR
from common.database import get_db
from manager.core.db import (
    db_get_all_scripts, db_get_script, db_create_script,
    db_update_script, db_delete_script, db_search_scripts,
    db_get_next_script_id,
)

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


class ScriptSaveRequest(BaseModel):
    content: str
    name: Optional[str] = None


@router.post("/upload")
async def upload_script(file: UploadFile = File(...), name: Optional[str] = None,
                        db: AsyncSession = Depends(get_db)):
    """上传 JMeter 脚本文件（.jmx），自动生成脚本 ID 并保存。"""
    if not file.filename.endswith(".jmx"):
        raise HTTPException(status_code=400, detail="Only .jmx files are supported")

    script_id_num = await db_get_next_script_id(db)
    script_id = str(script_id_num)
    original_name = file.filename
    filename = f"{script_id}.jmx"

    content = await file.read()
    content_str = content.decode("utf-8", errors="replace")

    await db_create_script(
        db, script_id=script_id, original_name=original_name,
        filename=filename, content=content_str, size=len(content),
    )

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    script_path = os.path.join(SCRIPTS_DIR, filename)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(content_str)

    return {
        "script_id": script_id,
        "name": original_name,
        "filename": filename,
        "size": len(content),
        "created_at": time.time(),
    }


@router.get("/")
async def list_scripts(db: AsyncSession = Depends(get_db)):
    """获取所有已上传脚本的列表，按修改时间倒序排列。"""
    scripts = await db_get_all_scripts(db)
    return {"total": len(scripts), "scripts": scripts}


@router.get("/search")
async def search_scripts(q: str = "", db: AsyncSession = Depends(get_db)):
    """根据关键词搜索脚本，支持文件名匹配和内容匹配。"""
    if not q:
        return {"results": []}

    scripts = await db_search_scripts(db, q)
    results = []
    q_lower = q.lower()

    for s in scripts:
        match_type = None
        matched_labels = []

        if q_lower in s["original_name"].lower() or q_lower in s["script_id"].lower():
            match_type = "name"

        if q_lower in s["content"].lower():
            match_type = "content"
            for line in s["content"].split("\n"):
                if 'testname="' in line:
                    start = line.find('testname="') + 10
                    end = line.find('"', start)
                    if end > start:
                        label = line[start:end]
                        if q_lower in label.lower():
                            matched_labels.append(label)

        if match_type:
            results.append({
                "script_id": s["script_id"],
                "filename": s["original_name"],
                "size": s["size"],
                "matched_labels": matched_labels[:10],
                "match_type": match_type,
            })

    return {"total": len(results), "results": results}


@router.get("/{script_id}")
async def get_script(script_id: str, db: AsyncSession = Depends(get_db)):
    """获取指定脚本的完整内容和元数据。"""
    script = await db_get_script(db, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    return {
        "script_id": script["script_id"],
        "filename": script["original_name"],
        "content": script["content"],
        "size": script["size"],
        "modified_at": script["modified_at"],
        "old_ids": [],
    }


@router.get("/{script_id}/structure")
async def get_script_structure(script_id: str, db: AsyncSession = Depends(get_db)):
    """解析并返回 JMX 脚本的元素结构树。"""
    script = await db_get_script(db, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        root = ET.fromstring(script["content"])
        elements = []
        _parse_jmx_tree(root, elements, depth=0)
        return {"script_id": script_id, "elements": elements}
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML解析错误: {str(e)}")


def _parse_jmx_tree(element, elements, depth):
    """递归解析 JMX XML 元素树，提取测试组件信息。"""
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
        elif "CSVDataSet" in testclass:
            elem_type = "csv_data_set"

        props = {}
        for child in element:
            child_tag = child.tag
            child_text = child.text or ""
            if child_tag in ("stringProp", "intProp", "boolProp", "longProp"):
                name = child.get("name", "")
                if name and child_text:
                    short_name = name.split(".")[-1] if "." in name else name
                    props[short_name] = child_text
            elif child_tag == "collectionProp":
                coll_name = child.get("name", "")
                items = []
                for item in child:
                    item_props = {}
                    item_props["type"] = item.tag
                    item_props["name"] = item.get("name", "")
                    for sub in item:
                        sub_text = sub.text or ""
                        if sub_text:
                            sub_name = sub.get("name", sub.tag)
                            item_props[sub_name] = sub_text
                        elif sub.tag == "elementProp":
                            sub_name = sub.get("name", "")
                            for subsub in sub:
                                subsub_text = subsub.text or ""
                                if subsub_text:
                                    subsub_name = subsub.get("name", subsub.tag)
                                    item_props[f"{sub_name}.{subsub_name}" if sub_name else subsub_name] = subsub_text
                    items.append(item_props)
                if items:
                    props[coll_name.split(".")[-1] if "." in coll_name else coll_name] = items
            elif child_tag == "elementProp":
                name = child.get("name", "")
                etype = child.get("elementType", "")
                if name:
                    sub_items = {}
                    # 递归查找 Argument.value
                    for sub in child:
                        sub_text = (sub.text or "").strip()
                        if sub_text:
                            sub_name = sub.get("name", sub.tag)
                            sub_items[sub_name] = sub_text
                        elif sub.tag == "elementProp":
                            inner_name = sub.get("name", "")
                            for subsub in sub:
                                subsub_text = (subsub.text or "").strip()
                                if subsub_text:
                                    subsub_name = subsub.get("name", subsub.tag)
                                    key = f"{inner_name}.{subsub_name}" if inner_name else subsub_name
                                    sub_items[key] = subsub_text
                                elif subsub.tag == "elementProp":
                                    inner2_name = subsub.get("name", "")
                                    for subsubsub in subsub:
                                        sss_text = (subsubsub.text or "").strip()
                                        if sss_text:
                                            sss_name = subsubsub.get("name", subsubsub.tag)
                                            key2 = f"{inner2_name}.{sss_name}" if inner2_name else sss_name
                                            sub_items[key2] = sss_text
                        elif sub.tag == "collectionProp":
                            # 从 collectionProp 中提取 Argument.value
                            for item in sub:
                                if item.tag == "elementProp":
                                    for subsub in item:
                                        sss_text = (subsub.text or "").strip()
                                        if sss_text:
                                            sss_name = subsub.get("name", subsub.tag)
                                            if sss_name == "Argument.value":
                                                sub_items["body"] = sss_text
                                            else:
                                                sub_items[sss_name] = sss_text
                    if sub_items:
                        props[name] = sub_items
                    elif child_text:
                        props[name] = child_text

        elements.append({
            "depth": depth,
            "type": elem_type,
            "testclass": testclass,
            "testname": testname,
            "tag": tag,
            "props": props,
        })

    for child in element:
        _parse_jmx_tree(child, elements, depth + 1)


@router.post("/{script_id}/save")
async def save_script_content(script_id: str, req: ScriptSaveRequest,
                              db: AsyncSession = Depends(get_db)):
    """保存脚本内容到指定脚本文件，会验证 XML 格式。"""
    script = await db_get_script(db, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        ET.fromstring(req.content)
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML格式错误: {str(e)}")

    await db_update_script(db, script_id, content=req.content, size=len(req.content.encode()))

    script_path = os.path.join(SCRIPTS_DIR, script["filename"])
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(req.content)

    return {
        "status": "saved",
        "script_id": script_id,
        "size": len(req.content.encode()),
        "modified_at": time.time(),
    }


@router.put("/{script_id}")
async def update_script(script_id: str, file: UploadFile = File(...),
                        db: AsyncSession = Depends(get_db)):
    """通过上传文件更新指定脚本的内容。"""
    script = await db_get_script(db, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    content = await file.read()
    content_str = content.decode("utf-8", errors="replace")
    await db_update_script(db, script_id, content=content_str, size=len(content))

    script_path = os.path.join(SCRIPTS_DIR, script["filename"])
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(content_str)

    return {"message": "Script updated", "size": len(content)}


@router.post("/create")
async def create_script_from_content(req: ScriptSaveRequest,
                                     db: AsyncSession = Depends(get_db)):
    """从提供的 XML 内容创建新的 JMeter 脚本。"""
    try:
        ET.fromstring(req.content)
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML格式错误: {str(e)}")

    script_id = f"script-{uuid.uuid4().hex[:8]}"
    filename = f"{script_id}.jmx"

    await db_create_script(
        db, script_id=script_id, original_name=filename,
        filename=filename, content=req.content, size=len(req.content.encode()),
    )

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    script_path = os.path.join(SCRIPTS_DIR, filename)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(req.content)

    return {
        "status": "created",
        "script_id": script_id,
        "filename": filename,
        "size": len(req.content.encode()),
        "created_at": time.time(),
    }


@router.delete("/{script_id}")
async def delete_script(script_id: str, db: AsyncSession = Depends(get_db)):
    """删除指定脚本。"""
    deleted = await db_delete_script(db, script_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Script not found")
    return {"message": "Script deleted"}


@router.get("/{script_id}/csv-hints")
async def get_csv_hints(script_id: str, db: AsyncSession = Depends(get_db)):
    """分析脚本内容，返回 CSV 参数化提示：JMX 中引用的 CSV 信息 + 匹配的已上传文件 + 历史使用记录。"""
    script = await db_get_script(db, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    jmx_csv_refs = []
    try:
        root = ET.fromstring(script["content"])
        for elem in root.iter("CSVDataSet"):
            filename = ""
            variable_names = ""
            delimiter = ","
            for child in elem:
                name = child.get("name", "")
                if name == "filename":
                    filename = child.text or ""
                elif name == "variableNames":
                    variable_names = child.text or ""
                elif name == "delimiter":
                    delimiter = child.text or ","
            if filename or variable_names:
                jmx_csv_refs.append({
                    "filename": filename,
                    "variable_names": variable_names,
                    "delimiter": delimiter,
                })
    except ET.ParseError:
        pass

    from manager.core.db import db_get_all_csvs
    all_csvs = await db_get_all_csvs(db)

    matched_csvs = []
    for csv_meta in all_csvs:
        csv_name = csv_meta.get("filename", "").lower()
        csv_id = csv_meta.get("csv_id", "")
        for ref in jmx_csv_refs:
            ref_name = ref["filename"].lower()
            if csv_name in ref_name or ref_name in csv_name or csv_id in ref_name:
                matched_csvs.append({
                    "csv_id": csv_id,
                    "filename": csv_meta.get("filename"),
                    "row_count": csv_meta.get("row_count", 0),
                    "matched_by": "filename",
                    "jmx_variable_names": ref["variable_names"],
                })
                break

    from common.config import REPORTS_DIR
    history_csvs = []
    if os.path.exists(REPORTS_DIR):
        csv_usage = {}
        for task_dir in os.listdir(REPORTS_DIR):
            task_path = os.path.join(REPORTS_DIR, task_dir)
            if not os.path.isdir(task_path):
                continue
            try:
                task_meta_path = os.path.join(task_path, "task.json")
                if os.path.exists(task_meta_path):
                    with open(task_meta_path) as f:
                        task_meta = json.load(f)
                else:
                    continue
            except Exception:
                continue
            if task_meta.get("script_id") != script_id:
                continue
            csv_file = task_meta.get("csv_file")
            if csv_file and csv_file not in csv_usage:
                csv_usage[csv_file] = {"csv_file": csv_file, "task_count": 0, "last_used": 0}
            if csv_file:
                csv_usage[csv_file]["task_count"] += 1
                created = task_meta.get("created_at", 0)
                if created > csv_usage[csv_file]["last_used"]:
                    csv_usage[csv_file]["last_used"] = created
        history_csvs = sorted(csv_usage.values(), key=lambda x: x["last_used"], reverse=True)

    return {
        "script_id": script_id,
        "jmx_csv_refs": jmx_csv_refs,
        "matched_csvs": matched_csvs,
        "history_csvs": history_csvs,
        "all_csvs": [{"csv_id": c.get("csv_id"), "filename": c.get("filename"), "row_count": c.get("row_count", 0)} for c in all_csvs],
    }


class ReorderRequest(BaseModel):
    from_index: int
    to_index: int


@router.post("/{script_id}/reorder")
async def reorder_elements(script_id: str, req: ReorderRequest,
                           db: AsyncSession = Depends(get_db)):
    """在同层级内拖拽排序 JMX 脚本中的测试元素。"""
    script = await db_get_script(db, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        root = ET.fromstring(script["content"])

        root_ht = root.find("hashTree") if root.tag == "jmeterTestPlan" else root
        children = list(root_ht)
        start_ht = children[1] if len(children) > 1 and children[1].tag == "hashTree" else root_ht

        def walk(ht, depth):
            result = []
            children = list(ht)
            i = 0
            while i < len(children):
                child = children[i]
                if child.tag != "hashTree":
                    tc = child.attrib.get("testclass", "")
                    tn = child.attrib.get("testname", "")
                    if tc:
                        next_ht = children[i + 1] if i + 1 < len(children) and children[i + 1].tag == "hashTree" else None
                        result.append({
                            "depth": depth,
                            "element": child,
                            "hashtree": ht,
                            "next_ht": next_ht,
                            "testclass": tc,
                            "testname": tn,
                        })
                        if next_ht is not None:
                            result.extend(walk(next_ht, depth + 1))
                i += 1
            return result

        elements = walk(start_ht, 0)

        if req.from_index < 0 or req.from_index >= len(elements):
            raise HTTPException(status_code=400, detail=f"Invalid from_index: {req.from_index}, max: {len(elements)-1}")
        if req.to_index < 0 or req.to_index >= len(elements):
            raise HTTPException(status_code=400, detail=f"Invalid to_index: {req.to_index}, max: {len(elements)-1}")

        from_el = elements[req.from_index]
        to_el = elements[req.to_index]

        if from_el["depth"] != to_el["depth"]:
            raise HTTPException(status_code=400, detail="只能在同一层级内拖拽排序")

        parent_ht = from_el["hashtree"]
        children = list(parent_ht)

        from_elem = from_el["element"]
        from_ht = from_el["next_ht"]
        to_elem = to_el["element"]

        from_pos = children.index(from_elem)
        to_pos = children.index(to_elem)

        parent_ht.remove(from_elem)
        if from_ht is not None:
            parent_ht.remove(from_ht)

        children = list(parent_ht)
        insert_pos = children.index(to_elem)

        parent_ht.insert(insert_pos, from_elem)
        if from_ht is not None:
            parent_ht.insert(insert_pos + 1, from_ht)

        from io import BytesIO
        tree = ET.ElementTree(root)
        buf = BytesIO()
        tree.write(buf, encoding="UTF-8", xml_declaration=True)
        new_content = buf.getvalue().decode("UTF-8")

        await db_update_script(db, script_id, content=new_content, size=len(new_content.encode()))

        return {"status": "reordered", "from": req.from_index, "to": req.to_index}
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML解析错误: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
