"""Merge the wiki attachment index and deadline reminder migration heads."""

from collections.abc import Sequence


revision: str = "7bde71b402af"
down_revision: tuple[str, ...] = ("f729ce7c149b", "8c7b5792ee5e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Converge both migrations on one revision."""


def downgrade() -> None:
    """Return to the two independent migration heads."""
