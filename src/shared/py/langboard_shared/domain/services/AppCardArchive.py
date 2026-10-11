"""Freeze native archive evidence without granting an external write authority."""

from json import loads
from ...core.db import SqlBuilder
from ...core.types import SnowflakeID
from ..models import AppCardArchive, AppDefinition, CardAppOwnership, CardAppResourceSelection, ExternalImportRecord


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
    policy = policies.get(card_type) if isinstance(card_type, str) else None
    archive = policy.get("archive") if isinstance(policy, dict) else None
    valid = isinstance(archive, dict) and archive.get("mode") in ("retain", "external")
    reason = "declared_retain" if valid and archive["mode"] == "retain" else "external_sync_disabled"
    if not valid:
        reason = "lifecycle_missing_or_ambiguous"
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
        },
    )
    db.insert(row)
    return row
