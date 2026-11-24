"""Shared card reminder commands and queries for transport adapters."""

from datetime import datetime
from hashlib import sha256
from json import dumps
from typing import Annotated
from uuid import UUID
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import Card, CardReminderRule, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardReminderAudience import readable_reminder_recipients
from langboard_shared.domain.services.CardReminderPersistence import (
    card_allows_reminders,
    change_next_occurrence,
    create_rule,
    reminder_utc,
    reschedule_rule,
)
from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError


schedule_adapter = TypeAdapter(ReminderSchedule)


class ReminderRecipientOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(default_factory=list, max_length=50)
    exclude: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(default_factory=list, max_length=50)


def _canonical_overrides(value: ReminderRecipientOverrides | None, shared: bool) -> dict | None:
    if value is None:
        return None
    try:
        included = {_uid(uid).to_short_code() for uid in value.include}
        excluded = {_uid(uid).to_short_code() for uid in value.exclude}
    except ReminderNotFound:
        raise ValueError("Invalid reminder recipient") from None
    if not shared or included & excluded:
        raise ValueError("Invalid reminder recipient override")
    return {"include": sorted(included), "exclude": sorted(excluded)}


def _require_readable_new_recipients(db, card_service, card, overrides, previous=None):
    before = previous or {}
    ids = {
        _uid(uid) for operation in ("include", "exclude")
        for uid in set(overrides[operation]) - set(before.get(operation, []))
    }
    if ids and {user.id for user in readable_reminder_recipients(db, card_service, card, ids)} != ids:
        raise ValueError("Invalid reminder recipient")


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


def _payload(rule: CardReminderRule, *, show_recipients: bool = False) -> dict:
    return {
        "uid": rule.get_uid(),
        "shared": rule.is_shared,
        "enabled": rule.enabled,
        "schedule": rule.schedule,
        "external_delivery": rule.external_delivery,
        **({"recipient_overrides": rule.recipient_overrides} if show_recipients and rule.is_shared else {}),
        "revision": rule.revision,
        "next_due_at": reminder_utc(rule.next_due_at).isoformat() if rule.next_due_at is not None else None,
        "next_regular_due_at": reminder_utc(rule.next_regular_due_at).isoformat() if rule.next_regular_due_at is not None else None,
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
        show_recipients = False
        if any(row.is_shared and row.recipient_overrides for row in rows):
            try:
                _require_card_edit(db, card, user)
                show_recipients = True
            except PermissionError:
                pass
    return {
        "items": [_payload(row, show_recipients=show_recipients) for row in rows[:limit]],
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
    external_delivery: bool = False,
    recipient_overrides: ReminderRecipientOverrides | None = None,
) -> tuple[dict, bool]:
    overrides = _canonical_overrides(recipient_overrides, shared)
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
                        **({"external_delivery": True} if external_delivery else {}),
                        **({"recipient_overrides": overrides} if overrides is not None else {}),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            request_uid = str(request_uid)
            replay = _creation_replay(db, user, request_uid, fingerprint)
            if replay is not None:
                return replay, True
            if overrides is not None:
                _require_readable_new_recipients(db, service.card, card, overrides)
            rule = create_rule(
                card.id,
                user.id,
                schedule,
                SafeDateTime.now(),
                deadline=reminder_utc(card.deadline_at) if card.deadline_at is not None else None,
                is_shared=shared,
                recipient_overrides=overrides,
                is_runnable=card_allows_reminders(db, card),
                request_uid=request_uid,
                external_delivery=external_delivery,
                request_fingerprint=fingerprint,
            )
            receipt = _payload(rule, show_recipients=overrides is not None)
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
    external_delivery: bool | None = None,
    recipient_overrides: ReminderRecipientOverrides | None = None,
) -> dict:
    with DbSession.atomic() as db:
        card = _locked_card(db, project_uid, card_uid, user, channel, service)
        rule = db.exec(_visible_rules(card, user).where(CardReminderRule.id == _uid(rule_uid))).first()
        if rule is None:
            raise ReminderNotFound()
        if rule.is_shared:
            _require_card_edit(db, card, user)
        overrides = _canonical_overrides(recipient_overrides, rule.is_shared)
        if overrides is not None:
            _require_readable_new_recipients(db, service.card, card, overrides, rule.recipient_overrides)
        changed = reschedule_rule(
            rule.id,
            expected_revision,
            schedule,
            SafeDateTime.now(),
            deadline=reminder_utc(card.deadline_at) if card.deadline_at is not None else None,
            is_runnable=card_allows_reminders(db, card),
            enabled=enabled,
            external_delivery=external_delivery,
            recipient_overrides=overrides,
        )
        if not changed:
            raise ReminderConflict()
        saved = db.exec(
            _visible_rules(card, user).where(CardReminderRule.id == rule.id).execution_options(populate_existing=True)
        ).first()
    return _payload(saved, show_recipients=bool(saved.recipient_overrides))



def change_card_reminder_occurrence(
    service: DomainService,
    user: User,
    project_uid: str,
    card_uid: str,
    rule_uid: str,
    channel: CollaborationChannel,
    *,
    expected_revision: int,
    expected_due_at: datetime,
    moved_to: datetime | None = None,
) -> dict:
    with DbSession.atomic() as db:
        card = _locked_card(db, project_uid, card_uid, user, channel, service)
        rule = db.exec(_visible_rules(card, user).where(CardReminderRule.id == _uid(rule_uid))).first()
        if rule is None:
            raise ReminderNotFound()
        if rule.is_shared:
            _require_card_edit(db, card, user)
        occurrence = change_next_occurrence(
            rule.id,
            expected_revision,
            expected_due_at,
            SafeDateTime.now(),
            moved_to=moved_to,
            deadline=reminder_utc(card.deadline_at) if card.deadline_at is not None else None,
            is_runnable=card_allows_reminders(db, card),
        )
        if occurrence is None:
            raise ReminderConflict()
        return {
            "occurrence_uid": occurrence.get_uid(),
            "scheduled_at": reminder_utc(occurrence.due_at).isoformat(),
            "status": "moved" if occurrence.moved_to is not None else "skipped",
            "moved_to": reminder_utc(occurrence.moved_to).isoformat() if occurrence.moved_to is not None else None,
        }
