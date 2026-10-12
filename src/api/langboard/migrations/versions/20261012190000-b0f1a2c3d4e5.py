"""Bind administrator managed webhook settings to app connections."""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "b0f1a2c3d4e5"
down_revision: Union[str, None] = "a9de428cb76f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_event_destination",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column("webhook_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("target_revision", sa.String(), nullable=False),
        sa.Column("trust_revision", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_event_destination")),
        sa.UniqueConstraint("connection_id", name=op.f("uq_app_event_destination_connection_id")),
    )
    op.create_index(
        op.f("ix_app_event_destination_connection_id"), "app_event_destination", ["connection_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_event_destination_connection_id"), table_name="app_event_destination")
    op.drop_table("app_event_destination")
