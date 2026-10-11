"""Explicit owner consent to the current external MCP identity and tool scopes."""
from ...core.db import DbSession, SqlBuilder
from ..models import AppConnection, AppDefinition, AppMcpConsent
from .AppConnectionAuthentication import _identity_hash, _manage
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, current_policy


MCP_SCOPES = ("read", "write", "automation")


def _scope_tools(declaration):
    return {scope: sorted(tool["name"] for tool in declaration.get("mcp", {}).get("tools", [])
                         if tool["authority_scope"] == scope) for scope in MCP_SCOPES}


def effective_mcp_consent(db, connection, definition):
    if (connection.state != "connected" or not definition.is_enabled
        or current_policy(db, connection.organization_id)["effective_mode"] == "disabled"):
        return []
    retained = db.exec(SqlBuilder.select.table(AppMcpConsent).where(AppMcpConsent.connection_id == connection.id)).first()
    if retained is None or retained.identity_hash != _identity_hash(db, connection, definition=definition):
        return []
    current = _scope_tools(definition.declaration)
    return [scope for scope, tools in retained.scope_tools.items() if scope in current and tools == current[scope]]


def revoke_mcp_consents(db, app_key, scopes=MCP_SCOPES):
    connection_ids = SqlBuilder.select.table(AppConnection.id).where(AppConnection.app_key == app_key)
    for consent in db.exec(SqlBuilder.select.table(AppMcpConsent)
                           .where(AppMcpConsent.connection_id.in_(connection_ids)).with_for_update()).all():
        retained = {scope: tools for scope, tools in consent.scope_tools.items() if scope not in scopes}
        if retained != consent.scope_tools:
            consent.scope_tools = retained
            consent.revision += 1
            db.update(consent)


def _context(db, actor, connection_id):
    connection = _current(db, AppConnection, connection_id)
    if connection is None:
        raise AppGovernanceDenied()
    _manage(db, actor, connection)  # An instance admin cannot approve another person's private connection.
    definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == connection.app_key).with_for_update()).first()
    if definition is None:
        raise AppGovernanceDenied()
    row = db.exec(SqlBuilder.select.table(AppMcpConsent).where(AppMcpConsent.connection_id == connection.id).with_for_update()).first()
    return connection, definition, row


def _receipt(db, connection, definition, row):
    return {"connection_uid": connection.get_uid(), "revision": row.revision if row else None,
            "app_revision": definition.edit_revision(),
            "identity_revision": _identity_hash(db, connection, definition=definition),
            "scopes": effective_mcp_consent(db, connection, definition),
            "available_scopes": [scope for scope in MCP_SCOPES
                                 if f"mcp.{scope}" in definition.declaration.get("capabilities", [])],
            "execution_supported": False}


def read_mcp_connection_consent(actor, connection_id):
    with DbSession.atomic() as db:
        connection, definition, row = _context(db, actor, connection_id)
        return _receipt(db, connection, definition, row)


def save_mcp_connection_consent(actor, connection_id, scopes, expected_revision, expected_identity_revision, expected_app_revision):
    if (not isinstance(scopes, list) or len(set(scopes)) != len(scopes)
        or any(scope not in MCP_SCOPES for scope in scopes)
        or (expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1))):
        raise ValueError("Explicit unique MCP scopes and consent revision required")
    with DbSession.atomic() as db:
        connection, definition, row = _context(db, actor, connection_id)
        identity = _identity_hash(db, connection, definition=definition)
        if ((row.revision if row else None) != expected_revision or identity != expected_identity_revision
            or definition.edit_revision() != expected_app_revision):
            raise AppGovernanceConflict()
        if scopes and (connection.state != "connected" or not definition.is_enabled
                       or current_policy(db, connection.organization_id)["effective_mode"] == "disabled"
                       or not {f"mcp.{scope}" for scope in scopes}.issubset(definition.declaration.get("capabilities", []))):
            raise AppGovernanceDenied()
        scope_tools = {scope: _scope_tools(definition.declaration)[scope] for scope in scopes}
        if row is None:
            row = AppMcpConsent(connection_id=connection.id, identity_hash=identity, scope_tools=scope_tools)
            db.insert(row)
        elif row.identity_hash != identity or row.scope_tools != scope_tools:
            row.identity_hash, row.scope_tools = identity, scope_tools
            row.revision += 1
            db.update(row)
        return _receipt(db, connection, definition, row)
