"""Retain explicit app execution Review transition receipts."""
import sqlalchemy as sa
from alembic import op

revision = "b86548cd52e3"
down_revision = ("a75437bc41d2", "e71b63c8a204")
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_execution_review",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_execution_review LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained execution Review history")
    op.drop_table("app_execution_review")
