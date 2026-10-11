"""Retain explicit connection-owner MCP scope consent."""
import sqlalchemy as sa
from alembic import op

revision = "c97659de63f4"
down_revision = "b86548cd52e3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("app_mcp_consent",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("identity_hash", sa.String(), nullable=False),
        sa.Column("scope_tools", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_mcp_consent LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained MCP consent")
    op.drop_table("app_mcp_consent")
