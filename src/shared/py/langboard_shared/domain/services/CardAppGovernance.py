"""Human-owned card app assignment, independent of authorship or execution grants."""

import re
from ...core.db import DbSession, SqlBuilder
from ..models import AppDefinition, BoardAppBinding, Card, CardAppOwnership, CardAppOwnershipAudit
from .AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
    _actor,
    _authorized_board,
    _current,
    current_policy,
    lock_policy_mutation,
)


def _card(db, project, card_id, *, for_update=True):
    card = _current(db, Card, card_id, for_update=for_update)
    if card is None or card.deleted_at or card.project_id != project.id:
        raise AppGovernanceDenied()
    return card


def _ownership(db, card_id, *, for_update=True):
    statement = SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card_id)
    return db.exec(statement.with_for_update() if for_update else statement).first()


def _response(row, *, changed=False):
    return {"app_key": row.app_key if row else None, "revision": row.revision if row else None, "changed": changed}


def read_card_app_ownership(actor, project_id, card_id):
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=False)
        project = _authorized_board(db, actor, project_id, for_update=False)
        card = _card(db, project, card_id, for_update=False)
        return _response(_ownership(db, card.id, for_update=False))


def set_card_app_ownership(actor, project_id, card_id, app_key, expected_revision, expected_app_revision=None):
    if app_key is not None and (not isinstance(app_key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", app_key)):
        raise ValueError("Invalid app key")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        raise ValueError("Invalid ownership revision")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor = _actor(db, actor)
        definition = None
        if app_key is not None:
            definition = db.exec(
                SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == app_key).with_for_update()
            ).first()
            if definition is None or not definition.is_enabled:
                raise AppGovernanceDenied()
            if definition.edit_revision() != expected_app_revision:
                raise AppGovernanceConflict()
        project = _authorized_board(db, actor, project_id)
        card = _card(db, project, card_id)
        if app_key is not None:
            binding = db.exec(
                SqlBuilder.select.table(BoardAppBinding)
                .where(BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == app_key)
                .with_for_update()
            ).first()
            if (
                binding is None
                or binding.state != "enabled"
                or "cards.create" not in binding.granted_capabilities
                or "cards.create" not in definition.declaration.get("capabilities", [])
                or not definition.declaration.get("card_lifecycle")
                or current_policy(db, project.organization_id)["effective_mode"] == "disabled"
            ):
                raise AppGovernanceDenied()
        row = _ownership(db, card.id)
        if (row is None and expected_revision is not None) or (row is not None and expected_revision != row.revision):
            raise AppGovernanceConflict()
        previous = row.app_key if row else None
        if previous == app_key:
            return _response(row)
        if row is None:
            row = CardAppOwnership(card_id=card.id, app_key=app_key)
            db.insert(row)
        else:
            row.app_key = app_key
            row.revision += 1
            db.update(row)
        db.insert(
            CardAppOwnershipAudit(
                card_id=card.id, actor_id=actor.id, previous_app_key=previous, app_key=app_key, revision=row.revision
            )
        )
        return _response(row, changed=True)
