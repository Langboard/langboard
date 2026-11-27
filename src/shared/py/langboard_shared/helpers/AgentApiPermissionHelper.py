from fastapi import status
from starlette.datastructures import Headers
from ..core.routing import ApiPermission
from ..core.security import AuthSecurity


AGENT_PERMISSION_LEVEL_PERMISSIONS: dict[str, set[str]] = {
    "read": {ApiPermission.Read.value},
    "edit": {ApiPermission.Read.value, ApiPermission.Create.value, ApiPermission.Edit.value},
    "full_access": {
        ApiPermission.Read.value,
        ApiPermission.Create.value,
        ApiPermission.Edit.value,
        ApiPermission.Delete.value,
    },
}


def has_agent_api_token(headers: Headers) -> bool:
    return bool(headers.get(AuthSecurity.API_TOKEN_HEADER) or headers.get(AuthSecurity.API_TOKEN_HEADER.lower()))


def get_agent_allowed_permissions(headers: Headers, default_read: bool = False) -> set[str] | None:
    api_token = headers.get(AuthSecurity.API_TOKEN_HEADER, headers.get(AuthSecurity.API_TOKEN_HEADER.lower(), None))
    if not api_token:
        return AGENT_PERMISSION_LEVEL_PERMISSIONS["read"] if default_read else None

    try:
        payload = AuthSecurity.decode_access_token(api_token)
    except Exception:
        return AGENT_PERMISSION_LEVEL_PERMISSIONS["read"]

    permission_level = payload.get("api_permission_level")
    if not isinstance(permission_level, str):
        permission_level = "read"

    return AGENT_PERMISSION_LEVEL_PERMISSIONS.get(permission_level, AGENT_PERMISSION_LEVEL_PERMISSIONS["read"])


def create_permission_denied_response() -> tuple[dict[str, str], int]:
    return {"message": "Not enough permissions to access this endpoint."}, status.HTTP_403_FORBIDDEN
