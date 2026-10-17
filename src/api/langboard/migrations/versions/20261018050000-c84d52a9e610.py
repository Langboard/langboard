"""Persist human-only card/relationship discussions separately from comments."""

import sqlalchemy as sa
from alembic import op


revision = "c84d52a9e610"
down_revision = "a31f9e7b402c"
branch_labels = None
depends_on = None


def _identity():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "whisper_thread",
        *_identity(),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("card_id", sa.BigInteger(), sa.ForeignKey("card.id"), nullable=True),
        sa.Column("relationship_id", sa.BigInteger(), sa.ForeignKey("card_relationship.id"), nullable=True),
        sa.Column("subanchor_type", sa.String(), nullable=True),
        sa.Column("subanchor_uid", sa.String(), nullable=True),
        sa.Column("creator_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.CheckConstraint("state IN ('OPEN', 'RESOLVED')", name="ck_whisper_thread_state"),
        sa.CheckConstraint(
            "(card_id IS NOT NULL AND relationship_id IS NULL) OR "
            "(card_id IS NULL AND relationship_id IS NOT NULL AND subanchor_type IS NULL)",
            name="ck_whisper_thread_anchor",
        ),
        sa.CheckConstraint(
            "(subanchor_type IS NULL AND subanchor_uid IS NULL) OR "
            "(subanchor_type IS NOT NULL AND subanchor_type IN ('content_block', 'comment', 'checklist', 'attachment') "
            "AND subanchor_uid IS NOT NULL)",
            name="ck_whisper_thread_subanchor",
        ),
    )
    for column in ("project_id", "card_id", "relationship_id"):
        op.create_index(f"ix_whisper_thread_{column}", "whisper_thread", [column])
    op.create_table(
        "whisper_message",
        *_identity(),
        sa.Column("thread_id", sa.BigInteger(), sa.ForeignKey("whisper_thread.id"), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mention_uids", sa.JSON(), nullable=False),
    )
    op.create_index("ix_whisper_message_thread_id_id", "whisper_message", ["thread_id", "id"])
    op.create_table(
        "whisper_participant",
        *_identity(),
        sa.Column("thread_id", sa.BigInteger(), sa.ForeignKey("whisper_thread.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("last_read_message_id", sa.BigInteger(), sa.ForeignKey("whisper_message.id"), nullable=True),
        sa.UniqueConstraint("thread_id", "user_id", name="uq_whisper_participant"),
    )
    op.create_index("ix_whisper_participant_thread_id", "whisper_participant", ["thread_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM whisper_thread LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained human discussions")
    for table in ("whisper_participant", "whisper_message", "whisper_thread"):
        op.drop_table(table)
