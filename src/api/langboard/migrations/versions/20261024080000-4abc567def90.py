"""Add named single-choice Poll options and immutable response revisions."""

import sqlalchemy as sa
from alembic import op


revision = "4abc567def90"
down_revision = "12ab34cde678"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "decision_option",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "decision_id", sa.BigInteger(), sa.ForeignKey("decision_case.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("decision_id", "key", name="uq_decision_option_key"),
        sa.UniqueConstraint("decision_id", "position", name="uq_decision_option_position"),
    )
    op.create_index("ix_decision_option_decision_id", "decision_option", ["decision_id"])
    with op.batch_alter_table("decision_response") as batch:
        batch.add_column(sa.Column("response_revision", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("option_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key(
            "fk_decision_response_option_id", "decision_option", ["option_id"], ["id"], ondelete="CASCADE"
        )
        batch.drop_constraint("uq_decision_response_actor", type_="unique")
        batch.create_unique_constraint(
            "uq_decision_response_actor", ["decision_id", "round_id", "actor_id", "response_revision"]
        )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "LOCK TABLE decision_case, decision_option, decision_response IN ACCESS EXCLUSIVE MODE"
        )
    if (
        connection.scalar(sa.text("SELECT count(*) FROM decision_case WHERE source_kind = 'poll'"))
        or connection.scalar(sa.text("SELECT count(*) FROM decision_option"))
        or connection.scalar(
            sa.text(
                "SELECT count(*) FROM decision_response WHERE response_revision <> 1 OR option_id IS NOT NULL OR choice = 'vote'"
            )
        )
    ):
        raise RuntimeError("Cannot discard retained Poll options or response history")
    with op.batch_alter_table("decision_response") as batch:
        batch.drop_constraint("uq_decision_response_actor", type_="unique")
        batch.create_unique_constraint("uq_decision_response_actor", ["decision_id", "round_id", "actor_id"])
        batch.drop_constraint("fk_decision_response_option_id", type_="foreignkey")
        batch.drop_column("option_id")
        batch.drop_column("response_revision")
    op.drop_table("decision_option")
