"""Persistent external app approval. Current primary admin authority on every edit."""

from dataclasses import replace
from langboard_sdk.definition import validate_app_definition
from langboard_sdk.governance import update_consent
from langboard_sdk.workflow import WorkflowRequirements
from ...core.db import DbSession, SqlBuilder
from ...publishers import AppSettingPublisher
from ..models import AppConnection, AppDefinition, BoardAppBinding, User
from .AppGovernance import lock_policy_mutation
from .AppManifest import APP_MANIFESTS, AppManifest, AppSignalPolicy


class AppRegistryConflict(Exception):
    pass


class AppRegistryDenied(Exception):
    pass


def _admin(db, actor):
    current = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id).with_for_update()).first()
    if current is None or not current.is_admin or current.deleted_at or not current.activated_at:
        raise AppRegistryDenied()


def list_definitions(actor, *, after_key=None, limit=None):
    """Preserve legacy reads; bounded callers receive a stable key cursor."""
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 50):
        raise ValueError("Registry page limit must be 1 to 50")
    if after_key is not None and limit is None:
        raise ValueError("A registry cursor requires a page limit")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        _admin(db, actor)
        statement = SqlBuilder.select.table(AppDefinition)
        if after_key is not None:
            statement = statement.where(AppDefinition.key > after_key)
        statement = statement.order_by(AppDefinition.key)
        rows = db.exec(statement.limit(limit + 1) if limit is not None else statement).all()
        if limit is None:
            return [row.registry_response() for row in rows]
        page = rows[:limit]
        return {"items": [row.registry_response() for row in page],
                "next_cursor": page[-1].key if len(rows) > limit else None}


def save_definition(actor, declaration, expected_revision=None, *, package=None):
    declaration = validate_app_definition(declaration)
    key = declaration["key"]
    if key in APP_MANIFESTS:
        raise ValueError("Built-in app declarations are host-owned")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
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
            if tuple(map(int, declaration["version"].split("."))) <= tuple(map(int, row.declaration["version"].split("."))):
                raise ValueError("An app update needs a newer version")
            _update_bindings(db, key, row.declaration, declaration)
            row.declaration = declaration
            if package is not None:
                row.package = package
            row.is_enabled = True
            row.generation += 1
            row.approved_by = actor.id
            db.update(row)
        db.after_commit(AppSettingPublisher.apps_changed)
        return row.registry_response()


def disable_definition(actor, key, expected_revision):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        _admin(db, actor)
        row = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key).with_for_update()).first()
        if row is None:
            return None
        if row.edit_revision() != expected_revision:
            raise AppRegistryConflict()
        _disable_bindings(db, key)
        from .AppMcpConnectionConsent import revoke_mcp_consents
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
    from .AppMcpConnectionConsent import MCP_SCOPES, _scope_tools, revoke_mcp_consents

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
        if "workflow.transition" not in binding.granted_capabilities:
            binding.stage_transitions_enabled = False
        db.update(binding)


def _disable_bindings(db, key):
    for binding in db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == key).with_for_update()).all():
        binding.state = "disabled"
        binding.granted_capabilities = []
        binding.stage_transitions_enabled = False
        if binding.archive_sync_types:
            binding.archive_sync_revision += 1
        binding.archive_sync_types = []
        db.update(binding)


def signal_app_allowed(db, key, *, lock=False):
    """Explicit registry overrides fence native adapters; absence retains built-in compatibility."""
    query = SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key)
    row = db.exec(query.with_for_update() if lock else query).first()
    if row is not None:
        return row.is_enabled and "signals.read" in row.declaration.get("capabilities", [])
    manifest = APP_MANIFESTS.get(key)
    return manifest is not None and "signals.read" in manifest.capabilities


