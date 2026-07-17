"""add CSV distribution mode

Revision ID: f9a1b3c5d7e9
Revises: e7b2c4d6f8a0
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9a1b3c5d7e9"
down_revision: Union[str, Sequence[str], None] = "e7b2c4d6f8a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "csv_distribution",
            sa.String(length=16),
            nullable=False,
            server_default="replicate",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "csv_distribution")
