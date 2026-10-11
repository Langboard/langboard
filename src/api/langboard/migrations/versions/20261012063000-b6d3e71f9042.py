"""Associate boards with generic organization app policy boundaries."""

import sqlalchemy as sa
from alembic import op


revision = "b6d3e71f9042"
down_revision = "a7c9e31b6204"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("project", sa.Column("organization_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_project_organization_id_organization",
        "project",
        "organization",
        ["organization_id"],
        ["id"],
    )
    op.create_index("ix_project_organization_id", "project", ["organization_id"])


def downgrade():
    op.drop_index("ix_project_organization_id", table_name="project")
    op.drop_constraint("fk_project_organization_id_organization", "project", type_="foreignkey")
    op.drop_column("project", "organization_id")
