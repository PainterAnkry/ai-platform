"""agents, tools and data_files (Phase 5 agent workflows)

Revision ID: 0005_agents_tools_data_files
Revises: 0004_conversations_and_messages
Create Date: 2026-09-30 00:00:00

``conversations.agent_id`` was created as a plain integer in 0004; this
migration only adds the new tables. (SQLite cannot add a FOREIGN KEY to an
existing column without table rebuilds, and a dangling agent_id degrades to
"no agent bound" in the service layer, so the constraint is deliberately
deferred to Phase 7's hardening pass.)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_agents_tools_data_files"
down_revision: Union[str, None] = "0004_conversations_and_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"], unique=False)

    op.create_table(
        "tools",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("type", sa.String(length=32), nullable=False, server_default="builtin"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_tools_name"),
    )

    op.create_table(
        "data_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=16), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("column_names", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_data_files_user_id", "data_files", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_data_files_user_id", table_name="data_files")
    op.drop_table("data_files")
    op.drop_table("tools")
    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_table("agents")
