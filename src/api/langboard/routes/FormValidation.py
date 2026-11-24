"""Shared API form validation with the existing public error contract."""

from re import match
from langboard_shared.core.routing.Exception import MissingException, ValidationFailureException, ValidationFailureInfo
from ..Constants import EMAIL_REGEX


def validate_email_address(value: str) -> str:
    if not value:
        raise MissingException("body", "email", {"email": value})
    if not bool(match(EMAIL_REGEX, value)):
        raise ValidationFailureException(ValidationFailureInfo(loc="body", field="email", inputs={"email": value}))
    return value
