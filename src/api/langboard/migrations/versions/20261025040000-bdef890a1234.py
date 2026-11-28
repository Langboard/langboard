"""Add durable safe evidence for each native SMTP invocation; retain old outboxes."""

import sqlalchemy as sa
from alembic import op


revision = "bdef890a1234"
down_revision = "acde789f0123"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mail_delivery_attempt",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("safe_error_code", sa.String(40), nullable=True),
        sa.Column("recipient_masked", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(160), nullable=True),
        sa.Column("template_name", sa.String(64), nullable=True),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("source_uid", sa.String(11), nullable=True),
        sa.Column("linked_ids", sa.JSON(), nullable=True),
    )
    for field in ("started_at", "outcome", "source_uid"):
        op.create_index(f"ix_mail_delivery_attempt_{field}", "mail_delivery_attempt", [field])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM mail_delivery_attempt LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained SMTP attempt evidence")
    op.drop_table("mail_delivery_attempt")
