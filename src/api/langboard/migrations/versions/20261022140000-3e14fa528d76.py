"""Retain immutable creation receipts for native image comments."""

import sqlalchemy as sa
from alembic import op


revision = "3e14fa528d76"
down_revision = "2d03e9417c65"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("card_comment", sa.Column("image_request_uid", sa.String(36), nullable=True))
    op.add_column("card_comment", sa.Column("image_request_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_card_comment_image_request_uid", "card_comment", ["image_request_uid"], unique=True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM card_comment WHERE image_request_uid IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard durable image comment request receipts")
    op.drop_index("ix_card_comment_image_request_uid", table_name="card_comment")
    op.drop_column("card_comment", "image_request_fingerprint")
    op.drop_column("card_comment", "image_request_uid")
