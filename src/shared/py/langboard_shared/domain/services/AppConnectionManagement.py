"""Explicit inbound service identities and board resource consent for approved apps."""

from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...publishers import AppSettingPublisher
from ..models import (
    AppConnection,
    AppConnectionCredential,
    AppDefinition,
    AppMcpConsent,
    AppResourceBinding,
    BoardAppBinding,
)
from ..models.ProjectRole import ProjectRoleAction
from .AppConnectionAuthentication import _identity_hash, _manage, is_native_inbound_connection
from .AppGovernance import (
    AppGovernanceDenied,
    _actor,
    _authorize,
    _current,
    current_policy,
    lock_policy_mutation,
    require_connection_access,
)
from .AppManifest import APP_MANIFESTS
from .AppRegistry import AppRegistryConflict


def _definition(db, key, revision, *, revocation=False):
    # Built-in provider identities must continue through their verified adapters.
    if key in APP_MANIFESTS:
        raise AppGovernanceDenied()
    row = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key).with_for_update()).first()
    if row is None or (not revocation and not row.is_enabled):
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
        lock_policy_mutation(db)
        actor = _actor(db, actor)
        if organization_id is not None:
            _authorize(db, actor, organization_id)
        if current_policy(db, organization_id)["effective_mode"] == "disabled":
            raise AppGovernanceDenied()
        _definition(db, key, app_revision)
        connection = AppConnection(
            app_key=key,
            owner_id=actor.id,
            organization_id=organization_id,
            ownership="organization" if organization_id is not None else "personal",
            state="connected",
        )
        db.insert(connection)
        # Receipts must use the same persisted timestamp representation as later
        # listing, credential issuance and concurrency checks.
        connection = _current(db, AppConnection, connection.id)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {
            "connection_uid": connection.get_uid(),
            "ownership": connection.ownership,
            "state": connection.state,
            "revision": _identity_hash(db, connection),
        }


def disconnect_inbound_connection(actor, connection_id, expected_revision):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        connection = _current(db, AppConnection, connection_id)
        if connection is None or not is_native_inbound_connection(connection):
            raise AppGovernanceDenied()
        _manage(db, actor, connection)
        if _identity_hash(db, connection) != expected_revision:
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
        consent = db.exec(
            SqlBuilder.select.table(AppMcpConsent)
            .where(
                AppMcpConsent.connection_id == connection.id,
            )
            .with_for_update()
        ).first()
        if consent is not None and consent.scope_tools:
            consent.scope_tools = {}
            consent.revision += 1
            db.update(consent)
        # Refresh the ORM identity map after db.update's in-memory UTC timestamp.
        connection = db.exec(
            SqlBuilder.select.table(AppConnection)
            .where(AppConnection.id == connection.id)
            .execution_options(populate_existing=True)
        ).first()
        db.after_commit(AppSettingPublisher.apps_changed)
        return {
            "connection_uid": connection.get_uid(),
            "state": connection.state,
            "revision": _identity_hash(db, connection),
        }


