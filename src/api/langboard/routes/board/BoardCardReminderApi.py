"""Permission-scoped card reminder rule management."""

from hashlib import sha256
from json import dumps
from uuid import UUID
from fastapi import Query, Request
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, ApiPermission, AppRouter, JsonResponse
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
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from .CardAccess import require_visible_card


BASE_PATH = "/board/{project_uid}/card/{card_uid}/reminders"
TAGS = ["Board.Card"]
NO_STORE = {"Cache-Control": "no-store"}
schedule_adapter = TypeAdapter(ReminderSchedule)


class CreateReminderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ReminderSchedule
    request_uid: UUID
    shared: bool = False


class UpdateReminderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ReminderSchedule
    expected_revision: int = Field(ge=1)
    enabled: bool


def _uid(value: str) -> SnowflakeID:
    try:
        result = SnowflakeID.from_short_code(value)
        if result <= 0:
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise ApiException.NotFound_404() from None


def _locked_card(db, project_uid, card_uid, request, user, service):
    # Match card movement ordering before taking the rule revision fence.
    card = db.exec(
        SqlBuilder.select.table(Card)
        .where(Card.id == _uid(card_uid), Card.project_id == _uid(project_uid))
        .with_for_update()
    ).first()
    if card is None or card.is_linked_resource:
        raise ApiException.NotFound_404()
    return require_visible_card(project_uid, card_uid, request, user, service)


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
        raise ApiException.Forbidden_403()


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
        raise ApiException.Conflict_409()
    return original.creation_receipt


@AppRouter.api.get(BASE_PATH, tags=TAGS)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def list_card_reminders(
    request: Request,
    project_uid: str,
    card_uid: str,
    cursor: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    with DbSession.atomic() as db:
        card = require_visible_card(project_uid, card_uid, request, user, service)
        query = _visible_rules(card, user)
        if cursor:
            query = query.where(CardReminderRule.id > _uid(cursor))
        rows = db.exec(query.order_by(CardReminderRule.id).limit(limit + 1)).all()
    return JsonResponse(
        content={
            "items": [_payload(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1].get_uid() if len(rows) > limit else None,
        },
        headers=NO_STORE,
    )


@AppRouter.schema(form=CreateReminderBody, permission=ApiPermission.Edit)
@AppRouter.api.post(BASE_PATH, tags=TAGS)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def create_card_reminder(
    request: Request,
    project_uid: str,
    card_uid: str,
    body: CreateReminderBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        with DbSession.atomic() as db:
            card = _locked_card(db, project_uid, card_uid, request, user, service)
            if body.shared:
                _require_card_edit(db, card, user)
            fingerprint = sha256(
                dumps(
                    {
                        "card": int(card.id),
                        "shared": body.shared,
                        "schedule": schedule_adapter.dump_python(body.schedule, mode="json"),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            request_uid = str(body.request_uid)
            replay = _creation_replay(db, user, request_uid, fingerprint)
            if replay is not None:
                return JsonResponse(content={"item": replay}, headers=NO_STORE)
            rule = create_rule(
                card.id,
                user.id,
                body.schedule,
                SafeDateTime.now(),
                deadline=reminder_utc(card.deadline_at) if card.deadline_at is not None else None,
                is_shared=body.shared,
                is_runnable=card_allows_reminders(db, card),
                request_uid=request_uid,
                request_fingerprint=fingerprint,
            )
            receipt = _payload(rule)
            rule.creation_receipt = receipt
            db.update(rule)
    except IntegrityError:
        with DbSession.atomic() as db:
            card = _locked_card(db, project_uid, card_uid, request, user, service)
            if body.shared:
                _require_card_edit(db, card, user)
            replay = _creation_replay(db, user, request_uid, fingerprint)
            if replay is None:
                raise
            return JsonResponse(content={"item": replay}, headers=NO_STORE)
    except (ValueError, KeyError, OverflowError):
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content={"item": receipt}, headers=NO_STORE, status_code=201)


@AppRouter.schema(form=UpdateReminderBody, permission=ApiPermission.Edit)
@AppRouter.api.put(BASE_PATH + "/{rule_uid}", tags=TAGS)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def update_card_reminder(
    request: Request,
    project_uid: str,
    card_uid: str,
    rule_uid: str,
    body: UpdateReminderBody,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        with DbSession.atomic() as db:
            card = _locked_card(db, project_uid, card_uid, request, user, service)
            rule = db.exec(_visible_rules(card, user).where(CardReminderRule.id == _uid(rule_uid))).first()
            if rule is None:
                raise ApiException.NotFound_404()
            if rule.is_shared:
                _require_card_edit(db, card, user)
            changed = reschedule_rule(
                rule.id,
                body.expected_revision,
                body.schedule,
                SafeDateTime.now(),
                deadline=reminder_utc(card.deadline_at) if card.deadline_at is not None else None,
                is_runnable=card_allows_reminders(db, card),
                enabled=body.enabled,
            )
            if not changed:
                raise ApiException.Conflict_409()
            saved = db.exec(
                _visible_rules(card, user)
                .where(CardReminderRule.id == rule.id)
                .execution_options(populate_existing=True)
            ).first()
    except (ValueError, KeyError, OverflowError):
        raise ApiException.BadRequest_400() from None
    return JsonResponse(content={"item": _payload(saved)}, headers=NO_STORE)
