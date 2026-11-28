from typing import TYPE_CHECKING
from fastapi import status
from jwt import InvalidTokenError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from ..core.security import AuthSecurity
from ..core.security.CollaborationChannel import CollaborationChannel
from ..core.utils.decorators import staticclass
from ..domain.models import Bot, User
from ..Env import Env
from ..security import Auth, OidcMcpIdentity


if TYPE_CHECKING:
    from ..domain.services import DomainService


@staticclass
class MiddlewareHelper:
    @staticmethod
    async def log_api_key_usage(
        app: ASGIApp, scope: Scope, receive: Receive, send: Send, domain_service: "DomainService"
    ) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        if not MiddlewareHelper._is_api_key_used(headers):
            await app(scope, receive, send)
            return

        status_holder = {"status": None}

        # Wrap send to capture status code
        async def send_wrapper(message: Message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await app(scope, receive, send_wrapper)

        # Log API key usage after request completes
        api_key = scope.get("api_key")
        if status_holder["status"] is None or not api_key:
            return

        try:
            ip_address = AuthSecurity.resolve_client_ip((scope.get("client") or (None,))[0], headers)

            status_code = status_holder["status"]
            is_success = status.HTTP_200_OK <= status_code < status.HTTP_400_BAD_REQUEST

            await run_in_threadpool(
                domain_service.api_key.log_usage,
                api_key=api_key,
                endpoint=scope["path"],
                method=scope["method"],
                ip_address=ip_address,
                status_code=status_code,
                is_success=is_success,
            )
        except Exception:
            pass

    @staticmethod
    def validate_auth(scope: Scope, *, allow_oidc: bool = False) -> User | Bot | int:
        scope.pop("oidc_claims", None)
        # Reset untrusted/preexisting hints before validating any credential.
        scope["collaboration_channel"] = CollaborationChannel.Api
        headers = Headers(scope=scope)
        client_ip = AuthSecurity.resolve_client_ip((scope.get("client") or (None,))[0], headers)
        if headers.get(AuthSecurity.API_TOKEN_HEADER, headers.get(AuthSecurity.API_TOKEN_HEADER.lower())):
            validation_result = Auth.validate_user_by_api_token(headers)
            if isinstance(validation_result, User):
                scope["auth"] = validation_result
                return validation_result

            validation_result = Auth.validate_bot(headers, client_ip)
            if isinstance(validation_result, Bot):
                scope["collaboration_channel"] = CollaborationChannel.Bot
                scope["auth"] = validation_result
                return validation_result
            return validation_result

        if headers.get(AuthSecurity.API_KEY_HEADER, headers.get(AuthSecurity.API_KEY_HEADER.lower())):
            validation_result = Auth.validate_user_by_api_key(headers, client_ip)
            if isinstance(validation_result, tuple):
                user, api_key = validation_result
                scope["auth"] = user
                scope["api_key"] = api_key
                return user

            return validation_result

        validation_result = Auth.validate(headers)
        if isinstance(validation_result, User):
            # Native session requires both the verified bearer and refresh cookie.
            # An API credential with a session fallback is still an API call.
            if not MiddlewareHelper._is_api_key_used(headers):
                scope["collaboration_channel"] = CollaborationChannel.HumanUI
            scope["auth"] = validation_result
            return validation_result

        if allow_oidc:
            authorization = headers.get("authorization", "").split(" ", maxsplit=1)
            if len(authorization) == 2 and authorization[0].lower() == "bearer":
                try:
                    user, claims = OidcMcpIdentity.resolve_oidc_mcp_identity(authorization[1])
                    scope["auth"] = user
                    scope["oidc_claims"] = claims
                    return user
                except (PermissionError, InvalidTokenError):
                    pass
            return validation_result

        oidc_user = MiddlewareHelper._validate_oidc_user(headers)
        if oidc_user:
            scope["auth"] = oidc_user
            return oidc_user

        return validation_result

    @staticmethod
    def _validate_oidc_user(headers: Headers) -> User | None:
        """Resolve a resource-scoped OIDC token through an explicit identity link."""

        if not Env.OIDC_BEARER_ENABLED:
            return None
        authorization = headers.get(AuthSecurity.AUTHORIZATION_HEADER, "")
        scheme, separator, token = authorization.partition(" ")
        token = token.strip()
        if not separator or scheme.lower() != "bearer" or not token:
            return None
        try:
            user, _ = OidcMcpIdentity.resolve_oidc_mcp_identity(token)
            return user
        except (PermissionError, InvalidTokenError):
            return None

    @staticmethod
    def _is_api_key_used(headers: Headers) -> bool:
        return bool(headers.get(AuthSecurity.API_KEY_HEADER, headers.get(AuthSecurity.API_KEY_HEADER.lower())))
