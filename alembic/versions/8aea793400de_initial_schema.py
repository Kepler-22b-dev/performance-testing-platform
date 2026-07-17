"""initial schema

Revision ID: 8aea793400de
Revises: 
Create Date: 2026-07-16 21:59:08.768571

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8aea793400de'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建平台初始业务表。"""
    op.create_table(
        'scripts',
        sa.Column('script_id', sa.String(length=64), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.Column('modified_at', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('script_id'),
    )
    op.create_table(
        'tasks',
        sa.Column('task_id', sa.String(length=64), nullable=False),
        sa.Column('script_id', sa.String(length=64), nullable=False),
        sa.Column('target_agents', sa.JSON(), nullable=True),
        sa.Column('jmeter_args', sa.JSON(), nullable=True),
        sa.Column('timeout', sa.Integer(), nullable=True),
        sa.Column('distributed', sa.Boolean(), nullable=True),
        sa.Column('remote_hosts', sa.String(length=512), nullable=True),
        sa.Column('csv_file', sa.String(length=512), nullable=True),
        sa.Column('csv_variable_names', sa.String(length=512), nullable=True),
        sa.Column('csv_delimiter', sa.String(length=8), nullable=True),
        sa.Column('csv_recycle', sa.Boolean(), nullable=True),
        sa.Column('csv_stop_on_eof', sa.Boolean(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.Column('start_time', sa.Float(), nullable=True),
        sa.Column('end_time', sa.Float(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('task_id'),
    )
    op.create_index('idx_tasks_created_at', 'tasks', ['created_at'], unique=False)
    op.create_index('idx_tasks_status', 'tasks', ['status'], unique=False)
    op.create_table(
        'task_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=64), nullable=False),
        sa.Column('agent_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('start_time', sa.Float(), nullable=True),
        sa.Column('end_time', sa.Float(), nullable=True),
        sa.Column('report_path', sa.String(length=512), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('summary', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ['task_id'], ['tasks.task_id'],
            name='fk_task_results_task_id_tasks',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'agent_id', name='uq_task_results_task_agent'),
    )
    op.create_index('idx_task_results_task_id', 'task_results', ['task_id'], unique=False)
    op.create_table(
        'variables',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('scope', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'csv_files',
        sa.Column('csv_id', sa.String(length=64), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('filepath', sa.String(length=512), nullable=False),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=True),
        sa.Column('preview', sa.JSON(), nullable=True),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('csv_id'),
    )
    op.create_table(
        'templates',
        sa.Column('template_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=64), nullable=True),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('builtin', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('template_id'),
    )
    op.create_table(
        'environments',
        sa.Column('env_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('base_url', sa.String(length=512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('variables', sa.JSON(), nullable=True),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('auth_token', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('env_id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'alert_rules',
        sa.Column('rule_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('metric', sa.String(length=64), nullable=True),
        sa.Column('operator', sa.String(length=8), nullable=True),
        sa.Column('threshold', sa.Float(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('notify_webhook', sa.Boolean(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.Column('triggered_count', sa.Integer(), nullable=True),
        sa.Column('last_triggered', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('rule_id'),
    )
    op.create_table(
        'schedules',
        sa.Column('schedule_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('script_id', sa.String(length=64), nullable=False),
        sa.Column('target_agents', sa.JSON(), nullable=True),
        sa.Column('jmeter_args', sa.JSON(), nullable=True),
        sa.Column('timeout', sa.Integer(), nullable=True),
        sa.Column('distributed', sa.Boolean(), nullable=True),
        sa.Column('remote_hosts', sa.String(length=512), nullable=True),
        sa.Column('cron_expr', sa.String(length=128), nullable=True),
        sa.Column('interval_seconds', sa.Integer(), nullable=True),
        sa.Column('run_at', sa.Float(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.Column('last_run', sa.Float(), nullable=True),
        sa.Column('next_run', sa.Float(), nullable=True),
        sa.Column('run_count', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('schedule_id'),
    )
    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('webhooks', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'node_registry',
        sa.Column('node_id', sa.String(length=128), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=False),
        sa.Column('port', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('last_check', sa.Float(), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('node_id'),
    )
    op.create_table(
        'script_counter',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('counter', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """删除平台初始业务表。"""
    op.drop_table('script_counter')
    op.drop_table('node_registry')
    op.drop_table('notifications')
    op.drop_table('schedules')
    op.drop_table('alert_rules')
    op.drop_table('environments')
    op.drop_table('templates')
    op.drop_table('csv_files')
    op.drop_table('variables')
    op.drop_index('idx_task_results_task_id', table_name='task_results')
    op.drop_table('task_results')
    op.drop_index('idx_tasks_status', table_name='tasks')
    op.drop_index('idx_tasks_created_at', table_name='tasks')
    op.drop_table('tasks')
    op.drop_table('scripts')
