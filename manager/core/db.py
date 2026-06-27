"""
数据库操作层 - 提供各模块的 CRUD 操作
"""
import time
from typing import Optional
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

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

async def db_get_all_scripts(db: AsyncSession) -> list:
    result = await db.execute(select(Script).order_by(Script.modified_at.desc()))
    return [_to_dict(r) for r in result.all()]


async def db_get_script(db: AsyncSession, script_id: str) -> Optional[dict]:
    result = await db.execute(select(Script).where(Script.script_id == script_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


async def db_create_script(db: AsyncSession, script_id: str, original_name: str,
                           filename: str, content: str, size: int) -> dict:
    now = time.time()
    script = Script(
        script_id=script_id, original_name=original_name,
        filename=filename, content=content, size=size,
        created_at=now, modified_at=now,
    )
    db.add(script)
    await db.commit()
    return _to_dict(script)


async def db_update_script(db: AsyncSession, script_id: str, content: str, size: int) -> bool:
    result = await db.execute(
        update(Script).where(Script.script_id == script_id)
        .values(content=content, size=size, modified_at=time.time())
    )
    await db.commit()
    return result.rowcount > 0


async def db_delete_script(db: AsyncSession, script_id: str) -> bool:
    result = await db.execute(delete(Script).where(Script.script_id == script_id))
    await db.commit()
    return result.rowcount > 0


async def db_search_scripts(db: AsyncSession, query: str) -> list:
    q = f"%{query}%"
    result = await db.execute(
        select(Script).where(
            Script.original_name.ilike(q) | Script.content.ilike(q)
        )
    )
    return [_to_dict(r) for r in result.all()]


async def db_get_next_script_id(db: AsyncSession) -> int:
    result = await db.execute(select(ScriptCounter).where(ScriptCounter.id == 1))
    row = result.scalar_one_or_none()
    if row:
        new_counter = row.counter + 1
        row.counter = new_counter
    else:
        new_counter = 1
        db.add(ScriptCounter(id=1, counter=1))
    await db.commit()
    return new_counter


# ===== 任务 =====

async def db_create_task(db: AsyncSession, task_data: dict) -> None:
    task = Task(**task_data)
    db.add(task)
    await db.commit()


async def db_get_task(db: AsyncSession, task_id: str) -> Optional[dict]:
    result = await db.execute(select(Task).where(Task.task_id == task_id))
    row = result.scalar_one_or_none()
    if not row:
        return None
    task = _to_dict(row)
    # 加载关联的结果
    results = await db_get_task_results(db, task_id)
    task["results"] = {r["agent_id"]: r for r in results}
    return task


async def db_get_all_tasks(db: AsyncSession) -> list:
    result = await db.execute(
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


async def db_update_task(db: AsyncSession, task_id: str, **kwargs) -> bool:
    result = await db.execute(
        update(Task).where(Task.task_id == task_id).values(**kwargs)
    )
    await db.commit()
    return result.rowcount > 0


async def db_delete_task(db: AsyncSession, task_id: str) -> bool:
    await db.execute(delete(TaskResult).where(TaskResult.task_id == task_id))
    result = await db.execute(delete(Task).where(Task.task_id == task_id))
    await db.commit()
    return result.rowcount > 0


# ===== 任务结果 =====

async def db_add_task_result(db: AsyncSession, task_id: str, agent_id: str,
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
    await db.commit()


async def db_update_task_result(db: AsyncSession, task_id: str, agent_id: str, **kwargs) -> bool:
    result = await db.execute(
        update(TaskResult).where(
            TaskResult.task_id == task_id, TaskResult.agent_id == agent_id
        ).values(**kwargs)
    )
    await db.commit()
    return result.rowcount > 0


async def db_get_task_results(db: AsyncSession, task_id: str) -> list:
    result = await db.execute(
        select(TaskResult).where(TaskResult.task_id == task_id)
    )
    return [_to_dict(r) for r in result.all()]


# ===== 变量 =====

async def db_get_all_vars(db: AsyncSession) -> list:
    result = await db.execute(select(Variable).order_by(Variable.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


async def db_create_var(db: AsyncSession, var_data: dict) -> dict:
    var = Variable(**var_data)
    db.add(var)
    await db.commit()
    return _to_dict(var)


async def db_update_var(db: AsyncSession, var_id: str, **kwargs) -> bool:
    kwargs["updated_at"] = time.time()
    result = await db.execute(
        update(Variable).where(Variable.id == var_id).values(**kwargs)
    )
    await db.commit()
    return result.rowcount > 0


async def db_delete_var(db: AsyncSession, var_id: str) -> bool:
    result = await db.execute(delete(Variable).where(Variable.id == var_id))
    await db.commit()
    return result.rowcount > 0


# ===== CSV =====

async def db_get_all_csvs(db: AsyncSession) -> list:
    result = await db.execute(select(CsvFile).order_by(CsvFile.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


async def db_get_csv(db: AsyncSession, csv_id: str) -> Optional[dict]:
    result = await db.execute(select(CsvFile).where(CsvFile.csv_id == csv_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


async def db_create_csv(db: AsyncSession, csv_data: dict) -> dict:
    csv = CsvFile(**csv_data)
    db.add(csv)
    await db.commit()
    return _to_dict(csv)


async def db_delete_csv(db: AsyncSession, csv_id: str) -> bool:
    result = await db.execute(delete(CsvFile).where(CsvFile.csv_id == csv_id))
    await db.commit()
    return result.rowcount > 0


# ===== 模板 =====

async def db_get_all_templates(db: AsyncSession) -> list:
    result = await db.execute(select(Template))
    return [_to_dict(r) for r in result.all()]


async def db_create_template(db: AsyncSession, template_data: dict) -> dict:
    tpl = Template(**template_data)
    db.add(tpl)
    await db.commit()
    return _to_dict(tpl)


async def db_delete_template(db: AsyncSession, template_id: str) -> bool:
    result = await db.execute(delete(Template).where(Template.template_id == template_id))
    await db.commit()
    return result.rowcount > 0


# ===== 环境 =====

async def db_get_all_environments(db: AsyncSession) -> list:
    result = await db.execute(select(Environment).order_by(Environment.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


async def db_get_environment(db: AsyncSession, env_id: str) -> Optional[dict]:
    result = await db.execute(select(Environment).where(Environment.env_id == env_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


async def db_create_environment(db: AsyncSession, env_data: dict) -> dict:
    env = Environment(**env_data)
    db.add(env)
    await db.commit()
    return _to_dict(env)


async def db_update_environment(db: AsyncSession, env_id: str, **kwargs) -> bool:
    kwargs["updated_at"] = time.time()
    result = await db.execute(
        update(Environment).where(Environment.env_id == env_id).values(**kwargs)
    )
    await db.commit()
    return result.rowcount > 0


async def db_delete_environment(db: AsyncSession, env_id: str) -> bool:
    result = await db.execute(delete(Environment).where(Environment.env_id == env_id))
    await db.commit()
    return result.rowcount > 0


# ===== 告警规则 =====

async def db_get_all_alert_rules(db: AsyncSession) -> list:
    result = await db.execute(select(AlertRule).order_by(AlertRule.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


async def db_create_alert_rule(db: AsyncSession, rule_data: dict) -> dict:
    rule = AlertRule(**rule_data)
    db.add(rule)
    await db.commit()
    return _to_dict(rule)


async def db_update_alert_rule(db: AsyncSession, rule_id: str, **kwargs) -> bool:
    result = await db.execute(
        update(AlertRule).where(AlertRule.rule_id == rule_id).values(**kwargs)
    )
    await db.commit()
    return result.rowcount > 0


async def db_delete_alert_rule(db: AsyncSession, rule_id: str) -> bool:
    result = await db.execute(delete(AlertRule).where(AlertRule.rule_id == rule_id))
    await db.commit()
    return result.rowcount > 0


# ===== 调度 =====

async def db_get_all_schedules(db: AsyncSession) -> list:
    result = await db.execute(select(Schedule).order_by(Schedule.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


async def db_get_schedule(db: AsyncSession, schedule_id: str) -> Optional[dict]:
    result = await db.execute(select(Schedule).where(Schedule.schedule_id == schedule_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


async def db_create_schedule(db: AsyncSession, schedule_data: dict) -> dict:
    sched = Schedule(**schedule_data)
    db.add(sched)
    await db.commit()
    return _to_dict(sched)


async def db_update_schedule(db: AsyncSession, schedule_id: str, **kwargs) -> bool:
    result = await db.execute(
        update(Schedule).where(Schedule.schedule_id == schedule_id).values(**kwargs)
    )
    await db.commit()
    return result.rowcount > 0


async def db_delete_schedule(db: AsyncSession, schedule_id: str) -> bool:
    result = await db.execute(delete(Schedule).where(Schedule.schedule_id == schedule_id))
    await db.commit()
    return result.rowcount > 0


# ===== 通知 =====

async def db_get_notification_config(db: AsyncSession) -> dict:
    result = await db.execute(select(Notification).limit(1))
    row = result.scalar_one_or_none()
    if row:
        return {"enabled": row.enabled, "webhooks": row.webhooks or []}
    return {"enabled": True, "webhooks": []}


async def db_save_notification_config(db: AsyncSession, config: dict) -> None:
    result = await db.execute(select(Notification).limit(1))
    row = result.scalar_one_or_none()
    if row:
        row.enabled = config.get("enabled", True)
        row.webhooks = config.get("webhooks", [])
    else:
        db.add(Notification(
            enabled=config.get("enabled", True),
            webhooks=config.get("webhooks", []),
        ))
    await db.commit()


# ===== 节点注册 =====

async def db_get_all_nodes(db: AsyncSession) -> list:
    result = await db.execute(select(NodeRegistry).order_by(NodeRegistry.created_at.desc()))
    return [_to_dict(r) for r in result.all()]


async def db_get_node(db: AsyncSession, node_id: str) -> Optional[dict]:
    result = await db.execute(select(NodeRegistry).where(NodeRegistry.node_id == node_id))
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


async def db_create_node(db: AsyncSession, node_data: dict) -> dict:
    node = NodeRegistry(**node_data)
    db.add(node)
    await db.commit()
    return _to_dict(node)


async def db_update_node(db: AsyncSession, node_id: str, **kwargs) -> bool:
    result = await db.execute(
        update(NodeRegistry).where(NodeRegistry.node_id == node_id).values(**kwargs)
    )
    await db.commit()
    return result.rowcount > 0


async def db_delete_node(db: AsyncSession, node_id: str) -> bool:
    result = await db.execute(delete(NodeRegistry).where(NodeRegistry.node_id == node_id))
    await db.commit()
    return result.rowcount > 0