def list_inbound_connections(actor, key, *, organization_id=None, after_id=None, limit=25):
    """Recover management receipts in one explicit ownership scope, even after disable."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Connection page limit must be 1 to 50")
    if key in APP_MANIFESTS:
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor = _actor(db, actor)
        statement = SqlBuilder.select.table(AppConnection).where(AppConnection.app_key == key)
        if organization_id is None:
            statement = statement.where(AppConnection.ownership == "personal", AppConnection.owner_id == actor.id)
        else:
            _authorize(db, actor, organization_id)
            statement = statement.where(
                AppConnection.ownership == "organization", AppConnection.organization_id == organization_id
            )
        statement = statement.where(
            AppConnection.credential_reference.is_(None),
            AppConnection.instance_url == "",
            AppConnection.external_account_id == "",
        )
        if after_id is not None:
            statement = statement.where(AppConnection.id > after_id)
        rows = db.exec(statement.order_by(AppConnection.id).limit(limit + 1)).all()
        page = rows[:limit]
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key)).first()
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


def list_inbound_resources(service, actor, project_uid, key, connection_id, *, after_id=None, limit=25):
    """Read management receipts without granting upstream access or hiding revocation state."""
    if type(limit) is not int or not 1 <= limit <= 50 or key in APP_MANIFESTS:
        raise ValueError("Invalid resource page")
    with DbSession.atomic() as db:
        board = service._authorized_app_board(actor, project_uid, ProjectRoleAction.Update, revocation=True)
        if board is None:
            raise AppGovernanceDenied()
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == key)).first()
        connection = _current(db, AppConnection, connection_id)
        if definition is None or connection is None or connection.app_key != key:
            raise AppGovernanceDenied()
        _manage(db, actor, connection)
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.project_id == board.id,
                BoardAppBinding.app_key == key,
            )
        ).first()
        if binding is None:
            raise AppGovernanceDenied()
        statement = SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.board_binding_id == binding.id,
            AppResourceBinding.connection_id == connection.id,
        )
        if after_id is not None:
            statement = statement.where(AppResourceBinding.id > after_id)
        rows = db.exec(statement.order_by(AppResourceBinding.id).limit(limit + 1)).all()
        page = rows[:limit]
        return {
            "app_revision": definition.edit_revision(),
            "binding_uid": binding.get_uid(),
            "binding_revision": binding.edit_revision(),
            "resource_types": definition.declaration.get("resource_types", []),
            "items": [
                {
                    "resource_uid": row.get_uid(),
                    "resource_type": row.resource_type,
                    "external_resource_id": row.external_resource_id,
                    "selected": row.is_selected,
                    "access_state": row.access_state,
                    "access_revision": row.access_revision,
                }
                for row in page
            ],
            "next_cursor": page[-1].get_uid() if len(rows) > limit else None,
        }


def select_inbound_resource(
    service,
    actor,
    project_uid,
    key,
    app_revision,
    binding_uid,
    expected_revision,
    connection_id,
    resource_type,
    external_id,
    selected=True,
    expected_access_revision=None,
):
    """The board manager and connection manager explicitly approve one identity.

    Serialize on the board/binding. Replays retain a resource UID and do not
    increment its access generation. Removing selection revokes cached authority.
    """
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        board = service._authorized_app_board(
            actor, project_uid, ProjectRoleAction.Update, lock=True, revocation=not selected
        )
        if board is None:
            raise AppGovernanceDenied()
        definition = _definition(db, key, app_revision, revocation=not selected)
        if selected and resource_type not in definition.declaration.get("resource_types", []):
            raise ValueError("Resource type is not declared")
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding)
            .where(
                BoardAppBinding.project_id == board.id,
                BoardAppBinding.app_key == key,
            )
            .with_for_update()
        ).first()
        if binding is None or binding.get_uid() != binding_uid:
            raise AppGovernanceDenied()
        if binding.edit_revision() != expected_revision:
            raise AppRegistryConflict()
        connection = _current(db, AppConnection, connection_id)
        if connection is None or connection.app_key != key:
            raise AppGovernanceDenied()
        _manage(db, actor, connection)
        if selected:
            require_connection_access(db, actor, board, connection)
        resource = db.exec(
            SqlBuilder.select.table(AppResourceBinding)
            .where(
                AppResourceBinding.board_binding_id == binding.id,
                AppResourceBinding.connection_id == connection.id,
                AppResourceBinding.resource_type == resource_type,
                AppResourceBinding.external_resource_id == external_id,
            )
            .with_for_update()
        ).first()
        identity = _identity_hash(db, connection, definition=definition) if selected else ""
        if resource is None:
            if expected_access_revision is not None:
                raise AppRegistryConflict()
            if not selected:
                raise ValueError("Resource is not selected")
            resource = AppResourceBinding(
                board_binding_id=binding.id,
                connection_id=connection.id,
                resource_type=resource_type,
                external_resource_id=external_id,
                access_state="granted",
                is_selected=True,
                access_revision=1,
                identity_hash=identity,
            )
            db.insert(resource)
        else:
            if resource.access_revision != expected_access_revision:
                raise AppRegistryConflict()
            if (
                resource.is_selected != selected
                or resource.access_state != ("granted" if selected else "revoked")
                or resource.identity_hash != identity
            ):
                resource.is_selected = selected
                resource.access_state = "granted" if selected else "revoked"
                resource.identity_hash = identity
                resource.access_revision += 1
                db.update(resource)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {
            "resource_uid": resource.get_uid(),
            "selected": resource.is_selected,
            "access_state": resource.access_state,
            "access_revision": resource.access_revision,
            "binding_revision": binding.edit_revision(),
        }
