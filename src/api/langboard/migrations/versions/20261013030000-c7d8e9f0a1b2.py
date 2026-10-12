"""Retain card audience and creation-only private ownership without reclassification."""

import sqlalchemy as sa
from alembic import op


revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("card", sa.Column("created_by_user_id", sa.BigInteger(), nullable=True))
    op.add_column("card", sa.Column("created_by_bot_id", sa.BigInteger(), nullable=True))
    op.add_column("card", sa.Column("visibility", sa.Text(), nullable=False, server_default="INTERNAL"))
    op.add_column("card", sa.Column("owner_user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_card_owner_user_id", "card", "user", ["owner_user_id"], ["id"])
    op.create_index("ix_card_created_by_user_id", "card", ["created_by_user_id"])
    op.create_index("ix_card_created_by_bot_id", "card", ["created_by_bot_id"])
    op.create_index("ix_card_project_visibility", "card", ["project_id", "visibility"])
    op.create_check_constraint("card_visibility", "card", "visibility IN ('INTERNAL', 'SHARED', 'PRIVATE')")
    op.create_check_constraint(
        "card_private_owner",
        "card",
        "(visibility = 'PRIVATE' AND owner_user_id IS NOT NULL AND owner_user_id > 0 "
        "AND created_by_user_id IS NOT NULL AND owner_user_id = created_by_user_id "
        "AND created_by_bot_id IS NULL) OR (visibility <> 'PRIVATE' AND owner_user_id IS NULL)",
    )


def downgrade():
    op.drop_constraint("card_private_owner", "card", type_="check")
    op.drop_constraint("card_visibility", "card", type_="check")
    op.drop_index("ix_card_project_visibility", table_name="card")
    op.drop_index("ix_card_created_by_bot_id", table_name="card")
    op.drop_index("ix_card_created_by_user_id", table_name="card")
    op.drop_constraint("fk_card_owner_user_id", "card", type_="foreignkey")
    for column in ("owner_user_id", "visibility", "created_by_bot_id", "created_by_user_id"):
        op.drop_column("card", column)
