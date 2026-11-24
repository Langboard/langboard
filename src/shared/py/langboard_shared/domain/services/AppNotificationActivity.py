"""Admit native recipient notifications to explicitly approved personal app inboxes.

Native notification persistence and schedules own event production. This module
adds no clock, provider adapter, identity resolver or alternate queue.
"""
from datetime import timedelta
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from json import dumps, loads
from urllib.parse import urlsplit
from uuid import UUID
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import (
    Card,
    CardComment,
    CardReminderOccurrence,
    CardReminderRule,
    NotificationScheduleRule,
    Project,
    UserCardNotificationPreference,
    UserNotification,
)
from ..models.AppNotificationSubscription import (
    AppNotificationDispatch,
    AppNotificationSharedProjectScope,
    AppNotificationSubscription,
)
from ..models.UserNotification import NotificationType
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .AppNotificationDelivery import MAX_SUBSCRIPTIONS
from .CardReminderAudience import current_reminder_recipients
from .CardReminderPersistence import card_allows_reminders


ACTIVITY_TYPES = frozenset({NotificationType.AssignedToCard, NotificationType.MentionedInCard,
    NotificationType.MentionedInComment, NotificationType.CardCommentAdded, NotificationType.CardMoved})
SOURCE_TYPES = ACTIVITY_TYPES | {NotificationType.ScheduledRule, NotificationType.CardReminder}
KIND_BY_SOURCE = {
    NotificationType.ScheduledRule: "card_deadline",
    NotificationType.CardReminder: "card_reminder",
}


