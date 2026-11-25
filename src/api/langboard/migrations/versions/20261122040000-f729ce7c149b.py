"""Index exact wiki attachment provenance, including retained replacements."""

from alembic import op


revision = "f729ce7c149b"
down_revision = "e37a3108a9f2"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_wiki_attachment_file_identity"


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    keys = ("storage_type", "storage_name", "filename")
    if dialect == "postgresql":
        expressions = [f"((file::jsonb #>> ARRAY[]::text[])::jsonb ->> '{key}')" for key in keys]
    elif dialect == "sqlite":
        expressions = [f"json_extract(json_extract(file, '$'), '$.{key}')" for key in keys]
    else:
        return
    op.execute(f"CREATE INDEX {INDEX_NAME} ON project_wiki_attachment ({', '.join(expressions)})")


def downgrade() -> None:
    if op.get_bind().dialect.name in {"postgresql", "sqlite"}:
        op.drop_index(INDEX_NAME, table_name="project_wiki_attachment")
