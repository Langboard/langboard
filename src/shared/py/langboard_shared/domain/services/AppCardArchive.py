"""Freeze native archive evidence without granting an external write authority."""

from json import loads
from types import SimpleNamespace
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import (
    AppCardArchive,
    AppConnection,
    AppDefinition,
    AppExecutionOutbox,
    BoardAppBinding,
    CardAppOwnership,
    CardAppResourceSelection,
    ExternalImportRecord,
)
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current


def configure_archive_sync(service, actor, project_id, app_key, expected_revision, card_types):
    from ..models.ProjectRole import ProjectRoleAction

    if not isinstance(card_types, list) or any(not isinstance(x, str) for x in card_types) or len(set(card_types)) != len(card_types):
        raise ValueError("Archive types must be a unique list")
    with DbSession.atomic() as db:
        if service.workflow_stage._authorized_app_board(actor, project_id, ProjectRoleAction.Update, lock=True, revocation=not card_types) is None:
            raise AppGovernanceDenied()
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.project_id == project_id, BoardAppBinding.app_key == app_key).with_for_update()).first()
        if binding is None:
            raise AppGovernanceDenied()
        if binding.edit_revision() != expected_revision:
            raise AppGovernanceConflict()
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == app_key).with_for_update()).first()
        if card_types and (definition is None or not definition.is_enabled or binding.state != "enabled" or "events.receive" not in binding.granted_capabilities or "events.receive" not in definition.declaration.get("capabilities", [])):
            raise AppGovernanceDenied()
        policies = definition.declaration.get("card_lifecycle", {}) if definition else {}
        for kind in card_types:
            policy = policies.get(kind, {}).get("archive", {})
            if policy.get("mode") != "external" or not policy.get("action") or policy.get("default_enabled") is not False:
                raise ValueError("Archive synchronization requires an explicit external lifecycle policy")
        if binding.archive_sync_types != sorted(card_types):
            binding.archive_sync_revision += 1
        binding.archive_sync_types = sorted(card_types)
        db.update(binding)
        from ...publishers import AppSettingPublisher

        db.after_commit(AppSettingPublisher.apps_changed)
        return {"archive_sync_types": binding.archive_sync_types, "revision": binding.edit_revision()}


def record_app_card_archive(db, actor, project, card):
    """Called inside native movement's transaction, after its new card revision."""
    owner = db.exec(
        SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id).with_for_update()
    ).first()
    if owner is None or not owner.app_key:
        return None
    existing = db.exec(
        SqlBuilder.select.table(AppCardArchive).where(
            AppCardArchive.card_id == card.id, AppCardArchive.card_revision == card.last_change_seq
        )
    ).first()
    if existing:
        return existing
    definition = db.exec(
        SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == owner.app_key).with_for_update()
    ).first()
    selection = db.exec(
        SqlBuilder.select.table(CardAppResourceSelection).where(
            CardAppResourceSelection.card_id == card.id, CardAppResourceSelection.app_key == owner.app_key
        ).with_for_update()
    ).first()
    lineage = db.exec(
        SqlBuilder.select.table(ExternalImportRecord).where(
            ExternalImportRecord.project_id == project.id,
            ExternalImportRecord.target_type == "card",
            ExternalImportRecord.target_uid == card.get_uid(),
            ExternalImportRecord.record_type == "app_card",
        )
    ).all()
    matching = []
    for item in lineage:
        try:
            provenance = loads(item.provenance)
        except (ValueError, TypeError):
            continue
        if isinstance(provenance, dict) and provenance.get("app_key") == owner.app_key:
            matching.append((item, provenance))
    source, provenance = matching[0] if len(matching) == 1 else (None, {})
    card_type = provenance.get("card_type")
    policies = definition.declaration.get("card_lifecycle", {}) if definition else {}
    policy = policies.get(card_type) if isinstance(policies, dict) and isinstance(card_type, str) else None
    archive = policy.get("archive") if isinstance(policy, dict) else None
    valid = isinstance(archive, dict) and archive.get("mode") in ("retain", "external")
    if valid and archive["mode"] == "external":
        valid = isinstance(archive.get("action"), str) and bool(archive["action"]) and archive.get("default_enabled") is False
    reason = "declared_retain" if valid and archive["mode"] == "retain" else "external_sync_disabled"
    if not valid:
        reason = "lifecycle_missing_or_ambiguous"
    binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == owner.app_key)).first()
    row = AppCardArchive(
        card_id=card.id, project_id=project.id, actor_id=actor.id,
        app_key=owner.app_key, card_revision=card.last_change_seq,
        receipt={
            "schema_version": 1,
            "state": "retained" if valid else "blocked",
            "reason": reason,
            "card_type": card_type,
            "ownership_revision": owner.revision,
            "definition_revision": definition.edit_revision() if definition else None,
            "selection_revision": selection.revision if selection else None,
            "connection_uid": provenance.get("connection_uid"),
            "selected_connection_uid": SnowflakeID(selection.connection_id).to_short_code() if selection else None,
            "source_resource_uid": provenance.get("resource_uid"),
            "resource_uids": list(selection.resource_uids) if selection else [],
            "external_resource_id": source.source_record_id if source else None,
            "archive_policy": archive,
            "archived_at": card.archived_at.isoformat(),
            "external_action": "none",
            "external_state": None,
            "outcome_revision": 0,
            "archive_sync_revision": binding.archive_sync_revision if binding else None,
        },
    )
    db.insert(row)
    if valid and archive["mode"] == "external" and binding and card_type in binding.archive_sync_types:
        _queue_archive_event(db, row, selection)
    return row


