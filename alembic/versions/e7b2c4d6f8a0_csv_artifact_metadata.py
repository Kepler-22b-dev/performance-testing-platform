"""add CSV artifact metadata

Revision ID: e7b2c4d6f8a0
Revises: c4f7a1d9e2b3
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7b2c4d6f8a0"
down_revision: Union[str, Sequence[str], None] = "c4f7a1d9e2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("csv_files", sa.Column("artifact_id", sa.String(length=64), nullable=True))
    op.add_column("csv_files", sa.Column("artifact_version", sa.String(length=64), nullable=True))
    op.add_column("csv_files", sa.Column("storage_key", sa.String(length=1024), nullable=True))
    op.add_column("csv_files", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("csv_files", sa.Column("encoding", sa.String(length=32), nullable=True))
    op.add_column("csv_files", sa.Column("delimiter", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("csv_files", "delimiter")
    op.drop_column("csv_files", "encoding")
    op.drop_column("csv_files", "sha256")
    op.drop_column("csv_files", "storage_key")
    op.drop_column("csv_files", "artifact_version")
    op.drop_column("csv_files", "artifact_id")
