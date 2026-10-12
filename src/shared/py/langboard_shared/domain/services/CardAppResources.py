"""Administrator-selected resources are configuration, never execution authority."""

import re
from sqlalchemy import and_, or_, select, update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ...helpers import InfraHelper
from ..models import (
    AppConnection,
    AppDefinition,
    AppResourceBinding,
    BoardAppBinding,
    CardAppOwnership,
    CardAppResourceSelection,
    CardAppResourceSelectionAudit,
    User,
)
from .AppGovernance import AppGovernanceDenied, _current, lock_policy_mutation, require_connection_access
from .AppConnectionAuthentication import _identity_hash
from .AppManifest import APP_MANIFESTS
from .CardAppGovernance import CardAppOwnershipConflict, _admin_card


class CardAppResourcesUnavailable(AppGovernanceDenied):
    """Do not reveal an inaccessible card's resource configuration."""


def configure_card_app_resources(operation, service, actor, project_uid, card_uid, connection_uid, channel, *args):
    """Lock current authority and resolve native visibility for the authenticated transport."""
    with DbSession.atomic() as db:
        project_id = SnowflakeID.from_short_code(project_uid)
        card_id = SnowflakeID.from_short_code(card_uid)
        connection_id = SnowflakeID.from_short_code(connection_uid) if connection_uid is not None else None
        try:
            _admin_card(db, actor, project_id, card_id)
        except AppGovernanceDenied:
            raise CardAppResourcesUnavailable() from None
        if service.card.resolve_readable_card(project_uid, card_uid, actor, channel) is None:
            raise CardAppResourcesUnavailable()
        return operation(actor, project_id, card_id, connection_id, *args)


def list_card_app_resource_selections(actor, project_id, card_id, connection_id=None, after=None):
    """Bounded cleanup receipts under current ownership, including revoked connections."""
    if connection_id is not None:
        raise ValueError("Selection listing has no connection selector")
    after_id = SnowflakeID.from_short_code(after) if after else None
    with DbSession.atomic() as db:
        current, project, card = _admin_card(db, actor, project_id, card_id)
        query = (
            select(CardAppResourceSelection, AppConnection)
            .join(
                AppConnection,
                AppConnection.id == CardAppResourceSelection.connection_id,
            )
            .where(
                CardAppResourceSelection.card_id == card.id,
                CardAppResourceSelection.app_key == AppConnection.app_key,
                or_(
                    and_(AppConnection.ownership == "personal", AppConnection.owner_id == current.id),
                    and_(
                        AppConnection.ownership == "organization",
                        project.organization_id is not None,
                        AppConnection.organization_id == project.organization_id,
                    ),
                ),
            )
        )
        if after_id is not None:
            query = query.where(CardAppResourceSelection.id > after_id)
        rows = db.exec(query.order_by(CardAppResourceSelection.id).limit(26)).all()
        return {
            "items": [
                {
                    "app_key": row.app_key,
                    "connection_uid": connection.get_uid(),
                    "revision": row.revision,
                    "resource_uids": list(row.resource_uids),
                }
                for row, connection in rows[:25]
            ],
            "next_cursor": rows[24][0].get_uid() if len(rows) > 25 else None,
        }


def read_card_app_resources(actor, project_id, card_id, connection_id, *, app_key=None):
    """Read configuration for its current administrator and connection owner, including cleanup receipts."""
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        current, project, card = _admin_card(db, actor, project_id, card_id)
        connection = _current(db, AppConnection, connection_id)
        if (
            connection is None
            or (app_key is not None and connection.app_key != app_key)
            or (connection.ownership == "personal" and connection.owner_id != current.id)
            or (
                connection.ownership == "organization"
                and (connection.organization_id is None or connection.organization_id != project.organization_id)
            )
            or connection.ownership not in ("personal", "organization")
        ):
            raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(CardAppResourceSelection).where(
                CardAppResourceSelection.card_id == card.id,
                CardAppResourceSelection.app_key == connection.app_key,
            )
        ).first()
        if row is not None and row.connection_id != connection.id:
            raise AppGovernanceDenied()
        definition = db.exec(
            SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == connection.app_key)
        ).first()
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.project_id == project.id,
                BoardAppBinding.app_key == connection.app_key,
            )
        ).first()
        owner = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id)).first()
        return {
            "app_key": connection.app_key,
            "connection_uid": connection.get_uid(),
            "revision": row.revision if row else None,
            "resource_uids": list(row.resource_uids) if row else [],
            "identity_revision": _identity_hash(db, connection, definition=definition),
            "ownership_revision": owner.revision if owner else None,
            "app_revision": definition.edit_revision() if definition else None,
            "binding_revision": binding.edit_revision() if binding else None,
        }


