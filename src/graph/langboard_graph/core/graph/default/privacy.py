"""Redact structured credentials at graph output and model-context boundaries."""

from typing import Any
from langboard_shared.domain.contracts.audit_events import REDACTED, SENSITIVE_KEYS


_CREDENTIAL_KEYS = SENSITIVE_KEYS | {
    "app_api_token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "api_token",
    "apikey",
    "x_api_key",
    "x_api_token",
    "cookie",
    "set_cookie",
}


def redact_graph_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if any(marker in str(key).lower().replace("-", "_") for marker in _CREDENTIAL_KEYS) else redact_graph_credentials(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_graph_credentials(item) for item in value]
    return value
