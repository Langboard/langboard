"""Independent native urgency; unrelated to workflow or derived work ordering."""

from typing import Literal


CardPriority = Literal["P0", "P1", "P2", "P3"]


def validate_card_priority(value: object) -> CardPriority:
    if not isinstance(value, str) or value not in ("P0", "P1", "P2", "P3"):
        raise ValueError("Card priority must be P0, P1, P2, or P3")
    return value
