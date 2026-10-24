"""Add payload-free native MCP execution history."""

import sqlalchemy as sa
from alembic import op


revision = "b6810c30fe54"
down_revision = "a570fb29ed43"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mcp_tool_execution",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=False, unique=True),
        sa.Column("tool", sa.String(128), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id")),
        sa.Column("bot_id", sa.BigInteger(), sa.ForeignKey("bot.id")),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("card_id", sa.BigInteger(), sa.ForeignKey("card.id")),
        sa.Column("project_column_id", sa.BigInteger(), sa.ForeignKey("project_column.id")),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
    )
    for column in ("user_id", "bot_id", "project_id", "card_id"):
        op.create_index(f"ix_mcp_tool_execution_{column}", "mcp_tool_execution", [column])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM mcp_tool_execution LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained MCP execution history")
    op.drop_table("mcp_tool_execution")
