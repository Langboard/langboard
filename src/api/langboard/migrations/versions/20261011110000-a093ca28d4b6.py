"""Freeze explicitly approved workflow destinations for new human requests."""

import sqlalchemy as sa
from alembic import op


revision = "a093ca28d4b6"
down_revision = "fe82b917c3a5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_card_interaction", sa.Column("workflow_snapshot", sa.JSON(), nullable=False, server_default="{}")
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM app_card_interaction WHERE workflow_snapshot IS NOT NULL AND CAST(workflow_snapshot AS TEXT) != '{}' LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError("Cannot discard frozen interaction workflow history")
    op.drop_column("app_card_interaction", "workflow_snapshot")
