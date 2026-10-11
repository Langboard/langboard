"""Explicit card-type archive synchronization consent defaults to disabled."""

import sqlalchemy as sa
from alembic import op


revision = "d3c6fd51a7e9"
down_revision = "c2b5ec40f6d8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("board_app_binding", sa.Column("archive_sync_types", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("board_app_binding", sa.Column("archive_sync_revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM board_app_binding WHERE CAST(archive_sync_types AS TEXT) <> '[]' LIMIT 1")).first():
        raise RuntimeError("Disable archive synchronization before schema downgrade")
    op.drop_column("board_app_binding", "archive_sync_types")
    op.drop_column("board_app_binding", "archive_sync_revision")