def _hash(value):
    return sha256(dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _instant(value):
    from datetime import datetime
    from .AppNotificationDestinations import _expires
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError("Missing notification source timestamp")
        return _expires(value)
    except (TypeError, ValueError, OverflowError):
        raise AppGovernanceConflict() from None


@lru_cache(maxsize=1)
def _templates():
    return loads(files("langboard_shared.core.resources.locales").joinpath("notification_content.json").read_text())


def _text(value, maximum):
    return "".join(ch for ch in str(value) if ord(ch) >= 32 or ch in "\n\t")[:maximum]


def _source(db, notification, subscription, service, *, receipt_only=False):
    shared = subscription.filters.get("scope", "personal") == "shared"
    if (notification is None or notification.notification_type not in SOURCE_TYPES
            or (not shared and notification.receiver_id != subscription.user_id)
            or (shared and notification.notification_type != NotificationType.ScheduledRule)
            or notification.notifier_type not in {"user", "bot"}):
        raise AppGovernanceDenied()
    deadline = notification.notification_type == NotificationType.ScheduledRule
    reminder = notification.notification_type == NotificationType.CardReminder
    kind = KIND_BY_SOURCE.get(notification.notification_type, "card_activity")
    filters = subscription.filters
    if kind not in filters.get("event_kinds", []):
        raise AppGovernanceDenied()
    activated = filters.get("activated_at")
    if not activated or _instant(notification.created_at) < _instant(activated):
        raise AppGovernanceDenied()
    if not (deadline or reminder) and notification.notifier_type == "user" and notification.notifier_id == subscription.user_id:
        raise AppGovernanceDenied()
    models = {model.__tablename__: model for model in (Project, Card, CardComment)}
    allow_private_deadline = deadline and not shared and any(
        table == Project.__tablename__
        and SnowflakeID(identifier).to_short_code() in filters.get("private_deadline_project_uids", [])
        for table, identifier in notification.record_list
    )
    references = []
    for table, identifier in notification.record_list:
        model = models.get(table)
        if model is None:
            raise AppGovernanceDenied()
        references.append(model.model_construct(id=identifier))
    resolved = service.notification._resolve_notification_recipient(
        subscription.user_id, notification.notification_type, references,
        allow_private_deadline=allow_private_deadline)
    if resolved is None:
        raise AppGovernanceDenied()
    user, references = resolved
    project = next((item for item in references if isinstance(item, Project)), None)
    card = next((item for item in references if isinstance(item, Card)), None)
    comment = next((item for item in references if isinstance(item, CardComment)), None)
    if project is None or card is None:
        raise AppGovernanceDenied()
    selected = filters.get("reminder_project_uids" if reminder else "project_uids", [])
    if selected and project.get_uid() not in selected:
        raise AppGovernanceDenied()
    preference = db.exec(SqlBuilder.select.table(UserCardNotificationPreference).where(
        UserCardNotificationPreference.user_id == user.id, UserCardNotificationPreference.card_id == card.id)).first()
    if notification.notification_type in {NotificationType.CardCommentAdded, NotificationType.CardMoved}:
        mode = preference.mode if preference else "ignore"
        if mode != "comments_and_moves" and not (mode == "comments" and notification.notification_type == NotificationType.CardCommentAdded):
            raise AppGovernanceDenied()
    source = {"notification_uid": notification.get_uid(), "type": notification.notification_type.value,
              "project_uid": project.get_uid(), "card_uid": card.get_uid()}
    fingerprint = {"notification": _hash([notification.notifier_type, str(notification.notifier_id),
        str(notification.receiver_id), notification.notification_type.value, notification.record_list, notification.message_vars])}
    key = "notification:" + notification.get_uid()
    if comment is not None:
        # Mention and ordinary-comment reasons describe one recipient event.
        # A later edit is another revision; an obsolete unsent revision is denied.
        if _instant(comment.updated_at) > _instant(notification.created_at):
            raise AppGovernanceConflict()
        source["comment_uid"] = comment.get_uid()
        fingerprint["comment_revision"] = _instant(comment.updated_at).isoformat()
        key = "comment:" + comment.get_uid() + ":" + _hash(fingerprint["comment_revision"])[:16]
    reasons = [value for value in (preference.reasons if preference else [])
               if value in {"creator", "assignee", "commenter", "mentioned", "direct"}]
    if not reasons:
        reasons = ["assignee" if notification.notification_type == NotificationType.AssignedToCard else
                   "mentioned" if notification.notification_type in {NotificationType.MentionedInCard, NotificationType.MentionedInComment} else "direct"]
    if deadline:
        from ...tasks.notifications.NotificationScheduleTask import _does_rule_match, _resolve_rule_recipients
        values = notification.message_vars
        if shared:
            try:
                trigger = values.get("trigger_uid")
                if not isinstance(trigger, str) or str(UUID(trigger)) != trigger:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise AppGovernanceDenied() from None
            key = "trigger:" + trigger
        try:
            rule = _current(db, NotificationScheduleRule, SnowflakeID.from_short_code(values.get("rule_uid", "")))
        except (ValueError, TypeError):
            raise AppGovernanceDenied() from None
        is_private = card.visibility == "PRIVATE"
        if (rule is None or not rule.is_enabled or rule.target != "card" or rule.field != "deadline_at"
                or (rule.project_uid is not None and rule.project_uid != project.get_uid())
                or (card.visibility != "SHARED" and not (is_private and allow_private_deadline))
                or card.archived_at or card.is_linked_resource
                or not selected or project.get_uid() not in selected or card.deadline_at is None
                or values.get("target_table") != "card" or str(values.get("target_id")) != str(card.id)
                or values.get("field") != rule.field or values.get("operator") != rule.operator
                or values.get("value") != rule.value or values.get("rule_name") != rule.name
                or _instant(values.get("deadline_at")) != _instant(card.deadline_at)):
            raise AppGovernanceConflict()
        rule_data = {key: getattr(rule, key) for key in ("name", "is_enabled", "target", "field", "operator", "value",
                     "recipients", "repeat_after_hours", "interval_str")}
        if rule.project_uid is not None:
            rule_data["project_uid"] = rule.project_uid
        candidate = {"project": project, "card": card, "target_model": card}
        # A late provider observation is retained history, not a new send grant.
        # Keep source, recipient and policy fences; only elapsed time may differ.
        if (not receipt_only and not _does_rule_match(rule_data, card, SafeDateTime.now())) or (not shared and user.id not in {
                item.id for item in _resolve_rule_recipients(rule_data, candidate, service.notification.repo)}):
            raise AppGovernanceDenied()
        source["rule_uid"] = rule.get_uid()
        fingerprint["rule"] = _hash(rule_data)
        fingerprint["deadline_at"] = _instant(card.deadline_at).isoformat()
        if is_private:
            fingerprint["card_visibility"] = card.visibility
        reasons = ["scheduled_rule"]
    if reminder:
        occurrence = _current(db, CardReminderOccurrence, notification.reminder_occurrence_id) if notification.reminder_occurrence_id else None
        rule = _current(db, CardReminderRule, occurrence.rule_id) if occurrence else None
        if (
            notification.message_vars.get("external_delivery") is not True
            or card.visibility == "PRIVATE"
            or rule is None or occurrence.skipped or rule.card_id != card.id
            or notification.message_vars.get("occurrence_uid") != occurrence.get_uid()
            or not selected or project.get_uid() not in selected
        ):
            raise AppGovernanceDenied()
        if not receipt_only and (
            not rule.enabled or not rule.external_delivery or not card_allows_reminders(db, card)
            or notification.read_at is not None or notification.inbox_state == "done"
            or notification.reminder_snoozed_until is not None
            or user.id not in {recipient.id for recipient in current_reminder_recipients(db, service.card, card, rule)}
        ):
            raise AppGovernanceDenied()
        source["rule_uid"] = rule.get_uid()
        source["occurrence_uid"] = occurrence.get_uid()
        fingerprint["reminder_occurrence"] = occurrence.get_uid()
        reasons = ["scheduled_rule"]
    language = getattr(user.preferred_lang, "value", user.preferred_lang)
    template = _templates().get(language, _templates()["en-US"])[notification.notification_type.value]
    values = {"title": _text(card.title, 100), "project": _text(project.title, 200),
              "from_column": _text(notification.message_vars.get("from_column", ""), 200),
              "to_column": _text(notification.message_vars.get("to_column", ""), 200),
              "deadline_at": fingerprint.get("deadline_at", "")}
    url = service.notification.create_redirect_url(project, card)
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or len(url) > 2048:
        raise AppGovernanceDenied()
    content = {"kind": kind, "title": _text(template["title"].format(**values), 120),
               "body": _text(template["body"].format(**values), 2000), "url": url,
               "source": source, "actor": {"kind": notification.notifier_type,
                   "uid": SnowflakeID(notification.notifier_id).to_short_code()}, "reasons": reasons}
    if deadline:
        content["deadline_at"] = fingerprint["deadline_at"]
    return content, {"notification_uid": notification.get_uid(), "project_id": int(project.id), "fingerprint": fingerprint}, key


def admit_native_notification(notification_id):
    """Durable, bounded admission; retries reuse the native source and destination."""
    from ..models import AppExecutionOutbox
    from . import DomainService
    from .AppEventDestination import APP_NOTIFICATION_EVENT, current_destination
    from .AppNotificationDelivery import _current_subscription
    created = 0
    with DbSession.atomic() as db, DomainService.use() as service:
        lock_policy_mutation(db, exclusive=True)
        notification = _current(db, UserNotification, notification_id)
        if notification is None or notification.notification_type not in SOURCE_TYPES:
            return 0
        subscriptions = db.exec(SqlBuilder.select.table(AppNotificationSubscription).where(
            AppNotificationSubscription.user_id == notification.receiver_id,
            AppNotificationSubscription.state == "enabled").order_by(AppNotificationSubscription.id).limit(MAX_SUBSCRIPTIONS + 1)).all()
        if len(subscriptions) > MAX_SUBSCRIPTIONS:
            raise ValueError("Too many notification subscriptions for one recipient")
        subscriptions = [row for row in subscriptions if row.filters.get("scope", "personal") == "personal"]
        if (notification.notification_type == NotificationType.ScheduledRule
                and notification.message_vars.get("target_table") == "card"):
            project_ids = {identifier for table, identifier in notification.record_list if table == Project.__tablename__}
            if len(project_ids) == 1:
                shared_rows = db.exec(SqlBuilder.select.table(AppNotificationSubscription).join(
                    AppNotificationSharedProjectScope,
                    AppNotificationSharedProjectScope.subscription_id == AppNotificationSubscription.id).where(
                    AppNotificationSharedProjectScope.project_id == next(iter(project_ids)),
                    AppNotificationSubscription.state == "enabled").order_by(
                    AppNotificationSubscription.id).limit(MAX_SUBSCRIPTIONS + 1)).all()
                subscriptions.extend(shared_rows)
        # Bound combined fan-out; retain its pending native source
        # on overflow rather than dropping destinations or scanning without limit.
        if len(subscriptions) > MAX_SUBSCRIPTIONS:
            raise ValueError("Too many notification subscriptions for one recipient")
        seen_by_source = {}
        for candidate in subscriptions:
            kind = KIND_BY_SOURCE.get(notification.notification_type, "card_activity")
            if kind not in candidate.filters.get("event_kinds", []):
                continue
            try:
                subscription, destination, connection = _current_subscription(db, candidate.id)
                content, fence, key = _source(db, notification, subscription, service)
                target, _ = current_destination(db, connection, APP_NOTIFICATION_EVENT)
            except (AppGovernanceDenied, AppGovernanceConflict):
                continue
            identity = destination.proof["external_identity"]
            shared = destination.proof["destination"]["kind"] == "shared"
            if shared != (subscription.filters.get("scope", "personal") == "shared"):
                continue
            recipient = (destination.target_fingerprint if shared else
                         _hash([int(subscription.user_id), connection.app_key, identity["issuer"], identity["subject"],
                                destination.proof["destination"]["external_id"]]))
            request_id = "native:" + key
            if request_id not in seen_by_source:
                previous_query = SqlBuilder.select.table(AppNotificationDispatch).join(
                    AppNotificationSubscription, AppNotificationSubscription.id == AppNotificationDispatch.subscription_id).where(
                    AppNotificationDispatch.request_id == request_id)
                if not shared:
                    previous_query = previous_query.where(AppNotificationSubscription.user_id == subscription.user_id)
                previous_rows = db.exec(previous_query.limit(MAX_SUBSCRIPTIONS + 1)).all()
                if len(previous_rows) > MAX_SUBSCRIPTIONS:
                    raise ValueError("Too many retained notification destinations for one source")
                seen_by_source[request_id] = {row.snapshot.get("recipient_fingerprint") for row in previous_rows}
            seen = seen_by_source[request_id]
            if recipient in seen:
                continue
            seen.add(recipient)
            previous = db.exec(SqlBuilder.select.table(AppNotificationDispatch).where(
                AppNotificationDispatch.subscription_id == subscription.id,
                AppNotificationDispatch.request_id == request_id)).first()
            if previous is not None:
                continue
            dispatch = AppNotificationDispatch(subscription_id=subscription.id, request_id=request_id,
                expires_at=SafeDateTime.now() + timedelta(hours=24))
            db.insert(dispatch)
            event = AppExecutionOutbox(request_id=dispatch.id, project_id=fence["project_id"], app_key=connection.app_key,
                connection_id=connection.id, event_type=APP_NOTIFICATION_EVENT)
            db.insert(event)
            event.payload = {"schema_version": 1, "event_uid": event.get_uid(), "event_type": APP_NOTIFICATION_EVENT,
                "app_key": connection.app_key, "connection_uid": connection.get_uid(), "notification": content,
                "notification_fence": {"event_revision": 1, "subscription_uid": subscription.get_uid(),
                    "subscription_generation": subscription.generation, "destination_uid": destination.get_uid(),
                    "destination_revision": destination.revision},
                "external_destination": {"kind": destination.proof["destination"]["kind"], "external_id": destination.proof["destination"]["external_id"]},
                "expires_at": dispatch.expires_at.isoformat(),
                "destination": {"destination_uid": target.get_uid(), "revision": target.revision}}
            dispatch.event_id = event.id
            dispatch.snapshot = {"payload": event.payload, "source_fence": fence, "recipient_fingerprint": recipient}
            db.update(dispatch); db.update(event)
            from .AppEventDelivery import enqueue_event
            db.after_commit(lambda identifier=event.id: enqueue_event(identifier))
            created += 1
    return created


def revalidate_native_source(db, dispatch, subscription, *, receipt_only=False):
    from . import DomainService
    expected = dispatch.snapshot.get("source_fence")
    if not isinstance(expected, dict):
        raise AppGovernanceDenied()
    try:
        identifier = SnowflakeID.from_short_code(expected["notification_uid"])
    except (KeyError, TypeError, ValueError):
        raise AppGovernanceDenied() from None
    with DomainService.use() as service:
        _, actual, _ = _source(db, _current(db, UserNotification, identifier), subscription, service, receipt_only=receipt_only)
    if actual != expected:
        raise AppGovernanceConflict()
    return expected["project_id"]
