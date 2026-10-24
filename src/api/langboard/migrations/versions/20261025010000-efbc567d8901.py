"""Persist incoming native OAuth client connections and human board consent."""

import sqlalchemy as sa
from alembic import op


revision = "efbc567d8901"
down_revision = "dfab456c7890"
branch_labels = None
depends_on = None


def _base():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "native_mcp_connection",
        *_base(),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.UniqueConstraint("user_id", "client_id"),
    )
    op.create_index("ix_native_mcp_connection_user_id", "native_mcp_connection", ["user_id"])
    op.create_table(
        "native_mcp_board_consent",
        *_base(),
        sa.Column(
            "connection_id",
            sa.BigInteger(),
            sa.ForeignKey("native_mcp_connection.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("connection_id", "project_id"),
    )
    op.create_index("ix_native_mcp_board_consent_connection_id", "native_mcp_board_consent", ["connection_id"])
    op.create_index("ix_native_mcp_board_consent_project_id", "native_mcp_board_consent", ["project_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM native_mcp_connection LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained native MCP connections or consent")
    op.drop_table("native_mcp_board_consent")
    op.drop_table("native_mcp_connection")
