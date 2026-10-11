"""Persist independent board app resource selections."""

import sqlalchemy as sa
from alembic import op


revision = "d9a5b73e201f"
down_revision = "c8f4a62d190e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_resource_binding",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("board_binding_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("external_resource_id", sa.String(), nullable=False),
        sa.Column("resource_path", sa.JSON(), nullable=False),
        sa.Column("is_selected", sa.Boolean(), nullable=False),
        sa.Column("access_state", sa.String(), nullable=False),
        sa.Column("access_revision", sa.Integer(), nullable=False),
        sa.Column("health", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["board_binding_id"], ["board_app_binding.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["app_connection.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "board_binding_id",
            "connection_id",
            "resource_type",
            "external_resource_id",
            name="uq_app_resource_binding_identity",
        ),
        sa.CheckConstraint("access_revision >= 0", name="ck_app_resource_binding_revision"),
        sa.CheckConstraint(
            "access_state IN ('unknown','granted','denied','revoked')", name="ck_app_resource_binding_access"
        ),
        sa.CheckConstraint(
            "health IN ('unknown','healthy','degraded','unavailable')", name="ck_app_resource_binding_health"
        ),
    )
    op.create_index("ix_app_resource_binding_board_binding_id", "app_resource_binding", ["board_binding_id"])
    op.create_index("ix_app_resource_binding_connection_id", "app_resource_binding", ["connection_id"])


def downgrade():
    op.drop_table("app_resource_binding")
