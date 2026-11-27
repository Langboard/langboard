"""empty message

Revision ID: b7f2c8a1d9a4
Revises: 82f4e1fbed04
Create Date: 2026-04-14 11:30:00.000000

"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7f2c8a1d9a4"
down_revision: Union[str, None] = "82f4e1fbed04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archive_visible_days", sa.Integer(), nullable=False, server_default="3"))


def downgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("archive_visible_days")
