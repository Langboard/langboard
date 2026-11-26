"""Validate and persist explicit browser subscriptions at the user/session boundary."""

import json
import re
from base64 import urlsafe_b64decode
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy.exc import IntegrityError
from ...core.db import DbSession, SqlBuilder
from ...core.security import AuthSecurity
from ...core.utils.Encryptor import Encryptor
from ...Env import Env
from ..models.User import User
from ..models.UserWebPushSubscription import UserWebPushSubscription


_CHROME_ENDPOINT = re.compile(r"https://fcm\.googleapis\.com/fcm/send/[A-Za-z0-9_:-]{1,2048}")


def _decode_key(value: str, size: int) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value) or len(value.rstrip("=")) != (size * 8 + 5) // 6:
        raise ValueError("Invalid push key")
    decoded = urlsafe_b64decode(value.rstrip("=") + "=" * (-len(value.rstrip("=")) % 4))
    if len(decoded) != size:
        raise ValueError("Invalid push key")
    return decoded


def _validate_point(value: str) -> None:
    point = _decode_key(value, 65)
    if point[0] != 4:
        raise ValueError("Invalid push key")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)


class WebPushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    p256dh: SecretStr
    auth: SecretStr

    @field_validator("p256dh")
    @classmethod
    def validate_p256dh(cls, value: SecretStr) -> SecretStr:
        _validate_point(value.get_secret_value())
        return value

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, value: SecretStr) -> SecretStr:
        _decode_key(value.get_secret_value(), 16)
        return value


class WebPushRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    endpoint: SecretStr
    keys: WebPushKeys

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: SecretStr) -> SecretStr:
        if not _CHROME_ENDPOINT.fullmatch(value.get_secret_value()):
            raise ValueError("Unsupported browser push endpoint")
        return value


class WebPushSessionDenied(ValueError):
    pass


class WebPushEndpointConflict(ValueError):
    pass


def browser_push_public_key() -> str | None:
    config = browser_push_vapid_config()
    return config[0] if config else None


def browser_push_vapid_config() -> tuple[str, str, str] | None:
    if Env.get_from_env("CARD_REMINDER_WEB_PUSH_ENABLED", "false") != "true":
        return None
    public = Env.get_from_env("CARD_REMINDER_WEB_PUSH_PUBLIC_KEY", "")
    private = Env.get_from_env("CARD_REMINDER_WEB_PUSH_PRIVATE_KEY", "").replace("\\n", "\n")
    contact = Env.get_from_env("CARD_REMINDER_WEB_PUSH_CONTACT", "")
    return (public, private, contact) if _valid_vapid_configuration(public, private, contact) else None


@lru_cache(maxsize=1)
def _valid_vapid_configuration(public: str, private: str, contact: str) -> bool:
    if not re.fullmatch(r"mailto:[^\s@]+@[^\s@]+", contact):
        return False
    try:
        point = _decode_key(public, 65)
        key = load_pem_private_key(private.encode(), None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return False
    return (
        isinstance(key, ec.EllipticCurvePrivateKey)
        and isinstance(key.curve, ec.SECP256R1)
        and key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint) == point
    )


def _refresh_owner(token: str, user: User | None) -> tuple[int, dict]:
    try:
        payload = AuthSecurity.decode_refresh_token(token)
        user_id = int(payload["sub"])
    except Exception as exc:
        raise WebPushSessionDenied() from exc
    if user is not None and (int(user.id) != user_id or user.auth_version != payload.get("auth_version", 0)):
        raise WebPushSessionDenied()
    return user_id, payload


def browser_push_subscribed(token: str, user: User) -> bool:
    user_id, _ = _refresh_owner(token, user)
    with DbSession.use(readonly=False) as db:
        return (
            db.exec(
                SqlBuilder.select.column(UserWebPushSubscription.id).where(
                    UserWebPushSubscription.user_id == user_id,
                    UserWebPushSubscription.session_digest == sha256(token.encode()).hexdigest(),
                    UserWebPushSubscription.signed_out.is_(False),
                    UserWebPushSubscription.endpoint_digest.is_not(None),
                )
            ).first()
            is not None
        )


def change_browser_push(
    token: str, *, user: User | None = None, registration: WebPushRegistration | None = None
) -> None:
    user_id, payload = _refresh_owner(token, user)
    digest = sha256(token.encode()).hexdigest()
    try:
        with DbSession.atomic() as db:
            owner = db.exec(SqlBuilder.select.table(User).where(User.id == user_id).with_for_update()).first()
            if (
                owner is None
                or owner.activated_at is None
                or owner.deleted_at is not None
                or owner.auth_version != payload.get("auth_version", 0)
            ):
                raise WebPushSessionDenied()
            subscription = db.exec(
                SqlBuilder.select.table(UserWebPushSubscription).where(
                    UserWebPushSubscription.user_id == owner.id,
                    UserWebPushSubscription.session_digest == digest,
                )
            ).first()
            is_new = subscription is None
            if subscription is None:
                if registration is None and (user is not None or browser_push_public_key() is None):
                    return
                subscription = UserWebPushSubscription(
                    user_id=owner.id,
                    session_digest=digest,
                    auth_version=owner.auth_version,
                    session_expires_at=datetime.fromtimestamp(payload["exp"], UTC),
                )
            if registration is not None:
                if subscription.signed_out or subscription.auth_version != owner.auth_version:
                    raise WebPushSessionDenied()
                endpoint = registration.endpoint.get_secret_value()
                subscription.endpoint_digest = sha256(endpoint.encode()).hexdigest()
                subscription.encrypted_subscription = Encryptor.encrypt(
                    json.dumps(
                        {
                            "endpoint": endpoint,
                            "keys": {
                                "p256dh": registration.keys.p256dh.get_secret_value(),
                                "auth": registration.keys.auth.get_secret_value(),
                            },
                        }
                    ),
                    Env.COMMON_SECRET_KEY,
                )
            else:
                subscription.endpoint_digest = None
                subscription.encrypted_subscription = None
                if user is None:
                    subscription.signed_out = True
            if is_new:
                db.insert(subscription)
            else:
                db.update(subscription)
    except IntegrityError as exc:
        raise WebPushEndpointConflict() from exc


def revoke_browser_push_on_signout(token: str | None) -> None:
    if token is None:
        return
    try:
        change_browser_push(token)
    except WebPushSessionDenied:
        # An invalid or expired refresh cookie has no push authority.
        return
