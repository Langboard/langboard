"""Add optional project identity settings.

Generated with db:migrate (autogenerate=True) from e37a3108a9f2 on PostgreSQL 17.
Retain only the generated project identity column and its inverse.
"""

import sqlalchemy as sa
from alembic import op


revision = "0813b9a8721d"
down_revision = "e37a3108a9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project", sa.Column("identity", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("project", "identity")
