"""
同步数据库操作层 - 提供各模块的 CRUD 操作（同步版本）
用于调度器等同步代码路径
"""
import time
from typing import Optional
from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import Session

from manager.models.db_models import (
    Script, Task, TaskResult, Variable, CsvFile,
    Template, Environment, AlertRule, Schedule,
    Notification, NodeRegistry, ScriptCounter,
)


def _to_dict(obj) -> dict:
    """将 ORM 对象或 Row 转换为字典"""
    if obj is None:
        return None
    # Row 对象（select 返回）：取第一个元素
    if hasattr(obj, '_mapping'):
        mapping = dict(obj._mapping)
        if len(mapping) == 1:
            inner = list(mapping.values())[0]
            if hasattr(inner, '__table__'):
                return {c.key: getattr(inner, c.key) for c in inner.__table__.columns}
        return mapping
    # ORM 对象
    if hasattr(obj, '__table__'):
        return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}
    return dict(obj)


# ===== 脚本 =====

def db_get_all_scripts(db: Session) -> list:
    result = db.execute(select(Script).order_by(Script.modified_at.desc()))
    return [_to_dict(r) for r in result.all()]


def db_get_scripts_page(db: Session, offset: int = 0, limit: int = 100, include_content: bool = False) -> tuple[int, list]:
    total = db.scalar(select(func.count()).select_from(Script)) or 0
    query = select(Script).order_by(Script.modified_at.desc()).offset(offset).limit(limit)
    rows = [_to_dict(r) for r in db.execute(query).scalars().all()]
    if not include_content:
        for row in rows:
            row.pop("content", None)
    return total, rows


