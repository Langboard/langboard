"""Retire unsupported deletion triggers while retaining definitions and receipts.

The original conversion emitted card_deleted policy rules. The runtime cannot
admit these events after deletion, and active configuration validation rejects
them even when disabled. Use the existing retirement boundary to preserve their
identities, actions, conditions and execution history outside active settings.
"""

import sqlalchemy as sa
from alembic import op


revision = "acde789f0123"
down_revision = "facd678e9012"
branch_labels = None
depends_on = None


def upgrade():
    rule = sa.table(
        "workflow_rule",
        sa.column("trigger_kind", sa.String()),
        sa.column("event_type", sa.String()),
        sa.column("retired", sa.Boolean()),
        sa.column("is_enabled", sa.Boolean()),
    )
    op.get_bind().execute(
        rule.update()
        .where(rule.c.trigger_kind == "event", rule.c.event_type == "card_deleted", rule.c.retired.is_(False))
        .values(retired=True, is_enabled=False)
    )


def downgrade():
    # Prior enabled/retired state cannot be inferred without rewriting history.
    raise RuntimeError("Deletion trigger retirement is irreversible; restore a verified pre-upgrade backup")
