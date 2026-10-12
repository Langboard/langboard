"""Explicit card resource configuration; neither selection nor reading starts work."""

import re
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import AppResourceBinding, CardAppResourceSelection, CardAppResourceSelectionAudit, Project
from ..models.ProjectRole import ProjectRoleAction
from .AppConnectionAuthentication import _identity_hash
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .AppResourceSelection import _selection_context
from .CardAppGovernance import _card, _ownership


def _context(
    db,
    actor,
    project_id,
    card_id,
    app_key,
    connection_id,
    *,
    for_update=True,
    withdrawal=True,
    board_action=ProjectRoleAction.Update,
):
    definition, binding, connection = _selection_context(
        db,
        actor,
        project_id,
        app_key,
        connection_id,
        for_update=for_update,
        withdrawal=withdrawal,
        board_action=board_action,
    )
    project = _current(db, Project, project_id, for_update=False)
    if project is None or project.deleted_at:
        raise AppGovernanceDenied()
    card = _card(db, project, card_id, for_update=for_update)
    owner = _ownership(db, card.id, for_update=for_update)
    return definition, binding, connection, card, owner


def _row(db, card_id, app_key, *, for_update=True):
    statement = SqlBuilder.select.table(CardAppResourceSelection).where(
        CardAppResourceSelection.card_id == card_id, CardAppResourceSelection.app_key == app_key
    )
    return db.exec(statement.with_for_update() if for_update else statement).first()


def _response(row, connection, identity, owner, *, changed=False):
    return {
        "app_key": connection.app_key,
        "connection_uid": connection.get_uid(),
        "revision": row.revision if row else None,
        "resource_uids": list(row.resource_uids) if row else [],
        "identity_revision": identity,
        "ownership_revision": owner.revision if owner else None,
        "snapshot_current": bool(
            row
            and owner
            and owner.app_key == connection.app_key
            and row.identity_hash == identity
            and row.ownership_revision == owner.revision
        ),
        "changed": changed,
    }


def read_card_app_resources(actor, project_id, card_id, app_key, connection_id):
    with DbSession.atomic() as db:
        definition, binding, connection, card, owner = _context(
            db, actor, project_id, card_id, app_key, connection_id, for_update=False
        )
        row = _row(db, card.id, app_key, for_update=False)
        if row and row.connection_id != connection.id:
            raise AppGovernanceDenied()
        return {
            **_response(row, connection, _identity_hash(db, connection, definition=definition), owner),
            "app_revision": definition.edit_revision(),
            "binding_revision": binding.edit_revision(),
        }


