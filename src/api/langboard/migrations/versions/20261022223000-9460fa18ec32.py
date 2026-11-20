"""Retain Cloud provenance and independently published document sources."""

from json import loads
import sqlalchemy as sa
from alembic import op


revision = "9460fa18ec32"
down_revision = "8359ef07db21"
branch_labels = None
depends_on = None


def _object(value):
    while isinstance(value, str):
        try:
            value = loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def upgrade():
    with op.batch_alter_table("project_attachment", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.add_column(sa.Column("replaced_by_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key("fk_project_attachment_replaced_by", "project_attachment", ["replaced_by_id"], ["id"])
    op.create_index("ix_project_attachment_replaced_by_id", "project_attachment", ["replaced_by_id"])
    connection = op.get_bind()
    for name, attachment_name in (
        ("project_attachment_document", "project_attachment"),
        ("wiki_attachment_document", "project_wiki_attachment"),
    ):
        for column in ("text_source", "vector_source"):
            op.add_column(name, sa.Column(column, sa.JSON(none_as_null=True), nullable=True))
        documents = sa.Table(name, sa.MetaData(), autoload_with=connection, resolve_fks=False)
        attachments = sa.Table(attachment_name, sa.MetaData(), autoload_with=connection, resolve_fks=False)
        rows = (
            connection.execute(
                sa.select(
                    documents,
                    attachments.c.file,
                    attachments.c.filename,
                    attachments.c.content_sha256,
                ).join(attachments, documents.c.attachment_id == attachments.c.id)
            )
            .mappings()
            .all()
        )
        for row in rows:
            embedding = _object(row["embedding"])
            if row["status"] != "indexed" and not embedding.get("pointer"):
                continue
            file = _object(row["file"])
            if not file:
                raise RuntimeError("Cannot label published state without native file provenance")
            snapshot = {
                "file": file,
                "filename": row["filename"],
                "content_sha256": row["content_sha256"],
                "source_revision": row["source_revision"],
                "generation": row["generation"],
                "vision_config": _object(row["vision_config"]),
                "embedding_config": _object(row["embedding_config"]) or None,
            }
            connection.execute(
                documents.update()
                .where(documents.c.id == row["id"])
                .values(
                    text_source=snapshot if row["status"] == "indexed" else None,
                    vector_source=snapshot if embedding.get("pointer") else None,
                )
            )


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM project_attachment WHERE replaced_by_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained Cloud file provenance")
    for name in ("project_attachment_document", "wiki_attachment_document"):
        if connection.execute(
            sa.text(f"SELECT 1 FROM {name} WHERE text_source IS NOT NULL OR vector_source IS NOT NULL LIMIT 1")
        ).first():
            raise RuntimeError("Cannot discard independently published document sources")
    for name in ("project_attachment_document", "wiki_attachment_document"):
        with op.batch_alter_table(name, reflect_kwargs={"resolve_fks": False}) as batch:
            batch.drop_column("vector_source")
            batch.drop_column("text_source")
    op.drop_index("ix_project_attachment_replaced_by_id", table_name="project_attachment")
    with op.batch_alter_table("project_attachment", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.drop_constraint("fk_project_attachment_replaced_by", type_="foreignkey")
        batch.drop_column("replaced_by_id")
