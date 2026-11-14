"""Enforce personal collection ownership without deleting unmatched rows."""

import sqlalchemy as sa
from alembic import op


revision = "d8032e52b076"
down_revision = "c7921d41af65"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    orphan = connection.execute(
        sa.text(
            'SELECT 1 FROM user_project_collection c '
            'LEFT JOIN "user" u ON u.id = c.user_id WHERE u.id IS NULL LIMIT 1'
        )
    ).first()
    if orphan:
        raise RuntimeError("Cannot enforce collection ownership while unmatched owners exist")
    with op.batch_alter_table("user_project_collection", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.create_foreign_key(
            "fk_user_project_collection_owner", "user", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade():
    with op.batch_alter_table("user_project_collection", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.drop_constraint("fk_user_project_collection_owner", type_="foreignkey")

