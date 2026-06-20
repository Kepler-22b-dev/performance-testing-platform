"""JMeter 脚本管理 API 模块。

提供 JMeter 脚本的上传、列表、搜索、查看、编辑、保存、删除以及
脚本结构解析和元素排序等接口，支持 .jmx 格式的性能测试脚本管理。
"""

import sys
import os
import time
import json
import xml.etree.ElementTree as ET
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import SCRIPTS_DIR, REPORTS_DIR

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

COUNTER_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config", "script_counter.json",
)


class ScriptSaveRequest(BaseModel):
    content: str
    name: Optional[str] = None


def _get_next_script_id() -> int:
    """生成下一个自增的脚本 ID。"""
    os.makedirs(os.path.dirname(COUNTER_FILE), exist_ok=True)
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "r") as f:
            data = json.load(f)
        counter = data.get("counter", 0) + 1
    else:
        counter = 1
    with open(COUNTER_FILE, "w") as f:
        json.dump({"counter": counter}, f)
    return counter


@router.post("/upload")
async def upload_script(file: UploadFile = File(...), name: Optional[str] = None):
    """上传 JMeter 脚本文件（.jmx），自动生成脚本 ID 并保存元数据。"""
    if not file.filename.endswith(".jmx"):
        raise HTTPException(status_code=400, detail="Only .jmx files are supported")

    script_id_num = _get_next_script_id()
    script_id = str(script_id_num)
    original_name = file.filename
    filename = f"{script_id}.jmx"
    filepath = os.path.join(SCRIPTS_DIR, filename)

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    meta_path = os.path.join(SCRIPTS_DIR, f"{script_id}.json")
    with open(meta_path, "w") as f:
        json.dump({
            "script_id": script_id,
            "original_name": original_name,
            "filename": filename,
            "size": len(content),
            "created_at": time.time(),
        }, f)

    return {
        "script_id": script_id,
        "name": original_name,
        "filename": filename,
        "size": len(content),
        "created_at": time.time(),
    }


@router.get("/")
def list_scripts():
    """获取所有已上传脚本的列表，按修改时间倒序排列。"""
    scripts = []
    if os.path.exists(SCRIPTS_DIR):
        for f in os.listdir(SCRIPTS_DIR):
            if f.endswith(".jmx"):
                filepath = os.path.join(SCRIPTS_DIR, f)
                stat = os.stat(filepath)
                script_id = f.replace(".jmx", "")
                original_name = f
                meta_path = os.path.join(SCRIPTS_DIR, f"{script_id}.json")
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r") as mf:
                            meta = json.load(mf)
                        original_name = meta.get("original_name", f)
                    except Exception:
                        pass
                scripts.append({
                    "script_id": script_id,
                    "filename": original_name,
                    "size": stat.st_size,
                    "created_at": stat.st_ctime,
                    "modified_at": stat.st_mtime,
                })
    scripts.sort(key=lambda x: x.get("modified_at", 0), reverse=True)
    return {"total": len(scripts), "scripts": scripts}


@router.get("/search")
def search_scripts(q: str = ""):
    """根据关键词搜索脚本，支持文件名匹配和内容匹配。"""
    if not q:
        return {"results": []}

    results = []
    q_lower = q.lower()

    if os.path.exists(SCRIPTS_DIR):
        for f in os.listdir(SCRIPTS_DIR):
            if f.endswith(".jmx"):
                filepath = os.path.join(SCRIPTS_DIR, f)
                script_id = f.replace(".jmx", "")
                original_name = f
                meta_path = os.path.join(SCRIPTS_DIR, f"{script_id}.json")
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r") as mf:
                            meta = json.load(mf)
                        original_name = meta.get("original_name", f)
                    except Exception:
                        pass

                match_type = None
                matched_labels = []

                if q_lower in original_name.lower() or q_lower in script_id.lower():
                    match_type = "name"

                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as cf:
                        content = cf.read()
                    if q_lower in content.lower():
                        match_type = "content"
                        for line in content.split("\n"):
                            if 'testname="' in line:
                                start = line.find('testname="') + 10
                                end = line.find('"', start)
                                if end > start:
                                    label = line[start:end]
                                    if q_lower in label.lower():
                                        matched_labels.append(label)
                except Exception:
                    pass

                if match_type:
                    results.append({
                        "script_id": script_id,
                        "filename": original_name,
                        "size": os.path.getsize(filepath),
                        "matched_labels": matched_labels[:10],
                        "match_type": match_type,
                    })

    return {"total": len(results), "results": results}


@router.get("/{script_id}")
def get_script(script_id: str):
    """获取指定脚本的完整内容和元数据。"""
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    with open(filepath, "r") as f:
        content = f.read()

    stat = os.stat(filepath)
    filename = f"{script_id}.jmx"
    meta_path = os.path.join(SCRIPTS_DIR, f"{script_id}.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r") as mf:
                meta = json.load(mf)
            filename = meta.get("original_name", filename)
        except Exception:
            pass

    return {
        "script_id": script_id,
        "filename": filename,
        "content": content,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }


@router.get("/{script_id}/structure")
def get_script_structure(script_id: str):
    """解析并返回 JMX 脚本的元素结构树。"""
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
def save_script_content(script_id: str, req: ScriptSaveRequest):
    """保存脚本内容到指定脚本文件，会验证 XML 格式。"""
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
    """通过上传文件更新指定脚本的内容。"""
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    return {"message": "Script updated", "size": len(content)}


@router.post("/create")
def create_script_from_content(req: ScriptSaveRequest):
    """从提供的 XML 内容创建新的 JMeter 脚本。"""
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
    """删除指定脚本及其元数据文件。"""
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    os.remove(filepath)
    meta_path = os.path.join(SCRIPTS_DIR, f"{script_id}.json")
    if os.path.exists(meta_path):
        os.remove(meta_path)
    return {"message": "Script deleted"}


class ReorderRequest(BaseModel):
    from_index: int
    to_index: int


@router.post("/{script_id}/reorder")
def reorder_elements(script_id: str, req: ReorderRequest):
    """在同层级内拖拽排序 JMX 脚本中的测试元素。"""
    filepath = os.path.join(SCRIPTS_DIR, f"{script_id}.jmx")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        tree = ET.parse(filepath)
        root = tree.getroot()

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

        tree.write(filepath, encoding="UTF-8", xml_declaration=True)

        return {"status": "reordered", "from": req.from_index, "to": req.to_index}
    except ET.ParseError as e:
        raise HTTPException(status_code=400, detail=f"XML解析错误: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
