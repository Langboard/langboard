"""Persistent external app approval. Current primary admin authority on every edit."""

from langboard_sdk.definition import validate_app_definition
from langboard_sdk.governance import update_consent
from ...core.db import DbSession, SqlBuilder
from ...publishers import AppSettingPublisher
from ..models import AppConnection, AppDefinition, BoardAppBinding, User
from .AppMcpConnectionConsent import MCP_SCOPES, _scope_tools, revoke_mcp_consents


class AppRegistryConflict(Exception):
    pass


class AppRegistryDenied(Exception):
    pass


def _admin(db, actor, *, for_update=True):
    statement = SqlBuilder.select.table(User).where(User.id == actor.id)
    current = db.exec(statement.with_for_update() if for_update else statement).first()
    if current is None or not current.is_admin or current.deleted_at or not current.activated_at:
        raise AppRegistryDenied()


def list_definitions(actor, *, after_key=None, limit=25):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Registry page limit must be 1 to 50")
    with DbSession.atomic() as db:
        _admin(db, actor, for_update=False)
        statement = SqlBuilder.select.table(AppDefinition)
        if after_key is not None:
            statement = statement.where(AppDefinition.key > after_key)
        rows = db.exec(statement.order_by(AppDefinition.key).limit(limit + 1)).all()
        page = rows[:limit]
        return {
            "items": [row.registry_response() for row in page],
            "next_cursor": page[-1].key if len(rows) > limit else None,
        }


def save_definition(actor, declaration, expected_revision=None, *, package=None):
    declaration = validate_app_definition(declaration)
    key = declaration["key"]
    with DbSession.atomic() as db:
        _admin(db, actor)
        # Old approved definitions remain readable. Any new approval/update
        # producing cards must explicitly define their lifecycle, even in v1.
        if "cards.create" in declaration["capabilities"] and not declaration.get("card_lifecycle"):
            raise ValueError("Card-producing app approval requires lifecycle policies")
        row = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key).with_for_update()).first()
        if row is None:
            if expected_revision is not None:
                raise AppRegistryConflict()
            row = AppDefinition(key=key, declaration=declaration, approved_by=actor.id, package=package)
            db.insert(row)
        else:
            if expected_revision is None or row.edit_revision() != expected_revision:
                raise AppRegistryConflict()
            if tuple(map(int, declaration["version"].split("."))) <= tuple(
                map(int, row.declaration["version"].split("."))
            ):
                raise ValueError("An app update needs a newer version")
            _update_bindings(db, key, row.declaration, declaration)
            row.declaration = declaration
            row.package = package
            row.is_enabled = True
            row.generation += 1
            row.approved_by = actor.id
            db.update(row)
        db.after_commit(AppSettingPublisher.apps_changed)
        return row.registry_response()


def disable_definition(actor, key, expected_revision):
    with DbSession.atomic() as db:
        _admin(db, actor)
        row = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key).with_for_update()).first()
        if row is None:
            return None
        if row.edit_revision() != expected_revision:
            raise AppRegistryConflict()
        _disable_bindings(db, key)
        if row.declaration.get("mcp"):
            revoke_mcp_consents(db, key)
        row.is_enabled = False
        row.generation += 1
        row.approved_by = actor.id
        db.update(row)
        db.after_commit(AppSettingPublisher.apps_changed)
        return row.registry_response()


def _update_bindings(db, key, previous, current):
    """Keep existing consent only across updates to the same trust targets."""
    trust_changed = update_consent(previous, current, [])["trust_changed"]
    # Withdraw affected connection scopes durably: returning to an older tool
    # set must not resurrect consent without another explicit owner approval.
    old_tools, new_tools = _scope_tools(previous), _scope_tools(current)
    changed_scopes = {
        scope
        for scope in old_tools
        if old_tools[scope] != new_tools[scope]
        or (
            f"mcp.{scope}" in previous.get("capabilities", []) and f"mcp.{scope}" not in current.get("capabilities", [])
        )
    }
    # Lock ordering matches owner consent: definition -> connection -> consent.
    if trust_changed:
        for connection in db.exec(
            SqlBuilder.select.table(AppConnection)
            .where(AppConnection.app_key == key)
            .order_by(AppConnection.id)
            .with_for_update()
        ).all():
            connection.state = "revoked"
            db.update(connection)
    if (trust_changed or changed_scopes) and (previous.get("mcp") or current.get("mcp")):
        revoke_mcp_consents(db, key, MCP_SCOPES if trust_changed else changed_scopes)
    for binding in db.exec(
        SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == key).with_for_update()
    ).all():
        decision = update_consent(previous, current, binding.granted_capabilities)
        binding.granted_capabilities = sorted(decision["retained"])
        if (
            trust_changed
            or previous.get("card_lifecycle") != current.get("card_lifecycle")
            or "events.receive" not in binding.granted_capabilities
        ):
            if binding.archive_sync_types:
                binding.archive_sync_revision += 1
            binding.archive_sync_types = []
        if decision["trust_changed"] or not binding.granted_capabilities:
            binding.state = "needs_attention" if decision["trust_changed"] else "disabled"
            binding.stage_transitions_enabled = False
        db.update(binding)


def _disable_bindings(db, key):
    for binding in db.exec(
        SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == key).with_for_update()
    ).all():
        binding.state = "disabled"
        binding.granted_capabilities = []
        binding.stage_transitions_enabled = False
        if binding.archive_sync_types:
            binding.archive_sync_revision += 1
        binding.archive_sync_types = []
        db.update(binding)
