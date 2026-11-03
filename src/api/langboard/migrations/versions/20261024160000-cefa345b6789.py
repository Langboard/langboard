"""Named personal board filter combinations; preserve existing pins."""

import json
import sqlalchemy as sa
from alembic import op


revision = "cefa345b6789"
down_revision = "bdef234a5678"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_board_filter_preference",
        sa.Column("saved_views", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade():
    rows = op.get_bind().execute(sa.text("SELECT saved_views FROM user_board_filter_preference"))
    for (value,) in rows:
        if (json.loads(value) if isinstance(value, str) else value) != []:
            raise RuntimeError("Cannot discard saved filter views")
    op.drop_column("user_board_filter_preference", "saved_views")
