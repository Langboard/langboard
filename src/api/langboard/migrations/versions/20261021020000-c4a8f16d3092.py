"""Non-destructive, actor-scoped comment compaction runs."""

import sqlalchemy as sa
from alembic import op


revision = "c4a8f16d3092"
down_revision = "b392a7f43165"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "card_comment_compaction",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("card_id", sa.BigInteger(), sa.ForeignKey("card.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("internal_bot_id", sa.BigInteger(), sa.ForeignKey("internal_bot.id"), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("provider_revision", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("source_manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name="comment_compaction_status"),
        sa.CheckConstraint("attempt >= 0", name="comment_compaction_attempt"),
        sa.CheckConstraint("(status = 'completed' AND draft IS NOT NULL) OR (status <> 'completed' AND draft IS NULL)",
                           name="comment_compaction_draft_state"),
    )
    op.create_index("ix_card_comment_compaction_card_id", "card_comment_compaction", ["card_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM card_comment_compaction LIMIT 1")).first():
        raise RuntimeError("Cannot discard comment compaction evidence")
    op.drop_table("card_comment_compaction")
