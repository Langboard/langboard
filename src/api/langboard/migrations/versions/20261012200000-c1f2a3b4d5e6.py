"""Bound app event delivery claims and retry receipts."""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op


revision: str = "c1f2a3b4d5e6"
down_revision: Union[str, None] = "b0f1a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("app_execution_outbox", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("app_execution_outbox", sa.Column("claim_token", sa.String(), nullable=True))
    op.add_column("app_execution_outbox", sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("app_execution_outbox", sa.Column("last_error", sa.String(), nullable=True))

    op.add_column(
        "app_execution_outbox", sa.Column("delivery_receipts", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade() -> None:
    for name in ("delivery_receipts", "last_error", "claim_expires_at", "claim_token", "attempt"):
        op.drop_column("app_execution_outbox", name)
