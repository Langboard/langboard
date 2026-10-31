"""Persist installation requests independently from central installs and board grants."""

import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db import SnowflakeIDType


revision = "bdef234a5678"
down_revision = "acde123f4567"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_installation_request",
        sa.Column("id", SnowflakeIDType(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requester_id", SnowflakeIDType(), nullable=False),
        sa.Column("request_key", sa.String(), nullable=False),
        sa.Column("project_id", SnowflakeIDType(), nullable=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("artifact", sa.JSON(), nullable=False),
        sa.Column("expected_app_revision", sa.String(), nullable=True),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("installation_status", sa.String(), nullable=False),
        sa.Column("installation_error", sa.String(), nullable=True),
        sa.Column("app", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("reviewer_id", SnowflakeIDType(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.UniqueConstraint("requester_id", "request_key", name="uq_app_installation_request_identity"),
    )
    op.create_index("ix_app_installation_request_requester_id", "app_installation_request", ["requester_id"])
    op.create_index("ix_app_installation_request_decision", "app_installation_request", ["decision"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM app_installation_request LIMIT 1")).first():
        raise RuntimeError("Installation request receipts must be retained; downgrade refused")
    op.drop_table("app_installation_request")
