"""Authored next action text; empty input explicitly clears the field."""

from typing import Annotated
from pydantic import BeforeValidator, Field


NEXT_ACTION_MAX_LENGTH = 1000


def normalize_next_action(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > NEXT_ACTION_MAX_LENGTH:
        raise ValueError("Next action must be text of at most 1000 characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("Next action contains unsupported control characters")
    return value.strip() or None


NextAction = Annotated[
    Annotated[str, Field(max_length=NEXT_ACTION_MAX_LENGTH)] | None, BeforeValidator(normalize_next_action)
]
