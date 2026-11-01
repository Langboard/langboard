"""Optional card-local workflow stage proposal gates."""

import sqlalchemy as sa
from alembic import op


revision = "7fab890c1234"
down_revision = "6efa789b0123"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "card_decision_gate",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("card_id", sa.BigInteger(), sa.ForeignKey("card.id"), nullable=False),
        sa.Column("target_stage", sa.String(), nullable=False),
        sa.Column("decision_id", sa.BigInteger(), sa.ForeignKey("decision_case.id"), nullable=False),
        sa.Column("round_id", sa.String(), nullable=False),
        sa.Column("target_revision", sa.String(), nullable=False),
        sa.Column("policy_revision", sa.String(), nullable=False),
        sa.UniqueConstraint("card_id", "target_stage", name="uq_card_decision_gate_target"),
    )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE card_decision_gate IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM card_decision_gate")):
        raise RuntimeError("Cannot discard retained workflow gate bindings")
    op.drop_table("card_decision_gate")
