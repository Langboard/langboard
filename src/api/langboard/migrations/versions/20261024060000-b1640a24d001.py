"""Persist immutable Decision proposal rounds and authenticated actor responses."""

import sqlalchemy as sa
from alembic import op


revision = "b1640a24d001"
down_revision = "72f4c6e8a190"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "decision_case",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("card_id", sa.BigInteger(), sa.ForeignKey("card.id", ondelete="CASCADE"), nullable=False),
        sa.Column("creator_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("round_id", sa.String(), nullable=False, unique=True),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("target_revision", sa.String(), nullable=False),
        sa.Column("policy_revision", sa.String(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("response_order", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.UniqueConstraint("creator_id", "request_id", name="uq_decision_case_request"),
    )
    op.create_index("ix_decision_case_project_id", "decision_case", ["project_id"])
    op.create_index("ix_decision_case_card_id", "decision_case", ["card_id"])
    op.create_table(
        "decision_response",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "decision_id", sa.BigInteger(), sa.ForeignKey("decision_case.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("round_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("choice", sa.String(), nullable=False),
        sa.Column("response_order", sa.Integer(), nullable=False),
        sa.Column("responded_at", sa.DateTime(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.UniqueConstraint("decision_id", "round_id", "actor_id", name="uq_decision_response_actor"),
        sa.UniqueConstraint("decision_id", "response_order", name="uq_decision_response_order"),
        sa.UniqueConstraint("actor_id", "request_id", name="uq_decision_response_request"),
    )
    op.create_index("ix_decision_response_decision_id", "decision_response", ["decision_id"])


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE decision_case, decision_response IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM decision_case")) or connection.scalar(
        sa.text("SELECT count(*) FROM decision_response")
    ):
        raise RuntimeError("Cannot discard retained Decision rounds or response receipts")
    op.drop_table("decision_response")
    op.drop_table("decision_case")