def approved_manifests(*, retained_keys=()):
    """No signal adapter or connection route is inferred from an app declaration."""
    result = dict(APP_MANIFESTS)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(AppDefinition)).all()
        for row in rows:
            if not row.is_enabled and row.key not in retained_keys:
                result.pop(row.key, None)
                continue
            if row.key in APP_MANIFESTS:
                manifest = APP_MANIFESTS[row.key]
                result[row.key] = replace(manifest, capabilities=tuple(
                    capability for capability in manifest.capabilities
                    if row.is_enabled and capability in row.declaration.get("capabilities", [])
                ))
                continue
            item = validate_app_definition(row.declaration)
            workflow = item.get("workflow_requirements")
            requirements = WorkflowRequirements(tuple(workflow["required"]), tuple(workflow["optional"])) if workflow else None
            declared_policy = item.get("signal_policy")
            policy = None if declared_policy is None else AppSignalPolicy(
                event_types=tuple(declared_policy["event_types"]), resource_types=tuple(declared_policy["resource_types"]),
                requires_empty_commit=declared_policy["commit_mode"] == "empty",
                requires_commit=declared_policy["commit_mode"] == "required",
                time_basis=declared_policy["time_basis"] if isinstance(declared_policy["time_basis"], str) else None,
                event_time_bases=tuple(sorted(declared_policy["time_basis"].items()))
                if isinstance(declared_policy["time_basis"], dict) else (),
                evidence_kind="external_observation" if declared_policy["time_basis"] == "observation" else "external_event",
                outcome_states=tuple(declared_policy["outcome_states"].items()),
            )
            result[row.key] = AppManifest(row.key, item["name"], tuple(item["resource_types"]), tuple(item["capabilities"]) if row.is_enabled else (), requirements,
                version=item["version"], description=item["description"], panel=item.get("panel"), signal_policy=policy)
    return result

def prepare_board_consent(service, actor, project_uid, app_key, app_revision):
    """Create a disabled binding without granting capabilities or workflow effects."""
    from ..models.ProjectRole import ProjectRoleAction
    with DbSession.atomic() as db:
        board = service._authorized_app_board(actor, project_uid, ProjectRoleAction.Update, lock=True)
        if board is None:
            raise AppRegistryDenied()
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(
            AppDefinition.key == app_key).with_for_update()).first()
        if definition is None or not definition.is_enabled:
            raise AppRegistryDenied()
        if definition.edit_revision() != app_revision:
            raise AppRegistryConflict()
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.project_id == board.id, BoardAppBinding.app_key == app_key,
        ).with_for_update()).first()
        if binding is None:
            binding = BoardAppBinding(project_id=board.id, app_key=app_key)
            db.insert(binding)
            db.after_commit(AppSettingPublisher.apps_changed)
        return {"uid": binding.get_uid(), "state": binding.state,
                "revision": binding.edit_revision(), "granted_capabilities": binding.granted_capabilities,
                "stage_transitions_enabled": binding.stage_transitions_enabled}


def set_board_consent(service, actor, project_uid, app_key, app_revision, binding_uid, expected_revision, capabilities):
    """Explicitly replace an external app's board grants; never infer them from its declaration."""
    from ...helpers import InfraHelper
    from ..models import Project
    from ..models.ProjectRole import ProjectRoleAction

    if (
        not isinstance(capabilities, list)
        or any(not isinstance(v, str) for v in capabilities)
        or len(capabilities) != len(set(capabilities))
    ):
        raise ValueError("Consent capabilities must be a unique list")
    with DbSession.atomic() as db:
        board = db.exec(
            SqlBuilder.select.table(Project).where(Project.id == InfraHelper.convert_id(project_uid)).with_for_update()
        ).first()
        if (
            board is None
            or service._authorized_app_board(
                actor, board.id, ProjectRoleAction.Update, lock=True, revocation=not capabilities
            )
            is None
        ):
            raise AppRegistryDenied()
        definition = db.exec(
            SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == app_key).with_for_update()
        ).first()
        if definition is None or (capabilities and not definition.is_enabled):
            raise AppRegistryDenied()
        if definition.edit_revision() != app_revision:
            raise AppRegistryConflict()
        if "signals.read" in capabilities and not definition.declaration.get("signal_sources"):
            raise ValueError("Signal consent requires explicit approved sources")
        if not set(capabilities).issubset(definition.declaration.get("capabilities", [])):
            raise ValueError("Consent exceeds the approved app declaration")
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding)
            .where(BoardAppBinding.project_id == board.id, BoardAppBinding.app_key == app_key)
            .with_for_update()
        ).first()
        if binding is None or binding.get_uid() != binding_uid:
            raise AppRegistryDenied()
        if binding.edit_revision() != expected_revision:
            raise AppRegistryConflict()
        binding.granted_capabilities = sorted(capabilities)
        binding.state = "enabled" if capabilities else "disabled"
        # Consent grants capabilities; only the workflow editor can enable transitions.
        if "workflow.transition" not in capabilities:
            binding.stage_transitions_enabled = False
        if "events.receive" not in capabilities:
            if binding.archive_sync_types:
                binding.archive_sync_revision += 1
            binding.archive_sync_types = []
        db.update(binding)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {
            "uid": binding.get_uid(),
            "state": binding.state,
            "revision": binding.edit_revision(),
            "granted_capabilities": binding.granted_capabilities,
            "stage_transitions_enabled": binding.stage_transitions_enabled,
        }
