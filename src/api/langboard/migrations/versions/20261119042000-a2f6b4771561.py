"""Retain administrator-controlled webhook delivery state."""

from alembic import op
import sqlalchemy as sa

revision = "a2f6b4771561"
down_revision = "57742a932cb1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webhook_setting", sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM webhook_setting WHERE NOT is_enabled LIMIT 1")).scalar():
        raise RuntimeError("Cannot remove the webhook state while disabled webhooks exist")
    op.drop_column("webhook_setting", "is_enabled")
