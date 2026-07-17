"""P0 reliability constraints for existing databases.

Revision ID: c4f7a1d9e2b3
Revises: 8aea793400de
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "c4f7a1d9e2b3"
down_revision: Union[str, Sequence[str], None] = "8aea793400de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _constraint_names(inspector, table_name: str) -> set[str]:
    names = {
        item.get("name")
        for item in inspector.get_unique_constraints(table_name)
        if item.get("name")
    }
    names.update(
        item.get("name")
        for item in inspector.get_foreign_keys(table_name)
        if item.get("name")
    )
    return names


def upgrade() -> None:
    # 新数据库已由初始迁移创建约束；本迁移主要修复已存在的数据库。
    # 离线 SQL 无法反射约束，因此无需重复输出条件 DDL。
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_results" not in inspector.get_table_names():
        return

    constraints = _constraint_names(inspector, "task_results")
    if "uq_task_results_task_agent" not in constraints:
        op.execute(sa.text("""
            DELETE FROM task_results older
            USING task_results newer
            WHERE older.task_id = newer.task_id
              AND older.agent_id = newer.agent_id
              AND older.id < newer.id
        """))
        op.create_unique_constraint(
            "uq_task_results_task_agent",
            "task_results",
            ["task_id", "agent_id"],
        )

    inspector = sa.inspect(bind)
    constraints = _constraint_names(inspector, "task_results")
    if "fk_task_results_task_id_tasks" not in constraints:
        op.execute(sa.text("""
            DELETE FROM task_results
            WHERE NOT EXISTS (
                SELECT 1 FROM tasks WHERE tasks.task_id = task_results.task_id
            )
        """))
        op.create_foreign_key(
            "fk_task_results_task_id_tasks",
            "task_results",
            "tasks",
            ["task_id"],
            ["task_id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_results" not in inspector.get_table_names():
        return
    constraints = _constraint_names(inspector, "task_results")
    if "fk_task_results_task_id_tasks" in constraints:
        op.drop_constraint(
            "fk_task_results_task_id_tasks",
            "task_results",
            type_="foreignkey",
        )
    if "uq_task_results_task_agent" in constraints:
        op.drop_constraint(
            "uq_task_results_task_agent",
            "task_results",
            type_="unique",
        )
