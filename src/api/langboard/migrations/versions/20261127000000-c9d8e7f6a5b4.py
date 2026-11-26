"""Merge the project identity and attachment index migration heads."""

from collections.abc import Sequence


revision: str = "c9d8e7f6a5b4"
down_revision: tuple[str, ...] = ("0813b9a8721d", "7bde71b402af")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Converge the existing heads without changing retained data."""


def downgrade() -> None:
    """Restore the two predecessor heads."""
