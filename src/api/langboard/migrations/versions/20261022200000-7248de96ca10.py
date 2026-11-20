"""Durable actor-bound native project creation receipts."""

import sqlalchemy as sa
from alembic import op


revision = "7248de96ca10"
down_revision = "6137cd85bf09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_creation_receipt",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("request_uuid", sa.String(36), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("template_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_uuid", name="uq_project_creation_receipt_actor_request"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM project_creation_receipt LIMIT 1")).first():
        raise RuntimeError("Cannot discard project creation retry receipts")
    op.drop_table("project_creation_receipt")
