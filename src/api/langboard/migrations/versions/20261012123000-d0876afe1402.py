"""Join applied canary history with upstream Phoenix notification migrations.

No schema is recreated and no applied revision is stamped or replaced.
"""

revision = "d0876afe1402"
down_revision = ("c97659de63f4", "0cc6abc4b020")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
