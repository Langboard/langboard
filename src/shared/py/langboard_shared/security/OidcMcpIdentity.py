"""Resolve a verified OIDC bearer against current primary identity records."""

from typing import Any
from ..core.db import DbSession, SqlBuilder
from ..core.security import OidcClient
from ..domain.models import IdentityProvider, User, UserIdentityLink


def current_oidc_user(claims: dict[str, Any]) -> User:
    """A replica must never extend a revoked link or a deactivated account."""
    issuer = str(claims.get("iss", "")).strip().rstrip("/")
    subject = claims.get("sub")
    if not issuer or not isinstance(subject, str) or not subject:
        raise PermissionError("OIDC identity is not linked")
    with DbSession.use(readonly=False) as db:
        user = db.exec(
            SqlBuilder.select.table(User)
            .join(UserIdentityLink, UserIdentityLink.user_id == User.id)
            .where(
                UserIdentityLink.provider == IdentityProvider.Oidc,
                UserIdentityLink.external_id == subject,
                UserIdentityLink.issuer == issuer,
                User.activated_at.is_not(None),
                User.deleted_at.is_(None),
            )
            .limit(1)
        ).first()
    if user is None:
        raise PermissionError("Current OIDC account identity unavailable")
    return user


def resolve_oidc_mcp_identity(token: str) -> tuple[User, dict[str, Any]]:
    claims = OidcClient.validate_access_token(token)
    if claims.get("typ", "Bearer") != "Bearer":
        raise PermissionError("OIDC access token required")
    return current_oidc_user(claims), claims
