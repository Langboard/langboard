"""Bind newly issued sharing challenges to the user-selected board."""

from alembic import op
import sqlalchemy as sa
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType

revision = "57742a932cb1"
down_revision = "b40c2487826e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_notification_destination_challenge", sa.Column("project_id", SnowflakeIDType, nullable=True))


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM app_notification_destination_challenge WHERE project_id IS NOT NULL LIMIT 1"
    )).scalar():
        raise RuntimeError("Cannot remove retained notification challenge board scope")
    op.drop_column("app_notification_destination_challenge", "project_id")
