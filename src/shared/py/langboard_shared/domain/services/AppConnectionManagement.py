"""Explicit owner-managed inbound identities for approved independent apps."""

from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...publishers import AppSettingPublisher
from ..models import AppConnection, AppConnectionCredential, AppDefinition
from .AppConnectionAuthentication import _identity_hash, is_native_inbound_connection
from .AppGovernance import (
    AppGovernanceDenied,
    _actor,
    _authorize,
    current_policy,
)
from .AppMcpConnectionConsent import _context
from .AppRegistry import AppRegistryConflict


def _definition(db, key, revision):
    row = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key).with_for_update()).first()
    if row is None or not row.is_enabled:
        raise AppGovernanceDenied()
    if row.edit_revision() != revision:
        raise AppRegistryConflict()
    return row


def create_inbound_connection(actor, key, app_revision, organization_id=None):
    """Register a host credential principal, not an authenticated upstream account.

    No destination, credential reference, upstream identity or grants are accepted.
    Personal credentials remain owned by the caller even for instance admins.
    """
    with DbSession.atomic() as db:
        actor = _actor(db, actor)
        if organization_id is not None:
            _authorize(db, actor, organization_id)
        if current_policy(db, organization_id)["effective_mode"] == "disabled":
            raise AppGovernanceDenied()
        definition = _definition(db, key, app_revision)
        connection = AppConnection(
            app_key=key,
            owner_id=actor.id,
            organization_id=organization_id,
            ownership="organization" if organization_id is not None else "personal",
            state="connected",
        )
        db.insert(connection)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {
            "connection_uid": connection.get_uid(),
            "ownership": connection.ownership,
            "state": connection.state,
            "revision": _identity_hash(db, connection, definition=definition),
        }


def disconnect_inbound_connection(actor, connection_id, expected_revision):
    with DbSession.atomic() as db:
        connection, definition, consent = _context(db, actor, connection_id)
        if not is_native_inbound_connection(connection):
            raise AppGovernanceDenied()
        if _identity_hash(db, connection, definition=definition) != expected_revision:
            raise AppRegistryConflict()
        connection.state = "disconnected"
        db.update(connection)
        db.exec(
            update(AppConnectionCredential)
            .where(
                AppConnectionCredential.connection_id == connection.id,
                AppConnectionCredential.revoked_at.is_(None),
            )
            .values(revoked_at=SafeDateTime.now())
        )
        if consent is not None and consent.scope_tools:
            consent.scope_tools = {}
            consent.revision += 1
            db.update(consent)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {
            "connection_uid": connection.get_uid(),
            "state": connection.state,
            "revision": _identity_hash(db, connection, definition=definition),
        }


def list_inbound_connections(actor, key, *, organization_id=None, after_id=None, limit=25):
    """Recover management receipts in one explicit ownership scope, even after disable."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Connection page limit must be 1 to 50")
    with DbSession.atomic() as db:
        actor = _actor(db, actor)
        statement = SqlBuilder.select.table(AppConnection).where(AppConnection.app_key == key)
        if organization_id is None:
            statement = statement.where(AppConnection.ownership == "personal", AppConnection.owner_id == actor.id)
        else:
            _authorize(db, actor, organization_id)
            statement = statement.where(
                AppConnection.ownership == "organization", AppConnection.organization_id == organization_id
            )
        if after_id is not None:
            statement = statement.where(AppConnection.id > after_id)
        statement = statement.where(
            AppConnection.credential_reference.is_(None),
            AppConnection.instance_url == "",
            AppConnection.external_account_id == "",
        )
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key)).first()
        if definition is None:
            raise AppGovernanceDenied()
        rows = db.exec(statement.order_by(AppConnection.id).limit(limit + 1)).all()
        page = rows[:limit]
        return {
            "items": [
                {
                    "connection_uid": row.get_uid(),
                    "ownership": row.ownership,
                    "state": row.state,
                    "revision": _identity_hash(db, row, definition=definition),
                }
                for row in page
            ],
            "next_cursor": page[-1].get_uid() if len(rows) > limit else None,
        }
