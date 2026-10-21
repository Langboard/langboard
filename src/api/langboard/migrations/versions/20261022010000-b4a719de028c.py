"""Retain automatic compaction with its dedicated profile as the principal."""

import sqlalchemy as sa
from alembic import op


revision = "b4a719de028c"
down_revision = "a3e607cf912b"
branch_labels = None
depends_on = None


def _actor_constraint():
    if op.get_context().as_sql:
        return "workflow_effect_actor"
    matches = [row["name"] for row in sa.inspect(op.get_bind()).get_check_constraints("workflow_effect_run")
               if row["name"] == "workflow_effect_actor" or row["name"].endswith("`workflow_effect_actor`")]
    if len(matches) != 1:
        raise RuntimeError("Workflow actor constraint must be present exactly once")
    return matches[0]


def _set_actors(values):
    name = _actor_constraint()
    with op.batch_alter_table("workflow_effect_run") as batch:
        batch.drop_constraint(op.f(name), type_="check")
        batch.create_check_constraint("workflow_effect_actor", f"actor_type IN ({values})")


def upgrade():
    with op.batch_alter_table("card_comment_compaction") as batch:
        batch.alter_column("user_id", existing_type=sa.BigInteger(), nullable=True)
    _set_actors("'user','bot','profile'")


def downgrade():
    db = op.get_bind()
    if (db.execute(sa.text("SELECT 1 FROM card_comment_compaction WHERE user_id IS NULL LIMIT 1")).first()
            or db.execute(sa.text("SELECT 1 FROM workflow_effect_run WHERE actor_type='profile' LIMIT 1")).first()):
        raise RuntimeError("Cannot discard compactor principal evidence")
    _set_actors("'user','bot'")
    with op.batch_alter_table("card_comment_compaction") as batch:
        batch.alter_column("user_id", existing_type=sa.BigInteger(), nullable=False)
