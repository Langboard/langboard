# Generated using native db:migrate from cb92fe156d98 on PostgreSQL18.6.
# Pre-existing baseline drift was excluded by exact AST multiset comparison.
# The retained-history downgrade guard is a separate manually reviewed addition.
"""empty message

Revision ID: 9f243a28a91f
Revises: cb92fe156d98
Create Date: 2026-11-13 12:17:02.867701

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType

revision: str = "9f243a28a91f"
down_revision: Union[str, None] = "cb92fe156d98"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_transfer_request",
        sa.Column("id", SnowflakeIDType, autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", SnowflakeIDType, nullable=False),
        sa.Column("client_request_id", sa.String(), nullable=False),
        sa.Column("project_id", SnowflakeIDType, nullable=False),
        sa.Column("card_id", SnowflakeIDType, nullable=False),
        sa.Column("connection_id", SnowflakeIDType, nullable=False),
        sa.Column("app_key", sa.String(), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=False),
        sa.Column("authority", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_transfer_request")),
        sa.UniqueConstraint(
            "actor_id",
            "client_request_id",
            name="uq_app_transfer_request_client_request",
        ),
    )
    op.create_index(
        op.f("ix_app_transfer_request_actor_id"),
        "app_transfer_request",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_app_transfer_request_card_id"),
        "app_transfer_request",
        ["card_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_app_transfer_request_project_id"),
        "app_transfer_request",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM app_transfer_request LIMIT 1"))
        .first()
        is not None
    ):
        raise RuntimeError("Cannot discard app transfer history")
    op.drop_index(
        op.f("ix_app_transfer_request_project_id"), table_name="app_transfer_request"
    )
    op.drop_index(
        op.f("ix_app_transfer_request_card_id"), table_name="app_transfer_request"
    )
    op.drop_index(
        op.f("ix_app_transfer_request_actor_id"), table_name="app_transfer_request"
    )
    op.drop_table("app_transfer_request")
