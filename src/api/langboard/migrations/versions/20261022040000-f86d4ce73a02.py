"""Persist personal project collections separately from boards and app pins."""

import sqlalchemy as sa
from alembic import op


revision = "f86d4ce73a02"
down_revision = "e75c3bd62f91"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_project_collection",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("collections", sa.JSON(), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM user_project_collection LIMIT 1")).first():
        raise RuntimeError("Cannot discard saved project collections")
    op.drop_table("user_project_collection")
