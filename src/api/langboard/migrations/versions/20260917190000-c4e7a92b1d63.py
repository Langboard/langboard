"""add checklist is_system marker

Revision ID: c4e7a92b1d63
Revises: da39f306364b
Create Date: 2026-09-17 19:00:00.000000

"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4e7a92b1d63'
down_revision: Union[str, None] = 'canary_integration_20260917'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "is_system" not in {column["name"] for column in inspector.get_columns("checklist")}:
        op.add_column('checklist', sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.false()))
    if "ix_checklist_is_system" not in {index["name"] for index in inspector.get_indexes("checklist")}:
        op.create_index(op.f('ix_checklist_is_system'), 'checklist', ['is_system'])


def downgrade() -> None:
    op.drop_index(op.f('ix_checklist_is_system'), table_name='checklist')
    op.drop_column('checklist', 'is_system')
