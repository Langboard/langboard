"""Add canonical human-intent and Active audit evidence without recreating apps."""

import sqlalchemy as sa
from alembic import op

revision = "e1987bdf2503"
down_revision = "d0876afe1402"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve existing selections and access revisions. An empty hash is an
    # absence of identity evidence, never a fabricated approval of an account.
    op.add_column(
        "board_app_binding", sa.Column("resource_selection_revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("app_resource_binding", sa.Column("identity_hash", sa.String(), nullable=False, server_default=""))
    with op.batch_alter_table("app_resource_binding") as batch:
        batch.alter_column("identity_hash", server_default=None)
    op.create_table(
        "card_execution_intent",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("execution_generation", sa.Integer(), nullable=False),
        sa.Column("app_key", sa.String(), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.CheckConstraint("execution_generation >= 1", name=op.f("ck_card_execution_intent_positive")),
        sa.UniqueConstraint("card_id", "idempotency_key", name="uq_card_execution_intent_request"),
        sa.UniqueConstraint("card_id", "execution_generation", name="uq_card_execution_intent_generation"),
    )
    op.create_index("ix_card_execution_intent_card_id", "card_execution_intent", ["card_id"])
    op.create_table(
        "app_execution_active",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("lease_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("start_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("column_id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.CheckConstraint("generation >= 1", name=op.f("ck_app_execution_active_positive")),
    )


def downgrade():
    # Retained request/start/handoff evidence must not disappear on rollback.
    for table in ("card_execution_intent", "app_execution_active"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Cannot discard retained execution intent or Active handoff evidence")
    op.drop_table("app_execution_active")
    op.drop_index("ix_card_execution_intent_card_id", table_name="card_execution_intent")
    op.drop_table("card_execution_intent")
    op.drop_column("app_resource_binding", "identity_hash")
    op.drop_column("board_app_binding", "resource_selection_revision")