def _queue_archive_event(db, row, selection):
    from .AppEventDestination import APP_CARD_ARCHIVE_EVENT, current_destination

    try:
        if selection is None:
            raise AppGovernanceDenied()
        connection = _current(db, AppConnection, selection.connection_id)
        if connection is None:
            raise AppGovernanceDenied()
        destination, _ = current_destination(db, connection, APP_CARD_ARCHIVE_EVENT)
        event = AppExecutionOutbox(request_id=row.id, project_id=row.project_id, app_key=row.app_key,
                                   connection_id=selection.connection_id, event_type=APP_CARD_ARCHIVE_EVENT)
        event.payload = {"schema_version": 1, "event_uid": event.get_uid(), "event_type": event.event_type,
                         "archive_uid": row.get_uid(), "card_uid": SnowflakeID(row.card_id).to_short_code(),
                         "board_uid": SnowflakeID(row.project_id).to_short_code(), "app_key": row.app_key,
                         "connection_uid": connection.get_uid(), "card_revision": row.card_revision,
                         "card_type": row.receipt["card_type"], "external_resource_id": row.receipt["external_resource_id"],
                         "action": row.receipt["archive_policy"]["action"],
                         "destination": {"destination_uid": destination.get_uid(), "revision": destination.revision}}
        revalidate_archive_event(db, event)
    except (AppGovernanceDenied, AppGovernanceConflict):
        row.receipt = {**row.receipt, "state": "blocked", "reason": "current_authority_or_destination_changed"}
        db.update(row)
        return
    db.insert(event)
    row.receipt = {**row.receipt, "state": "pending", "reason": "external_delivery_pending",
                   "event_uid": event.get_uid(), "external_action": event.payload["action"]}
    db.update(row)

    def enqueue():
        from ...tasks.webhooks.AppEventDeliveryTask import app_event_delivery_task

        app_event_delivery_task(event.get_uid(), destination.revision)

    db.after_commit(enqueue)


def revalidate_archive_event(db, event, *, reporting=False):
    from ..models import ProjectRole, User
    from .AppCardScope import require_app_card_scope

    row = _current(db, AppCardArchive, event.request_id)
    connection = _current(db, AppConnection, event.connection_id)
    if row is None or connection is None or row.app_key != event.app_key or row.project_id != event.project_id or connection.app_key != row.app_key:
        raise AppGovernanceDenied()
    from .AppEventDestination import current_destination

    destination, _ = current_destination(db, connection, event.event_type)
    if event.payload.get("destination") != {"destination_uid": destination.get_uid(), "revision": destination.revision}:
        raise AppGovernanceConflict()
    if row.receipt.get("provider_report") is not None and not reporting:
        # Unknown provider outcomes require reconciliation, never another mutation
        # merely because the webhook transport response was lost.
        raise AppGovernanceConflict()
    principal = SimpleNamespace(app_key=connection.app_key, owner_id=connection.owner_id, connection_id=connection.id)
    card, owner, selection, binding, _ = require_app_card_scope(db, principal, row.project_id, row.card_id,
                                                             capability="events.receive", role_action="card_update", allow_archived=True)
    actor = _current(db, User, row.actor_id)
    role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.project_id == row.project_id,
                  ProjectRole.user_id == row.actor_id)).first()
    from ..models import Project
    project = _current(db, Project, row.project_id)
    if actor is None or actor.deleted_at or not actor.activated_at or (project.owner_id != actor.id and
            (role is None or not role.is_granted("card_update"))):
        raise AppGovernanceDenied()
    definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == row.app_key)).first()
    kind = row.receipt["card_type"]
    if (not card.archived_at or card.archived_at.isoformat() != row.receipt["archived_at"] or owner.revision != row.receipt["ownership_revision"]
        or selection.revision != row.receipt["selection_revision"] or kind not in binding.archive_sync_types
        or binding.archive_sync_revision != row.receipt["archive_sync_revision"]
        or row.receipt["connection_uid"] != connection.get_uid() or row.receipt["selected_connection_uid"] != connection.get_uid()
        or row.receipt["source_resource_uid"] not in selection.resource_uids
        or set(row.receipt["resource_uids"]) != set(selection.resource_uids)
        or definition.edit_revision() != row.receipt["definition_revision"]
        or definition.declaration.get("card_lifecycle", {}).get(kind, {}).get("archive") != row.receipt["archive_policy"]):
        raise AppGovernanceConflict()
    expected = {"archive_uid": row.get_uid(), "card_uid": card.get_uid(), "board_uid": SnowflakeID(row.project_id).to_short_code(),
                "app_key": row.app_key, "connection_uid": connection.get_uid(), "card_revision": row.card_revision,
                "card_type": kind, "external_resource_id": row.receipt["external_resource_id"],
                "action": row.receipt["archive_policy"]["action"]}
    if any(event.payload.get(key) != value for key, value in expected.items()):
        raise AppGovernanceConflict()


