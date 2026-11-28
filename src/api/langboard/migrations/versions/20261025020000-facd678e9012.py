"""Retain native wiki attachment replacement provenance."""

import sqlalchemy as sa
from alembic import op


revision = "facd678e9012"
down_revision = "efbc567d8901"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "replaced_by_id" not in {column["name"] for column in inspector.get_columns("project_wiki_attachment")}:
        op.add_column("project_wiki_attachment", sa.Column("replaced_by_id", sa.BigInteger(), nullable=True))
    if "ix_project_wiki_attachment_replaced_by_id" not in {
        index["name"] for index in inspector.get_indexes("project_wiki_attachment")
    }:
        op.create_index("ix_project_wiki_attachment_replaced_by_id", "project_wiki_attachment", ["replaced_by_id"])


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM project_wiki_attachment WHERE replaced_by_id IS NOT NULL LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Cannot discard retained wiki attachment replacement provenance")
    op.drop_index("ix_project_wiki_attachment_replaced_by_id", table_name="project_wiki_attachment")
    op.drop_column("project_wiki_attachment", "replaced_by_id")
