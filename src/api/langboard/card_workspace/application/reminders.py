"""Shared card reminder commands and queries for transport adapters."""

from hashlib import sha256
from json import dumps
from uuid import UUID
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import Card, CardReminderRule, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardReminderPersistence import (
    card_allows_reminders,
    create_rule,
    reminder_utc,
    reschedule_rule,
)
from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule
from pydantic import TypeAdapter
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError


schedule_adapter = TypeAdapter(ReminderSchedule)


class ReminderNotFound(Exception):
    """The card or reminder is absent in the actor's current scope."""


class ReminderConflict(Exception):
    """The revision or request identity no longer matches."""


def _uid(value: str) -> SnowflakeID:
    try:
        result = SnowflakeID.from_short_code(value)
        if result <= 0:
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise ReminderNotFound() from None


def _readable_card(service, project_uid, card_uid, user, channel):
    resolved = service.card.resolve_readable_card(_uid(project_uid), _uid(card_uid), user, channel)
    if resolved is None or resolved[1].is_linked_resource:
        raise ReminderNotFound()
    return resolved[1]


def _locked_card(db, project_uid, card_uid, user, channel, service):
    # Match card movement ordering before taking the rule revision fence.
    card = db.exec(
        SqlBuilder.select.table(Card)
        .where(Card.id == _uid(card_uid), Card.project_id == _uid(project_uid))
        .with_for_update()
    ).first()
    if card is None or card.is_linked_resource:
        raise ReminderNotFound()
    return _readable_card(service, project_uid, card_uid, user, channel)


def _require_card_edit(db, card: Card, user: User) -> None:
    actor = db.exec(
        SqlBuilder.select.table(User).where(User.id == user.id).execution_options(populate_existing=True)
    ).first()
    if actor is not None and actor.is_admin:
        return
    role = db.exec(
        SqlBuilder.select.table(ProjectRole)
        .where(ProjectRole.project_id == card.project_id, ProjectRole.user_id == user.id)
        .execution_options(populate_existing=True)
    ).first()
    if role is None or ("*" not in role.actions and ProjectRoleAction.CardUpdate.value not in role.actions):
        raise PermissionError()


def _visible_rules(card: Card, user: User):
    return SqlBuilder.select.table(CardReminderRule).where(
        CardReminderRule.card_id == card.id,
        or_(CardReminderRule.is_shared.is_(True), CardReminderRule.owner_id == user.id),
    )


def _payload(rule: CardReminderRule) -> dict:
    return {
        "uid": rule.get_uid(),
        "shared": rule.is_shared,
        "enabled": rule.enabled,
        "schedule": rule.schedule,
        "revision": rule.revision,
        "next_due_at": reminder_utc(rule.next_due_at).isoformat() if rule.next_due_at is not None else None,
    }


def _creation_replay(db: DbSession, user: User, request_uid: str, fingerprint: str) -> dict | None:
    original = db.exec(
        SqlBuilder.select.table(CardReminderRule).where(
            CardReminderRule.owner_id == user.id,
            CardReminderRule.request_uid == request_uid,
        )
    ).first()
    if original is None:
        return None
    if original.request_fingerprint != fingerprint or original.creation_receipt is None:
        raise ReminderConflict()
    return original.creation_receipt


def list_card_reminder_rules(
    service: DomainService,
    user: User,
    project_uid: str,
    card_uid: str,
    channel: CollaborationChannel,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    if not 1 <= limit <= 50:
        raise ValueError("Limit must be between 1 and 50")
    with DbSession.atomic() as db:
        card = _readable_card(service, project_uid, card_uid, user, channel)
        query = _visible_rules(card, user)
        if cursor:
            query = query.where(CardReminderRule.id > _uid(cursor))
        rows = db.exec(query.order_by(CardReminderRule.id).limit(limit + 1)).all()
    return {
        "items": [_payload(row) for row in rows[:limit]],
        "next_cursor": rows[limit - 1].get_uid() if len(rows) > limit else None,
    }


def create_card_reminder_rule(
    service: DomainService,
    user: User,
    project_uid: str,
    card_uid: str,
    channel: CollaborationChannel,
    *,
    schedule: ReminderSchedule,
    shared: bool,
    request_uid: UUID,
) -> tuple[dict, bool]:
    try:
        with DbSession.atomic() as db:
            card = _locked_card(db, project_uid, card_uid, user, channel, service)
            if shared:
                _require_card_edit(db, card, user)
            fingerprint = sha256(
                dumps(
                    {
                        "card": int(card.id),
                        "shared": shared,
                        "schedule": schedule_adapter.dump_python(schedule, mode="json"),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            request_uid = str(request_uid)
            replay = _creation_replay(db, user, request_uid, fingerprint)
            if replay is not None:
                return replay, True
            rule = create_rule(
                card.id,
                user.id,
                schedule,
                SafeDateTime.now(),
                deadline=reminder_utc(card.deadline_at) if card.deadline_at is not None else None,
                is_shared=shared,
                is_runnable=card_allows_reminders(db, card),
                request_uid=request_uid,
                request_fingerprint=fingerprint,
            )
            receipt = _payload(rule)
            rule.creation_receipt = receipt
            db.update(rule)
    except IntegrityError:
        with DbSession.atomic() as db:
            card = _locked_card(db, project_uid, card_uid, user, channel, service)
            if shared:
                _require_card_edit(db, card, user)
            replay = _creation_replay(db, user, request_uid, fingerprint)
            if replay is None:
                raise
            return replay, True
    return receipt, False


def update_card_reminder_rule(
    service: DomainService,
    user: User,
    project_uid: str,
    card_uid: str,
    rule_uid: str,
    channel: CollaborationChannel,
    *,
    schedule: ReminderSchedule,
    expected_revision: int,
    enabled: bool,
) -> dict:
    with DbSession.atomic() as db:
        card = _locked_card(db, project_uid, card_uid, user, channel, service)
        rule = db.exec(_visible_rules(card, user).where(CardReminderRule.id == _uid(rule_uid))).first()
        if rule is None:
            raise ReminderNotFound()
        if rule.is_shared:
            _require_card_edit(db, card, user)
        changed = reschedule_rule(
            rule.id,
            expected_revision,
            schedule,
            SafeDateTime.now(),
            deadline=reminder_utc(card.deadline_at) if card.deadline_at is not None else None,
            is_runnable=card_allows_reminders(db, card),
            enabled=enabled,
        )
        if not changed:
            raise ReminderConflict()
        saved = db.exec(
            _visible_rules(card, user).where(CardReminderRule.id == rule.id).execution_options(populate_existing=True)
        ).first()
    return _payload(saved)