def read_archive_outcome(token, project_id, archive_id):
    """Current owning-app receipt for ambiguous reports; no mutation or delivery retry."""
    from .AppConnectionAuthentication import authenticate_connection_credential
    from .AppEventDestination import APP_CARD_ARCHIVE_EVENT
    from .AppExecutionGrant import AppExecutionCredentialDenied

    with DbSession.atomic() as db:
        try:
            principal = authenticate_connection_credential(token)
        except AppGovernanceDenied as exc:
            raise AppExecutionCredentialDenied() from exc
        row = _current(db, AppCardArchive, archive_id)
        if row is None or row.project_id != project_id or row.app_key != principal.app_key:
            raise AppGovernanceDenied()
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(
            AppExecutionOutbox.request_id == row.id, AppExecutionOutbox.event_type == APP_CARD_ARCHIVE_EVENT
        )).first()
        if event is None or event.connection_id != principal.connection_id:
            raise AppGovernanceDenied()
        revalidate_archive_event(db, event, reporting=True)
        return {"archive_uid": row.get_uid(), "receipt": row.receipt,
                "delivery": {"event_uid": event.get_uid(), "state": event.state, "attempt_count": event.attempt_count}}


def report_archive_outcome(token, project_id, archive_id, expected_revision, outcome):
    """Persist an authenticated app report; transport success is not provider success."""
    from .AppConnectionAuthentication import authenticate_connection_credential
    from .AppEventDestination import APP_CARD_ARCHIVE_EVENT
    from .AppExecutionGrant import AppExecutionCredentialDenied

    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("A nonnegative outcome revision is required")
    if not isinstance(outcome, dict) or set(outcome) != {"state", "external_state", "readback_confirmed"}:
        raise ValueError("Invalid archive outcome")
    state = outcome["state"]
    external = outcome["external_state"]
    if state not in ("applied", "already_applied", "failed", "blocked", "unknown") or type(outcome["readback_confirmed"]) is not bool:
        raise ValueError("Invalid archive outcome")
    if external is not None and (not isinstance(external, str) or not external.strip() or len(external) > 64):
        raise ValueError("Invalid provider state")
    if state in ("applied", "already_applied") and (not outcome["readback_confirmed"] or not external):
        raise ValueError("Applied outcomes require provider state readback")
    with DbSession.atomic() as db:
        try:
            principal = authenticate_connection_credential(token)
        except AppGovernanceDenied as exc:
            raise AppExecutionCredentialDenied() from exc
        row = _current(db, AppCardArchive, archive_id)
        if row is None or row.project_id != project_id or row.app_key != principal.app_key:
            raise AppGovernanceDenied()
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(
            AppExecutionOutbox.request_id == row.id, AppExecutionOutbox.event_type == APP_CARD_ARCHIVE_EVENT
        ).with_for_update()).first()
        if event is None or event.connection_id != principal.connection_id or (event.state not in ("delivering", "delivered") and not (event.state == "blocked" and row.receipt.get("provider_report"))):
            raise AppGovernanceDenied()
        revalidate_archive_event(db, event, reporting=True)
        revision = row.receipt.get("outcome_revision", 0)
        previous = row.receipt.get("provider_report")
        if revision == expected_revision + 1 and previous == outcome:
            return {"archive_uid": row.get_uid(), "receipt": row.receipt, "changed": False}
        if revision != expected_revision or (previous and previous["state"] in ("applied", "already_applied")):
            raise AppGovernanceConflict()
        row.receipt = {**row.receipt, "state": state, "external_state": external,
                       "outcome_revision": revision + 1, "provider_report": outcome,
                       "outcome_source": "authenticated_app_report"}
        db.update(row)
        return {"archive_uid": row.get_uid(), "receipt": row.receipt, "changed": True}
