"""Explicit administrator-owned app grouping without identity migration."""

import sqlalchemy as sa
from alembic import op


revision = "e71b63c8a204"
down_revision = "d3c6fd51a7e9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_composition",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("approved_by", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
    )
    op.create_index("ix_app_composition_key", "app_composition", ["key"], unique=True)
    op.create_table(
        "app_composition_member",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("composition_id", sa.BigInteger(), sa.ForeignKey("app_composition.id"), nullable=False),
        sa.Column("module_key", sa.String(), nullable=False, unique=True),
        sa.Column("order", sa.Integer(), nullable=False),
    )
    op.create_index("ix_app_composition_member_composition_id", "app_composition_member", ["composition_id"])
    op.create_index("ix_app_composition_member_module_key", "app_composition_member", ["module_key"], unique=True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_composition LIMIT 1")).first():
        raise RuntimeError("Ungroup approved compositions before removing the grouping schema")
    op.drop_table("app_composition_member")
    op.drop_table("app_composition")
