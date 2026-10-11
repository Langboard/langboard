"""Board-scoped resource selection, not a provider access verification receipt."""

from ...core.db import DbSession, SqlBuilder
from ...publishers import AppSettingPublisher
from ..models import AppConnection, AppResourceBinding, BoardAppBinding, User
from .AppBoardConsent import _context
from .AppConnectionAuthentication import _identity_hash
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, current_policy


def _selection_context(db, actor, project_id, app_key, connection_id, *, withdrawal=False):
    definition, project, binding = _context(db, actor, project_id, app_key, lock_binding=False)
    connection = _current(db, AppConnection, connection_id)
    if binding is None or connection is None or connection.app_key != app_key:
        raise AppGovernanceDenied()
    binding = _current(db, BoardAppBinding, binding.id)
    if connection.ownership == "personal":
        if connection.owner_id != actor.id or connection.organization_id is not None:
            raise AppGovernanceDenied()
    elif connection.ownership == "organization":
        if project.organization_id is None or connection.organization_id != project.organization_id:
            raise AppGovernanceDenied()
    else:
        raise AppGovernanceDenied()
    if not withdrawal:
        owner = _current(db, User, connection.owner_id, for_update=False)
        if owner is None or owner.deleted_at or not owner.activated_at or connection.state != "connected":
            raise AppGovernanceDenied()
        if (
            not definition.is_enabled
            or binding.state != "enabled"
            or "resources.read" not in binding.granted_capabilities
        ):
            raise AppGovernanceDenied()
        if current_policy(db, project.organization_id)["effective_mode"] == "disabled":
            raise AppGovernanceDenied()
    return definition, binding, connection


def _response(row):
    return {
        "resource_uid": row.get_uid(),
        "resource_type": row.resource_type,
        "external_resource_id": row.external_resource_id,
        "resource_path": row.resource_path,
        "is_selected": row.is_selected,
        "revision": row.access_revision,
        "access_state": row.access_state,
        "health": row.health,
    }


def save_resource_selection(
    actor,
    project_id,
    app_key,
    connection_id,
    resource_type,
    external_resource_id,
    selected,
    expected_revision,
    expected_binding_revision,
    expected_app_revision,
    expected_identity_revision,
):
    if type(selected) is not bool or type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Invalid resource selection revision")
    if (
        not isinstance(resource_type, str)
        or not 1 <= len(resource_type) <= 64
        or not isinstance(external_resource_id, str)
        or not 1 <= len(external_resource_id) <= 512
    ):
        raise ValueError("Invalid resource identity")
    with DbSession.atomic() as db:
        definition, binding, connection = _selection_context(
            db, actor, project_id, app_key, connection_id, withdrawal=not selected
        )
        if binding.edit_revision() != expected_binding_revision or definition.edit_revision() != expected_app_revision:
            raise AppGovernanceConflict()
        identity = _identity_hash(db, connection, definition=definition)
        if selected and identity != expected_identity_revision:
            raise AppGovernanceConflict()
        if selected and resource_type not in definition.declaration["resource_types"]:
            raise ValueError("Resource type must be declared by the approved app")
        row = db.exec(
            SqlBuilder.select.table(AppResourceBinding)
            .where(
                AppResourceBinding.board_binding_id == binding.id,
                AppResourceBinding.connection_id == connection.id,
                AppResourceBinding.resource_type == resource_type,
                AppResourceBinding.external_resource_id == external_resource_id,
            )
            .with_for_update()
        ).first()
        if row is None:
            if expected_revision != 0 or not selected:
                raise AppGovernanceConflict()
            row = AppResourceBinding(
                board_binding_id=binding.id,
                connection_id=connection.id,
                resource_type=resource_type,
                external_resource_id=external_resource_id,
            )
            db.insert(row)
        elif row.access_revision != expected_revision:
            raise AppGovernanceConflict()
        row.identity_hash = identity if selected else ""
        row.is_selected = selected
        row.access_revision += 1
        row.access_state = "unknown" if selected else "revoked"
        row.health = "unknown"
        db.update(row)
        binding.resource_selection_revision += 1
        db.update(binding)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {**_response(row), "binding_revision": binding.edit_revision()}


def list_resource_selections(actor, project_id, app_key, connection_id, *, after_id=None, limit=25):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Resource page limit must be 1 to 50")
    with DbSession.atomic() as db:
        definition, binding, connection = _selection_context(
            db, actor, project_id, app_key, connection_id, withdrawal=True
        )
        query = SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.board_binding_id == binding.id, AppResourceBinding.connection_id == connection.id
        )
        if after_id is not None:
            query = query.where(AppResourceBinding.id > after_id)
        rows = db.exec(query.order_by(AppResourceBinding.id).limit(limit + 1)).all()
        page = rows[:limit]
        return {
            "identity_revision": _identity_hash(db, connection, definition=definition),
            "items": [_response(row) for row in page],
            "next_cursor": page[-1].get_uid() if len(rows) > limit else None,
        }
