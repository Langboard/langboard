from calendar import timegm
from datetime import timedelta
from functools import lru_cache
from json import dumps as json_dumps
from json import loads as json_loads
from typing import Any, Literal
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from jwt import ExpiredSignatureError, InvalidTokenError
from jwt import decode as jwt_decode
from jwt import encode as jwt_encode
from starlette.datastructures import Headers
from ...Env import Env
from ..filter import AuthFilter
from ..routing.AppExceptionHandlingRoute import AppExceptionHandlingRoute
from ..types import SafeDateTime, SnowflakeID
from ..utils.decorators import staticclass
from ..utils.Encryptor import Encryptor


@staticclass
class AuthSecurity:
    AUTHORIZATION_HEADER = "Authorization"
    REAL_IP_HEADER = "X-Real-IP"
    FORWARDED_FOR_HEADER = "X-Forwarded-For"
    API_TOKEN_HEADER = "X-Api-Token"
    API_KEY_HEADER = "X-Api-Key"
    MCP_TOOL_GROUP_UID_HEADER = "X-MCP-Tool-Group-UID"
    MCP_USER_ASSERTION_HEADER = "X-MCP-User-Assertion"

    @staticmethod
    def authenticate(user_id: SnowflakeID, *, auth_version: int = 0) -> tuple[str, str]:
        """Authenticates the user and returns the access and refresh tokens.

        :param user_id: The user ID.
        :param auth_version: Current primary account generation; zero is the initial generation.
        """
        access_token = AuthSecurity.create_access_token(user_id, auth_version=auth_version)
        refresh_token = AuthSecurity.create_refresh_token(user_id, auth_version=auth_version)

        return access_token, refresh_token

    @staticmethod
    def refresh(token: str) -> str:
        """Refreshes the access token using the refresh token.

        :param token: The refresh token.

        :raises InvalidTokenError: If the token is invalid.
        :raises ExpiredSignatureError: If the signature has expired.
        """
        try:
            payload = AuthSecurity.decode_refresh_token(token)
        except ExpiredSignatureError as e:
            raise e
        except Exception:
            raise InvalidTokenError("Invalid token")

        access_token = AuthSecurity.create_access_token(payload["sub"], auth_version=payload.get("auth_version", 0))
        return access_token

    @staticmethod
    def set_openapi_schema(app: FastAPI):
        if app.openapi_schema:
            openapi_schema = app.openapi_schema
        else:
            openapi_schema = get_openapi(
                title=Env.PROJECT_NAME.capitalize(),
                version=Env.PROJECT_VERSION,
                routes=app.routes,
            )

        if "components" not in openapi_schema:
            openapi_schema["components"] = {}

        if "securitySchemes" not in openapi_schema["components"]:
            openapi_schema["components"]["securitySchemes"] = {}

        auth_schema = {
            "type": "http",
            "scheme": "bearer",
        }

        openapi_schema["components"]["securitySchemes"]["BearerAuth"] = auth_schema

        for route in app.routes:
            if not isinstance(route, AppExceptionHandlingRoute):
                continue
            if AuthFilter.exists(route.endpoint):
                if route.path.count(":") > 0:
                    route_path = route.path.split("/")
                    for i, part in enumerate(route_path):
                        if part.count(":") > 0:
                            route_path[i] = part.split(":", maxsplit=1)[0] + "}"
                    route_path = "/".join(route_path)
                else:
                    route_path = route.path
                path = openapi_schema["paths"][route_path]
                for method in route.methods or ():
                    path_method = path[method.lower()]
                    path_method["security"] = [{"BearerAuth": []}]

        app.openapi_schema = openapi_schema

    @staticmethod
    def get_client_ip(headers: Headers | dict[str, str]) -> str | None:
        real_ip = headers.get(AuthSecurity.REAL_IP_HEADER, headers.get(AuthSecurity.REAL_IP_HEADER.lower()))
        if real_ip:
            return real_ip.strip()
        forwarded_for = headers.get(
            AuthSecurity.FORWARDED_FOR_HEADER, headers.get(AuthSecurity.FORWARDED_FOR_HEADER.lower())
        )
        if forwarded_for:
            ips = [ip.strip() for ip in forwarded_for.split(",")]
            if ips:
                return ips[-1]
        return None

    @staticmethod
    def get_all_client_ips(headers: Headers | dict[str, str]) -> list[str]:
        ips: list[str] = []
        real_ip = headers.get(AuthSecurity.REAL_IP_HEADER, headers.get(AuthSecurity.REAL_IP_HEADER.lower()))
        if real_ip:
            ips.append(real_ip.strip())
        forwarded_for = headers.get(
            AuthSecurity.FORWARDED_FOR_HEADER, headers.get(AuthSecurity.FORWARDED_FOR_HEADER.lower())
        )
        if forwarded_for:
            ips.extend([ip.strip() for ip in forwarded_for.split(",")])
        ips = ",".join(ips).split(",")
        ips = [ip.strip() for ip in ips if ip.strip()]
        return ips

    @staticmethod
    def resolve_client_ip(peer_ip: str | None, headers: Headers | dict[str, str]) -> str | None:
        """Resolve the client IP from the unforgeable peer address plus forwarded
        headers that are only honored when the peer is a trusted proxy."""
        from ...core.utils.IpAddress import resolve_client_ip as _resolve

        forwarded_values = [
            value for name, value in headers.items() if name.lower() == AuthSecurity.FORWARDED_FOR_HEADER.lower()
        ]
        # Uvicorn and Headers select different entries for duplicate field lines.
        # An ambiguous chain must never authorize an IP-restricted credential.
        if len(forwarded_values) > 1:
            return None
        forwarded_for = forwarded_values[0] if forwarded_values else ""
        return _resolve(peer_ip, forwarded_for, Env.TRUSTED_PROXY_IPS)

    @staticmethod
    def create_access_token(user_id: int, *, auth_version: int = 0) -> str:
        payload = AuthSecurity.__create_payload(user_id, timedelta(seconds=Env.JWT_AT_EXPIRATION), auth_version)
        return jwt_encode(payload=payload, key=Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)

    @staticmethod
    def create_bot_one_time_token(
        user_id: int, api_permission_level: Literal["read", "edit", "full_access"] = "read", *, auth_version: int = 0
    ) -> str:
        if user_id <= 0 or api_permission_level not in ("read", "edit", "full_access"):
            raise ValueError("Invalid bot token owner or permission level")

        payload = AuthSecurity.__create_payload(user_id, timedelta(minutes=5), auth_version)
        payload["internal"] = "bot"
        payload["api_permission_level"] = api_permission_level
        return jwt_encode(payload=payload, key=Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)

    @staticmethod
    def create_refresh_token(user_id: int, *, auth_version: int = 0) -> str:
        payload = AuthSecurity.__create_payload(user_id, timedelta(days=Env.JWT_RT_EXPIRATION), auth_version)
        return Encryptor.encrypt(json_dumps(payload), Env.JWT_SECRET_KEY)

    @staticmethod
    def compare_tokens(access_token: str | None, refresh_token: str | None) -> bool | Literal["expired_access"]:
        if not access_token or not refresh_token:
            return False

        try:
            access_payload = AuthSecurity.decode_access_token(access_token)
        except ExpiredSignatureError:
            return "expired_access"
        except Exception:
            return False

        try:
            refresh_payload = AuthSecurity.decode_refresh_token(refresh_token)
        except Exception:
            return False

        return (
            access_payload["sub"] == refresh_payload["sub"]
            and access_payload["iss"] == refresh_payload["iss"]
            and access_payload.get("auth_version", 0) == refresh_payload.get("auth_version", 0)
        )

    @staticmethod
    def decode_access_token(token: str) -> dict[str, Any]:
        payload = jwt_decode(jwt=token, key=Env.JWT_SECRET_KEY, algorithms=[Env.JWT_ALGORITHM], issuer=Env.PROJECT_NAME)
        AuthSecurity.__validate_payload(payload)
        return payload

    @staticmethod
    def decode_refresh_token(token: str) -> dict[str, Any]:
        # Recheck time on every use; this cache never authorizes an account.
        payload = json_loads(AuthSecurity.__decode_refresh_payload(token, Env.JWT_SECRET_KEY))
        AuthSecurity.__validate_payload(payload)
        return payload

    @staticmethod
    @lru_cache(maxsize=128)
    def __decode_refresh_payload(token: str, signing_key: str) -> str:
        # A cookie is reused across requests. Avoid repeating scrypt for the
        # same authenticated ciphertext; a changed key or token is a cache miss.
        # Only valid payloads enter this bounded process-local cache.
        plaintext = Encryptor.decrypt(token, signing_key)
        payload = json_loads(plaintext)
        AuthSecurity.__validate_payload(payload)
        return plaintext

    @staticmethod
    def __create_payload(user_id: int, expiration: timedelta, auth_version: int = 0) -> dict[str, Any]:
        if type(auth_version) is not int or auth_version < 0:
            raise ValueError("Invalid authentication version")
        expiry = SafeDateTime.now() + expiration
        return {
            "sub": str(user_id),
            "exp": timegm(expiry.utctimetuple()),
            "iss": Env.PROJECT_NAME,
            "auth_version": auth_version,
        }

    @staticmethod
    def __validate_payload(payload: dict[str, Any]):
        if not isinstance(payload, dict):
            raise InvalidTokenError("Invalid token")
        if "sub" not in payload or "iss" not in payload or "exp" not in payload:
            raise InvalidTokenError("Invalid token structure")
        if (
            payload["iss"] != Env.PROJECT_NAME
            or not isinstance(payload["sub"], str)
            or not isinstance(payload["exp"], int)
        ):
            raise InvalidTokenError("Invalid token structure")
        if payload["exp"] <= timegm(SafeDateTime.now().utctimetuple()):
            raise ExpiredSignatureError("Signature has expired")
        auth_version = payload.get("auth_version", 0)
        if type(auth_version) is not int or auth_version < 0:
            raise InvalidTokenError("Invalid token structure")
