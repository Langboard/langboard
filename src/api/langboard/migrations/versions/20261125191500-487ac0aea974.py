"""Store authenticated browser push subscriptions."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


revision: str = "487ac0aea974"
down_revision: str = "7bde71b402af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_web_push_subscription",
        sa.Column("id", SnowflakeIDType, autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", SnowflakeIDType, nullable=False),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.Column("session_expires_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("endpoint_digest", sa.String(64), nullable=True),
        sa.Column("encrypted_subscription", sa.Text(), nullable=True),
        sa.Column("signed_out", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE", name=op.f("fk_user_web_push_subscription_user_id_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_web_push_subscription")),
        sa.UniqueConstraint("endpoint_digest", name=op.f("uq_user_web_push_subscription_endpoint_digest")),
        sa.UniqueConstraint("session_digest", name=op.f("uq_user_web_push_subscription_session_digest")),
    )
    op.create_index(op.f("ix_user_web_push_subscription_user_id"), "user_web_push_subscription", ["user_id"])
    op.create_index(
        "ix_user_web_push_subscription_session_expires_at", "user_web_push_subscription", ["session_expires_at"]
    )


def downgrade() -> None:
    op.drop_table("user_web_push_subscription")
