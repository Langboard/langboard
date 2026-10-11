"""Retain app package installation provenance separately from portable declarations."""
import sqlalchemy as sa
from alembic import op

revision = "a75437bc41d2"
down_revision = "b1a4db39e5c7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_definition", sa.Column("package", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_definition WHERE package IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained package installation history")
    op.drop_column("app_definition", "package")