def set_card_app_resources(
    actor,
    project_id,
    card_id,
    connection_id,
    resource_uids,
    expected_revision,
    *,
    app_key=None,
    expected_ownership_revision=None,
    expected_identity_revision=None,
    expected_app_revision=None,
    expected_binding_revision=None,
):
    """Atomically replace a bounded explicit selection; empty selection unlinks it."""
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    if (
        not isinstance(resource_uids, list)
        or len(resource_uids) > 20
        or any(not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", uid) for uid in resource_uids)
        or len(set(resource_uids)) != len(resource_uids)
    ):
        raise ValueError("Select at most 20 distinct resource UIDs")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        raise ValueError("Invalid resource selection revision")
    requested = sorted(resource_uids)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        current, project, card = _admin_card(db, actor, project_id, card_id)
        connection = _current(db, AppConnection, connection_id)
        if connection is None or (app_key is not None and connection.app_key != app_key):
            raise AppGovernanceDenied()
        owner = db.exec(SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id)).first()
        if requested and owner and owner.app_key is not None and owner.app_key != connection.app_key:
            raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(CardAppResourceSelection)
            .where(
                CardAppResourceSelection.card_id == card.id,
                CardAppResourceSelection.app_key == connection.app_key,
            )
            .with_for_update()
        ).first()
        if (row is None and expected_revision is not None) or (row is not None and expected_revision != row.revision):
            raise CardAppOwnershipConflict()
        # Clearing must remain possible after resource/consent revocation. Access to
        # someone else's personal connection is still forbidden.
        if connection.ownership == "personal" and connection.owner_id != current.id:
            raise AppGovernanceDenied()
        if connection.ownership == "organization" and (
            connection.organization_id is None or connection.organization_id != project.organization_id
        ):
            raise AppGovernanceDenied()
        if connection.ownership not in ("personal", "organization"):
            raise AppGovernanceDenied()
        if requested:
            require_connection_access(db, current, project, connection, unattended=True)
            definition = db.exec(
                SqlBuilder.select.table(AppDefinition)
                .where(
                    AppDefinition.key == connection.app_key,
                )
                .with_for_update()
            ).first()
            manifest = APP_MANIFESTS.get(connection.app_key)
            if definition is not None:
                if not definition.is_enabled:
                    raise AppGovernanceDenied()
                declared = definition.declaration
            elif manifest:
                declared = {"capabilities": manifest.capabilities, "resource_types": manifest.resource_types}
            else:
                raise AppGovernanceDenied()
            binding = db.exec(
                SqlBuilder.select.table(BoardAppBinding)
                .where(
                    BoardAppBinding.project_id == project.id,
                    BoardAppBinding.app_key == connection.app_key,
                )
                .with_for_update()
            ).first()
            if (
                binding is None
                or binding.state != "enabled"
                or "resources.read" not in binding.granted_capabilities
                or "resources.read" not in declared.get("capabilities", [])
            ):
                raise AppGovernanceDenied()
            if (
                (
                    expected_ownership_revision is not None
                    and (owner is None or owner.revision != expected_ownership_revision)
                )
                or (
                    expected_identity_revision is not None
                    and _identity_hash(db, connection, definition=definition) != expected_identity_revision
                )
                or (
                    expected_app_revision is not None
                    and (definition is None or definition.edit_revision() != expected_app_revision)
                )
                or (expected_binding_revision is not None and binding.edit_revision() != expected_binding_revision)
            ):
                raise CardAppOwnershipConflict()
            resources = db.exec(
                SqlBuilder.select.table(AppResourceBinding)
                .where(
                    AppResourceBinding.id.in_([InfraHelper.convert_id(uid) for uid in requested]),
                    AppResourceBinding.board_binding_id == binding.id,
                    AppResourceBinding.connection_id == connection.id,
                    AppResourceBinding.is_selected == True,  # noqa: E712
                    AppResourceBinding.access_state == "granted",
                    or_(
                        AppResourceBinding.identity_hash == "",
                        AppResourceBinding.identity_hash == _identity_hash(db, connection, definition=definition),
                    ),
                    AppResourceBinding.resource_type.in_(declared.get("resource_types", [])),
                )
                .with_for_update()
            ).all()
            if sorted(resource.get_uid() for resource in resources) != requested:
                raise AppGovernanceDenied()
        if row is None and not requested:
            return {"revision": None, "changed": False, "resource_uids": []}
        if row and row.connection_id == connection.id and row.resource_uids == requested:
            return {"revision": row.revision, "changed": False, "resource_uids": requested}
        if row is None:
            row = CardAppResourceSelection(
                card_id=card.id, app_key=connection.app_key, connection_id=connection.id, resource_uids=requested
            )
            db.insert(row)
        else:
            revision = row.revision + 1
            changed = db.exec(
                update(CardAppResourceSelection)
                .where(
                    CardAppResourceSelection.id == row.id,
                    CardAppResourceSelection.revision == expected_revision,
                )
                .values(
                    connection_id=connection.id,
                    resource_uids=requested,
                    revision=revision,
                    updated_at=SafeDateTime.now(),
                )
            )
            if changed != 1:
                raise CardAppOwnershipConflict()
            row.connection_id, row.resource_uids, row.revision = connection.id, requested, revision
        db.insert(
            CardAppResourceSelectionAudit(
                card_id=card.id,
                actor_id=current.id,
                app_key=connection.app_key,
                connection_id=connection.id,
                resource_uids=requested,
                revision=row.revision,
            )
        )
        return {"revision": row.revision, "changed": True, "resource_uids": requested}
