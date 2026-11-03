"""Native project-owned Cloud attachment provenance and immutable upload receipts."""

import sqlalchemy as sa
from alembic import op


revision = "4f25ab639e87"
down_revision = "3e14fa528d76"
branch_labels = None
depends_on = None


def _identity_columns():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "project_attachment",
        *_identity_columns(),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("folder_id", sa.String(16), nullable=False, server_default="inbox"),
    )
    op.create_index("ix_project_attachment_project_id", "project_attachment", ["project_id"])
    op.create_index("ix_project_attachment_user_id", "project_attachment", ["user_id"])
    op.create_index(
        "ix_project_attachment_project_content_sha256", "project_attachment", ["project_id", "content_sha256"]
    )
    op.create_table(
        "project_attachment_upload_receipt",
        *_identity_columns(),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("request_uid", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "project_id", "request_uid", name="uq_project_attachment_upload_request"),
    )
    dialect = op.get_bind().dialect.name
    if dialect in {"sqlite", "postgresql"}:
        keys = ("storage_type", "storage_name", "filename")
        expressions = (
            [f"((file::jsonb #>> ARRAY[]::text[])::jsonb ->> '{key}')" for key in keys]
            if dialect == "postgresql"
            else [f"json_extract(json_extract(file, '$'), '$.{key}')" for key in keys]
        )
        op.execute(
            "CREATE INDEX ix_project_attachment_file_identity ON project_attachment (" + ", ".join(expressions) + ")"
        )


def downgrade():
    for table in ("project_attachment_upload_receipt", "project_attachment"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Cannot discard native Cloud provenance or durable upload receipts")
    op.drop_table("project_attachment_upload_receipt")
    op.drop_table("project_attachment")
