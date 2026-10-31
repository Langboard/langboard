from hmac import compare_digest
from typing import Any, Literal, TypeVar, overload
from fastapi import Depends, Request, status
from jwt import ExpiredSignatureError, InvalidTokenError
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from ..core.caching import Cache
from ..core.db import BaseDbModel, DbSession, SqlBuilder
from ..core.routing import ApiErrorCode, ApiException
from ..core.security import AuthSecurity, KeyVault
from ..core.types import SnowflakeID
from ..core.utils.decorators import staticclass
from ..core.utils.IpAddress import has_allowed_ips
from ..domain.models import ApiKeySetting, Bot, User
from ..domain.models.BaseBotModel import BaseBotModel, BotPlatform
from ..Env import Env


_TModel = TypeVar("_TModel", bound=BaseDbModel)


@staticclass
class Auth:
    @staticmethod
    def __get_model_by_columns(model_cls: type[_TModel], *where: Any) -> _TModel | None:
        statement = SqlBuilder.select.table(model_cls)
        if where:
            statement = statement.where(*where)
        statement = statement.limit(1)

        # Authentication must observe revocation on the primary, not replica lag.
        with DbSession.use(readonly=False) as db:
            return db.exec(statement).first()

    @overload
    @staticmethod
    def scope(where: Literal["all"]) -> User | Bot: ...
    @overload
    @staticmethod
    def scope(where: Literal["user"]) -> User: ...
    @overload
    @staticmethod
    def scope(where: Literal["bot"]) -> Bot: ...
    @staticmethod
    def scope(where: Literal["all", "user", "bot"]) -> User | Bot:
        """Creates a scope for the user to be used in :class:`fastapi.FastAPI` endpoints."""
        if where in {"all", "user", "bot"}:

            def get_user_or_bot(req: Request) -> User | Bot | None:  # type: ignore
                return req.auth

        else:
            raise ValueError("Auth.scope must be called with either 'all', 'user', or 'bot'")

        return Depends(get_user_or_bot)

    @staticmethod
    def get_user_by_token(token: str) -> User | InvalidTokenError | ExpiredSignatureError | None:
        """Gets the user from the given token.

        :param token: The token to get the user from.

        :return User: The user if the token is valid.
        :return InvalidTokenError: If the token is invalid.
        :return ExpiredSignatureError: If the signature has expired.
        :return None: If the user could not be found.
        """
        try:
            payload = AuthSecurity.decode_access_token(token)
        except ExpiredSignatureError as e:
            return e
        except Exception:
            return InvalidTokenError("Invalid token")

        try:
            user_id = int(payload["sub"])
            if user_id <= 0:
                raise Exception()
        except Exception:
            return InvalidTokenError("Invalid token")

        user = Auth.get_user_by_id(user_id)
        if isinstance(user, User) and not Auth.is_current_auth_version(user, payload):
            return InvalidTokenError("Invalid token")
        return user

    @staticmethod
    def is_current_auth_version(user: User, payload: dict[str, Any]) -> bool:
        """Compare a verified payload with the current primary account record."""
        version = payload.get("auth_version", 0)
        return type(version) is int and version == user.auth_version

    @staticmethod
    def get_user_by_id(user_id: int) -> User | InvalidTokenError | None:
        """Gets the user from the given ID.

        Recheck current primary account state; cached profiles cannot authorize.

        :param user_id: The user ID to get the user from.

        :return User: The user if the user exists.
        :return None: If the user does not exist.
        """
        try:
            user = Auth.__get_model_by_columns(User, User.column("id") == user_id)
            if not user or user.activated_at is None or user.deleted_at is not None:
                return InvalidTokenError("Invalid token")
        except Exception:
            return None

        return user

    @staticmethod
    def get_bot_by_api_token(api_token: str) -> Bot | None:
        """Gets the bot from the given API token.

        Recheck current primary bot state; cached profiles cannot authorize.

        :param api_token: The API token to get the bot from.

        :return Bot: The bot if the bot exists.
        :return None: If the bot does not exist.
        """
        try:
            bot = Auth.__get_model_by_columns(Bot, Bot.column("app_api_token") == api_token)
            if not bot:
                return None
        except Exception:
            return None

        return bot

    @staticmethod
    def reset_user(user: User) -> None:
        """Invalidates the legacy user authentication cache.

        :param user: The user to reset.
        """
        if user.is_new():
            return

        cache_key = f"auth-user-{user.id}"
        Cache.delete(cache_key)

    @staticmethod
    def validate(queries_headers: dict[Any, Any] | Headers) -> User | Literal[401, 422]:
        """Validates the given headers or queries and returns the user if the token is valid.

        :param headers: The headers to validate.

        :return User: The user if the token is valid.
        :return 401: If the token is invalid. :class:`fastapi.status.HTTP_401_UNAUTHORIZED`
        :return 422: If the signature has expired. :class:`fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT`
        """
        authorization = queries_headers.get(
            AuthSecurity.AUTHORIZATION_HEADER, queries_headers.get(AuthSecurity.AUTHORIZATION_HEADER.lower(), None)
        )
        if not authorization:
            return status.HTTP_401_UNAUTHORIZED

        if isinstance(queries_headers, Headers):
            authorization_schemas = authorization.split(" ", maxsplit=1)
            if len(authorization_schemas) != 2:
                return status.HTTP_401_UNAUTHORIZED
            access_token_scheme, access_token = authorization_schemas
            if access_token_scheme.lower() != "bearer":
                return status.HTTP_401_UNAUTHORIZED

            cookie = cookie_parser(queries_headers.get("cookie", ""))
            refresh_token = cookie.get(Env.REFRESH_TOKEN_NAME)
            compared_result = AuthSecurity.compare_tokens(access_token, refresh_token)
            if compared_result == "expired_access":
                return status.HTTP_422_UNPROCESSABLE_CONTENT
            if not compared_result:
                return status.HTTP_401_UNAUTHORIZED
        else:
            if authorization.startswith("Bearer "):
                access_token = authorization.split("Bearer ", maxsplit=1)[1]
            elif authorization.startswith("bearer "):
                access_token = authorization.split("bearer ", maxsplit=1)[1]
            else:
                access_token = authorization

        if not access_token:
            return status.HTTP_401_UNAUTHORIZED

        user = Auth.get_user_by_token(access_token)
        if isinstance(user, User):
            return user
        elif isinstance(user, ExpiredSignatureError):
            return status.HTTP_422_UNPROCESSABLE_CONTENT
        else:
            return status.HTTP_401_UNAUTHORIZED

    @staticmethod
    def validate_bot(headers: Headers, client_ip: str | None = None) -> Bot | Literal[401]:
        """client_ip must be resolved via AuthSecurity.resolve_client_ip (peer +
        trusted proxies). None means it could not be established: IP-restricted
        keys fail closed."""
        ips = [client_ip] if client_ip else []
        api_token = headers.get(AuthSecurity.API_TOKEN_HEADER, headers.get(AuthSecurity.API_TOKEN_HEADER.lower(), None))
        if not api_token:
            return status.HTTP_401_UNAUTHORIZED

        bot = Auth.get_bot_by_api_token(api_token)
        if not bot:
            return status.HTTP_401_UNAUTHORIZED

        allowed_all_ips = BaseBotModel.ALLOWED_ALL_IPS_BY_PLATFORMS.get(BotPlatform(bot.platform), [])
        if Env.ENVIRONMENT == "development" or bot.platform_running_type in allowed_all_ips:
            return bot

        if has_allowed_ips(bot.ip_whitelist, ips):
            return bot
        return status.HTTP_401_UNAUTHORIZED

    @staticmethod
    def validate_user_by_api_token(headers: Headers) -> User | Literal[401]:
        api_token = headers.get(AuthSecurity.API_TOKEN_HEADER, headers.get(AuthSecurity.API_TOKEN_HEADER.lower(), None))
        if not api_token:
            return status.HTTP_401_UNAUTHORIZED

        try:
            payload = AuthSecurity.decode_access_token(api_token)
            if "sub" not in payload or "internal" not in payload or not payload["sub"] or not payload["internal"]:
                return status.HTTP_401_UNAUTHORIZED
            user_id = int(payload["sub"])
        except Exception:
            return status.HTTP_401_UNAUTHORIZED

        try:
            user = Auth.get_user_by_id(user_id)
            if not isinstance(user, User) or not Auth.is_current_auth_version(user, payload):
                return status.HTTP_401_UNAUTHORIZED
        except Exception:
            return status.HTTP_401_UNAUTHORIZED

        return user

    @staticmethod
    def validate_user_by_api_key(
        headers: Headers, client_ip: str | None = None
    ) -> tuple[User, ApiKeySetting] | Literal[401, 403]:
        """See validate_bot for the client_ip contract (fail-closed on None)."""
        ips = [client_ip] if client_ip else []
        key_material = headers.get(AuthSecurity.API_KEY_HEADER, headers.get(AuthSecurity.API_KEY_HEADER.lower(), None))
        if not key_material:
            return status.HTTP_401_UNAUTHORIZED

        # Validate API key format
        if not key_material.startswith("sk-"):
            return status.HTTP_401_UNAUTHORIZED

        key_reference, separator, actual_key = key_material[3:].partition(".")

        try:
            with DbSession.use(readonly=False) as db:
                if separator:
                    key_id = SnowflakeID.from_short_code(key_reference)
                    result = db.exec(
                        SqlBuilder.select.table(ApiKeySetting)
                        .where(ApiKeySetting.column("id") == key_id)
                        .where(ApiKeySetting.column("activated_at").is_not(None))
                        .limit(1)
                    )
                    candidate = result.first()
                    api_keys = [candidate] if isinstance(candidate, ApiKeySetting) else []
                else:
                    result = db.exec(
                        SqlBuilder.select.table(ApiKeySetting).where(ApiKeySetting.column("activated_at").is_not(None))
                    )
                    api_keys = result.all()

                api_key_setting = None
                for api_key in api_keys:
                    try:
                        stored_key = KeyVault.get_key(api_key.value)
                        if not compare_digest(stored_key, actual_key):
                            continue
                        if api_key.is_expired():
                            return status.HTTP_401_UNAUTHORIZED
                        api_key_setting = api_key
                        break
                    except Exception:
                        continue

                if not api_key_setting:
                    return status.HTTP_401_UNAUTHORIZED
        except Exception:
            return status.HTTP_401_UNAUTHORIZED

        # Verify the current account before reporting an IP authorization denial.
        result = Auth.get_user_by_id(api_key_setting.user_id)
        if not result or isinstance(result, InvalidTokenError):
            return status.HTTP_401_UNAUTHORIZED
        if Env.ENVIRONMENT != "development" and not has_allowed_ips(api_key_setting.ip_whitelist, ips):
            return status.HTTP_403_FORBIDDEN
        return result, api_key_setting

    @staticmethod
    def ensure_scim_authorized(headers: dict[Any, Any] | Headers) -> None:
        if not Env.SCIM_ENABLED:
            raise ApiException.NotFound_404()

        expected = Env.SCIM_BEARER_TOKEN.strip()
        if not expected:
            raise ApiException.ServiceUnavailable_503(ApiErrorCode.OP0000)

        authorization = headers.get(
            AuthSecurity.AUTHORIZATION_HEADER,
            headers.get(AuthSecurity.AUTHORIZATION_HEADER.lower(), None),
        )
        if not authorization:
            raise ApiException.Unauthorized_401(ApiErrorCode.AU1001)

        schemes = str(authorization).split(" ", maxsplit=1)
        if len(schemes) != 2 or schemes[0].lower() != "bearer":
            raise ApiException.Unauthorized_401(ApiErrorCode.AU1001)

        token = schemes[1].strip()
        if token != expected:
            raise ApiException.Unauthorized_401(ApiErrorCode.AU1001)
