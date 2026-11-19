"""Allow administrators to limit a scheduled notification rule to one project."""

from alembic import op
import sqlalchemy as sa

revision = "e37a3108a9f2"
down_revision = "a2f6b4771561"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_schedule_rule", sa.Column("project_uid", sa.String(length=11), nullable=True))


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM notification_schedule_rule WHERE project_uid IS NOT NULL LIMIT 1")
    ).scalar():
        raise RuntimeError("Cannot remove project scope while scoped notification rules exist")
    op.drop_column("notification_schedule_rule", "project_uid")