def set_card_app_resources(
    actor,
    project_id,
    card_id,
    app_key,
    connection_id,
    resource_uids,
    expected_revision,
    expected_ownership_revision,
    expected_identity_revision,
    expected_app_revision,
    expected_binding_revision,
):
    if (
        not isinstance(resource_uids, list)
        or len(resource_uids) > 20
        or any(not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", uid) for uid in resource_uids)
        or len(set(resource_uids)) != len(resource_uids)
    ):
        raise ValueError("Select at most 20 distinct resource UIDs")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        raise ValueError("Invalid selection revision")
    requested = sorted(resource_uids)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        definition, binding, connection, card, owner = _context(
            db, actor, project_id, card_id, app_key, connection_id, withdrawal=not requested
        )
        row = _row(db, card.id, app_key)
        if (row is None and expected_revision is not None) or (row is not None and row.revision != expected_revision):
            raise AppGovernanceConflict()
        if row and row.connection_id != connection.id:
            _selection_context(db, actor, project_id, app_key, row.connection_id, withdrawal=True)
        identity = _identity_hash(db, connection, definition=definition)
        if requested:
            if owner is None or owner.app_key != app_key:
                raise AppGovernanceDenied()
            if (
                owner.revision != expected_ownership_revision
                or identity != expected_identity_revision
                or definition.edit_revision() != expected_app_revision
                or binding.edit_revision() != expected_binding_revision
            ):
                raise AppGovernanceConflict()
            if "resources.read" not in definition.declaration.get("capabilities", []):
                raise AppGovernanceDenied()
            resources = db.exec(
                SqlBuilder.select.table(AppResourceBinding)
                .where(
                    AppResourceBinding.id.in_([SnowflakeID.from_short_code(uid) for uid in requested]),
                    AppResourceBinding.board_binding_id == binding.id,
                    AppResourceBinding.connection_id == connection.id,
                    AppResourceBinding.is_selected.is_(True),
                    AppResourceBinding.access_state == "granted",
                    AppResourceBinding.identity_hash == identity,
                    AppResourceBinding.resource_type.in_(definition.declaration.get("resource_types", [])),
                )
                .with_for_update()
            ).all()
            if sorted(resource.get_uid() for resource in resources) != requested:
                raise AppGovernanceDenied()
        elif row and row.connection_id != connection.id:
            raise AppGovernanceDenied()
        ownership_revision = owner.revision if owner else 0
        snapshot = identity if requested else ""
        if row is None and not requested:
            return _response(row, connection, identity, owner)
        if (
            row
            and row.connection_id == connection.id
            and row.resource_uids == requested
            and row.identity_hash == snapshot
            and row.ownership_revision == ownership_revision
        ):
            return _response(row, connection, identity, owner)
        if row is None:
            row = CardAppResourceSelection(card_id=card.id, app_key=app_key, connection_id=connection.id)
            db.insert(row)
        else:
            row.revision += 1
        row.connection_id, row.resource_uids = connection.id, requested
        row.identity_hash, row.ownership_revision = snapshot, ownership_revision
        db.update(row)
        db.insert(
            CardAppResourceSelectionAudit(
                card_id=card.id,
                actor_id=actor.id,
                app_key=app_key,
                connection_id=connection.id,
                resource_uids=requested,
                identity_hash=snapshot,
                ownership_revision=ownership_revision,
                revision=row.revision,
            )
        )
        return _response(row, connection, identity, owner, changed=True)


def verified_card_app_configuration(
    db, actor, project_id, card_id, app_key, connection_id, *, capability, for_update=True
):
    definition, binding, connection, card, owner = _context(
        db,
        actor,
        project_id,
        card_id,
        app_key,
        connection_id,
        for_update=for_update,
        withdrawal=False,
        board_action=ProjectRoleAction.Read if capability == "cards.read" else ProjectRoleAction.Update,
    )
    selection = _row(db, card.id, app_key, for_update=for_update)
    identity = _identity_hash(db, connection, definition=definition)
    required = {"resources.read", capability}
    if (
        card.archived_at
        or owner is None
        or owner.app_key != app_key
        or selection is None
        or selection.connection_id != connection.id
        or not selection.resource_uids
        or selection.identity_hash != identity
        or selection.ownership_revision != owner.revision
        or not required.issubset(definition.declaration.get("capabilities", []))
        or not required.issubset(binding.granted_capabilities)
    ):
        raise AppGovernanceDenied()
    resource_statement = SqlBuilder.select.table(AppResourceBinding).where(
        AppResourceBinding.id.in_([SnowflakeID.from_short_code(uid) for uid in selection.resource_uids]),
        AppResourceBinding.board_binding_id == binding.id,
        AppResourceBinding.connection_id == connection.id,
        AppResourceBinding.is_selected.is_(True),
        AppResourceBinding.access_state == "granted",
        AppResourceBinding.identity_hash == identity,
        AppResourceBinding.resource_type.in_(definition.declaration.get("resource_types", [])),
    )
    resources = db.exec(resource_statement.with_for_update() if for_update else resource_statement).all()
    if sorted(resource.get_uid() for resource in resources) != sorted(selection.resource_uids):
        raise AppGovernanceDenied()
    return (
        definition,
        binding,
        card,
        connection,
        {
            "app_revision": definition.edit_revision(),
            "binding_revision": binding.edit_revision(),
            "identity_revision": identity,
            "ownership_revision": owner.revision,
            "selection_revision": selection.revision,
            "card_revision": card.updated_at.isoformat(),
            "resource_revisions": {resource.get_uid(): resource.access_revision for resource in resources},
        },
    )