def db_get_script(db: Session, script_id: str) -> Optional[dict]:
    result = db.execute(select(Script).where(Script.script_id == script_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


def db_create_script(db: Session, script_id: str, original_name: str,
                     filename: str, content: str, size: int) -> dict:
    now = time.time()
    script = Script(
        script_id=script_id, original_name=original_name,
        filename=filename, content=content, size=size,
        created_at=now, modified_at=now,
    )
    db.add(script)
    db.commit()
    return _to_dict(script)


def db_update_script(db: Session, script_id: str, content: str, size: int) -> bool:
    result = db.execute(
        update(Script).where(Script.script_id == script_id)
        .values(content=content, size=size, modified_at=time.time())
    )
    db.commit()
    return result.rowcount > 0


def db_delete_script(db: Session, script_id: str) -> bool:
    result = db.execute(delete(Script).where(Script.script_id == script_id))
    db.commit()
    return result.rowcount > 0


def db_search_scripts(db: Session, query: str) -> list:
    q = f"%{query}%"
    result = db.execute(
        select(Script).where(
            Script.original_name.ilike(q) | Script.content.ilike(q)
        )
    )
    return [_to_dict(r) for r in result.all()]


def db_get_next_script_id(db: Session) -> int:
    result = db.execute(select(ScriptCounter).where(ScriptCounter.id == 1))
    row = result.scalar_one_or_none()
    if row:
        new_counter = row.counter + 1
        row.counter = new_counter
    else:
        new_counter = 1
        db.add(ScriptCounter(id=1, counter=1))
    db.commit()
    return new_counter


# ===== 任务 =====

def db_create_task(db: Session, task_data: dict) -> None:
    task = Task(**task_data)
    db.add(task)
    db.commit()


def db_get_task(db: Session, task_id: str) -> Optional[dict]:
    result = db.execute(select(Task).where(Task.task_id == task_id))
    row = result.scalar_one_or_none()
    if not row:
        return None
    task = _to_dict(row)
    # 加载关联的结果
    results = db_get_task_results(db, task_id)
    task["results"] = {r["agent_id"]: r for r in results}
    return task


def db_get_running_tasks(db: Session) -> list:
    result = db.execute(
        select(Task, TaskResult)
        .outerjoin(TaskResult, Task.task_id == TaskResult.task_id)
        .where(Task.status == "running")
        .order_by(Task.created_at.desc())
    )
    tasks_map = {}
    for task_row, result_row in result.all():
        task_dict = _to_dict(task_row)
        task_id = task_dict["task_id"]
        if task_id not in tasks_map:
            task_dict["results"] = {}
            tasks_map[task_id] = task_dict
        if result_row:
            r = _to_dict(result_row)
            tasks_map[task_id]["results"][r["agent_id"]] = r
    return list(tasks_map.values())


def db_get_all_tasks(db: Session) -> list:
    result = db.execute(
        select(Task, TaskResult)
        .outerjoin(TaskResult, Task.task_id == TaskResult.task_id)
        .order_by(Task.created_at.desc())
    )
    tasks_map = {}
    for task_row, result_row in result.all():
        task_dict = _to_dict(task_row)
        task_id = task_dict["task_id"]
        if task_id not in tasks_map:
            task_dict["results"] = {}
            tasks_map[task_id] = task_dict
        if result_row:
            r = _to_dict(result_row)
            tasks_map[task_id]["results"][r["agent_id"]] = r
    return list(tasks_map.values())


def db_get_tasks_page(db: Session, offset: int = 0, limit: int = 100, status: str = None) -> tuple[int, list]:
    base = select(Task)
    count_query = select(func.count()).select_from(Task)
    if status:
        base = base.where(Task.status == status)
        count_query = count_query.where(Task.status == status)

    total = db.scalar(count_query) or 0
    task_rows = db.execute(
        base.order_by(Task.created_at.desc()).offset(offset).limit(limit)
    ).scalars().all()
    tasks = [_to_dict(row) for row in task_rows]
    task_ids = [t["task_id"] for t in tasks]
    for task in tasks:
        task["results"] = {}

    if task_ids:
        result_rows = db.execute(
            select(TaskResult).where(TaskResult.task_id.in_(task_ids))
        ).scalars().all()
        task_map = {t["task_id"]: t for t in tasks}
        for result_row in result_rows:
            result_dict = _to_dict(result_row)
            task = task_map.get(result_dict["task_id"])
            if task is not None:
                task["results"][result_dict["agent_id"]] = result_dict
    return total, tasks


def db_update_task(db: Session, task_id: str, **kwargs) -> bool:
    result = db.execute(
        update(Task).where(Task.task_id == task_id).values(**kwargs)
    )
    db.commit()
    return result.rowcount > 0


def db_delete_task(db: Session, task_id: str) -> bool:
    db.execute(delete(TaskResult).where(TaskResult.task_id == task_id))
    result = db.execute(delete(Task).where(Task.task_id == task_id))
    db.commit()
    return result.rowcount > 0


# ===== 任务结果 =====

def db_add_task_result(db: Session, task_id: str, agent_id: str,
                        status: str, start_time: float = None,
                        end_time: float = None, report_path: str = None,
                        error_message: str = None, summary: dict = None) -> None:
    result = TaskResult(
        task_id=task_id, agent_id=agent_id, status=status,
        start_time=start_time, end_time=end_time,
        report_path=report_path, error_message=error_message,
        summary=summary or {},
    )
    db.add(result)
    db.commit()


def db_update_task_result(db: Session, task_id: str, agent_id: str, **kwargs) -> bool:
    result = db.execute(
        update(TaskResult).where(
            TaskResult.task_id == task_id, TaskResult.agent_id == agent_id
        ).values(**kwargs)
    )
    db.commit()
    return result.rowcount > 0


def db_get_task_results(db: Session, task_id: str) -> list:
    result = db.execute(
        select(TaskResult).where(TaskResult.task_id == task_id)
    )
    return [_to_dict(r) for r in result.all()]


# ===== 变量 =====

def db_get_all_vars(db: Session) -> list:
    result = db.execute(select(Variable).order_by(Variable.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


def db_create_var(db: Session, var_data: dict) -> dict:
    var = Variable(**var_data)
    db.add(var)
    db.commit()
    return _to_dict(var)


def db_update_var(db: Session, var_id: str, **kwargs) -> bool:
    kwargs["updated_at"] = time.time()
    result = db.execute(
        update(Variable).where(Variable.id == var_id).values(**kwargs)
    )
    db.commit()
    return result.rowcount > 0


def db_delete_var(db: Session, var_id: str) -> bool:
    result = db.execute(delete(Variable).where(Variable.id == var_id))
    db.commit()
    return result.rowcount > 0


# ===== CSV =====

def db_get_all_csvs(db: Session) -> list:
    result = db.execute(select(CsvFile).order_by(CsvFile.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


def db_get_csvs_page(db: Session, offset: int = 0, limit: int = 100) -> tuple[int, list]:
    total = db.scalar(select(func.count()).select_from(CsvFile)) or 0
    result = db.execute(
        select(CsvFile).order_by(CsvFile.created_at.desc()).offset(offset).limit(limit)
    )
    return total, [_to_dict(r) for r in result.scalars().all()]


def db_get_csv(db: Session, csv_id: str) -> Optional[dict]:
    result = db.execute(select(CsvFile).where(CsvFile.csv_id == csv_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


def db_create_csv(db: Session, csv_data: dict) -> dict:
    csv = CsvFile(**csv_data)
    db.add(csv)
    db.commit()
    return _to_dict(csv)


def db_delete_csv(db: Session, csv_id: str) -> bool:
    result = db.execute(delete(CsvFile).where(CsvFile.csv_id == csv_id))
    db.commit()
    return result.rowcount > 0


# ===== 模板 =====

def db_get_all_templates(db: Session) -> list:
    result = db.execute(select(Template))
    return [_to_dict(r) for r in result.all()]


def db_create_template(db: Session, template_data: dict) -> dict:
    tpl = Template(**template_data)
    db.add(tpl)
    db.commit()
    return _to_dict(tpl)


def db_delete_template(db: Session, template_id: str) -> bool:
    result = db.execute(delete(Template).where(Template.template_id == template_id))
    db.commit()
    return result.rowcount > 0


# ===== 环境 =====

def db_get_all_environments(db: Session) -> list:
    result = db.execute(select(Environment).order_by(Environment.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


def db_get_environment(db: Session, env_id: str) -> Optional[dict]:
    result = db.execute(select(Environment).where(Environment.env_id == env_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


def db_create_environment(db: Session, env_data: dict) -> dict:
    env = Environment(**env_data)
    db.add(env)
    db.commit()
    return _to_dict(env)


def db_update_environment(db: Session, env_id: str, **kwargs) -> bool:
    kwargs["updated_at"] = time.time()
    result = db.execute(
        update(Environment).where(Environment.env_id == env_id).values(**kwargs)
    )
    db.commit()
    return result.rowcount > 0


def db_delete_environment(db: Session, env_id: str) -> bool:
    result = db.execute(delete(Environment).where(Environment.env_id == env_id))
    db.commit()
    return result.rowcount > 0


# ===== 告警规则 =====

def db_get_all_alert_rules(db: Session) -> list:
    result = db.execute(select(AlertRule).order_by(AlertRule.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


def db_create_alert_rule(db: Session, rule_data: dict) -> dict:
    rule = AlertRule(**rule_data)
    db.add(rule)
    db.commit()
    return _to_dict(rule)


def db_update_alert_rule(db: Session, rule_id: str, **kwargs) -> bool:
    result = db.execute(
        update(AlertRule).where(AlertRule.rule_id == rule_id).values(**kwargs)
    )
    db.commit()
    return result.rowcount > 0


def db_delete_alert_rule(db: Session, rule_id: str) -> bool:
    result = db.execute(delete(AlertRule).where(AlertRule.rule_id == rule_id))
    db.commit()
    return result.rowcount > 0


# ===== 调度 =====

def db_get_all_schedules(db: Session) -> list:
    result = db.execute(select(Schedule).order_by(Schedule.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


def db_get_schedule(db: Session, schedule_id: str) -> Optional[dict]:
    result = db.execute(select(Schedule).where(Schedule.schedule_id == schedule_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


def db_create_schedule(db: Session, schedule_data: dict) -> dict:
    sched = Schedule(**schedule_data)
    db.add(sched)
    db.commit()
    return _to_dict(sched)


def db_update_schedule(db: Session, schedule_id: str, **kwargs) -> bool:
    result = db.execute(
        update(Schedule).where(Schedule.schedule_id == schedule_id).values(**kwargs)
    )
    db.commit()
    return result.rowcount > 0


def db_delete_schedule(db: Session, schedule_id: str) -> bool:
    result = db.execute(delete(Schedule).where(Schedule.schedule_id == schedule_id))
    db.commit()
    return result.rowcount > 0


# ===== 通知 =====

def db_get_notification_config(db: Session) -> dict:
    result = db.execute(select(Notification).limit(1))
    row = result.scalar_one_or_none()
    if row:
        return {"enabled": row.enabled, "webhooks": row.webhooks or []}
    return {"enabled": True, "webhooks": []}


def db_save_notification_config(db: Session, config: dict) -> None:
    result = db.execute(select(Notification).limit(1))
    row = result.scalar_one_or_none()
    if row:
        row.enabled = config.get("enabled", True)
        row.webhooks = config.get("webhooks", [])
    else:
        db.add(Notification(
            enabled=config.get("enabled", True),
            webhooks=config.get("webhooks", []),
        ))
    db.commit()


# ===== 节点注册 =====

def db_get_all_nodes(db: Session) -> list:
    result = db.execute(select(NodeRegistry).order_by(NodeRegistry.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


def db_get_node(db: Session, node_id: str) -> Optional[dict]:
    result = db.execute(select(NodeRegistry).where(NodeRegistry.node_id == node_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


def db_create_node(db: Session, node_data: dict) -> dict:
    node = NodeRegistry(**node_data)
    db.add(node)
    db.commit()
    return _to_dict(node)


def db_update_node(db: Session, node_id: str, **kwargs) -> bool:
    result = db.execute(
        update(NodeRegistry).where(NodeRegistry.node_id == node_id).values(**kwargs)
    )
    db.commit()
    return result.rowcount > 0


def db_delete_node(db: Session, node_id: str) -> bool:
    result = db.execute(delete(NodeRegistry).where(NodeRegistry.node_id == node_id))
    db.commit()
    return result.rowcount > 0
