"""Summarize conservatively proven misses within existing outbox recovery."""

from collections import defaultdict
from datetime import timedelta

from ...core.db import DbSession
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppExecutionOutbox, User
from ..models.AppNotificationSubscription import AppNotificationDispatch
from .AppEventDestination import APP_NOTIFICATION_EVENT
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .AppNotificationDestinations import _expires

_MAX_ITEMS = 50
_MARKER = "notification_missed_summary:"
_CONTENT = {
    "en-US": ("Missed notifications", "{count} notifications expired before delivery."),
    "ko-KR": ("전달되지 않은 알림", "{count}개의 알림이 전달 전에 만료됐습니다."),
    "ja-JP": ("未配信の通知", "{count} 件の通知が配信前に期限切れになりました。"),
    "zh-CN": ("未送达的通知", "{count} 条通知在送达前已过期。"),
}


def _missed_source(db, event, now, *, summary=None):
    """Accept only expired native dispatches with proof of zero attempts."""
    from .AppNotificationDelivery import revalidate_notification_event

    if (event is None or event.event_type != APP_NOTIFICATION_EVENT
            or event.attempt_count != 0 or event.claim_token or event.lease_until
            or event.delivery_history
            or (event.state, event.last_error) != (
                ("pending", None) if summary is None else ("blocked", _MARKER + summary.get_uid()))):
        raise AppGovernanceDenied()
    dispatch = _current(db, AppNotificationDispatch, event.request_id)
    if (dispatch is None or dispatch.receipt or _expires(dispatch.expires_at) > now
            or "source_fence" not in dispatch.snapshot):
        raise AppGovernanceDenied()
    kind = event.payload.get("notification", {}).get("kind")
    audience = event.payload.get("external_destination", {}).get("kind")
    if kind not in ("card_activity", "card_deadline") or (audience == "shared" and kind != "card_deadline"):
        raise AppGovernanceDenied()
    dispatch, subscription = revalidate_notification_event(db, event, receipt_only=True)
    if "missed_summary" not in subscription.filters.get("event_kinds", []):
        raise AppGovernanceDenied()
    return dispatch, subscription


def summarize_missed_notifications(candidate_ids, now):
    """Mark originals and persist their summary atomically, with no original resend."""
    if not candidate_ids:
        return set(), []
    if len(candidate_ids) > 100:
        raise ValueError("Notification recovery page exceeds its limit")
    marked = set()
    summaries = []
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        groups = defaultdict(list)
        for identifier in candidate_ids:
            event = _current(db, AppExecutionOutbox, identifier)
            try:
                dispatch, subscription = _missed_source(db, event, now)
            except (AppGovernanceDenied, AppGovernanceConflict):
                continue
            groups[(subscription.id, event.project_id)].append((event, dispatch, subscription.user_id))
        for items in groups.values():
            for start in range(0, len(items), _MAX_ITEMS):
                group = items[start:start + _MAX_ITEMS]
                first, dispatch, user_id = group[0]
                owner = _current(db, User, user_id)
                language = getattr(owner.preferred_lang, "value", owner.preferred_lang)
                title, body = _CONTENT.get(language, _CONTENT["en-US"])
                times = [_expires(row.created_at) for _, row, _ in group]
                source_uids = [event.get_uid() for event, _, _ in group]
                summary = AppNotificationDispatch(
                    subscription_id=dispatch.subscription_id, request_id="recovery:" + source_uids[0],
                    expires_at=now + timedelta(hours=24),
                )
                db.insert(summary)
                event = AppExecutionOutbox(request_id=summary.id, project_id=first.project_id,
                    app_key=first.app_key, connection_id=first.connection_id, event_type=APP_NOTIFICATION_EVENT)
                db.insert(event)
                event.payload = {
                    **first.payload,
                    "event_uid": event.get_uid(),
                    "expires_at": _expires(summary.expires_at).isoformat(),
                    "notification": {"kind": "missed_summary", "title": title,
                        "body": body.format(count=len(group)), "count": len(group),
                        "window_start": min(times).isoformat(), "window_end": max(times).isoformat()},
                }
                if event.payload["external_destination"]["kind"] == "shared":
                    event.payload["project_uid"] = SnowflakeID(first.project_id).to_short_code()
                summary.event_id = event.id
                summary.snapshot = {"payload": event.payload, "missed_event_uids": source_uids}
                db.update(summary)
                db.update(event)
                for original, _, _ in group:
                    original.state = "blocked"
                    original.last_error = _MARKER + event.get_uid()
                    db.update(original)
                    marked.add(original.id)
                summaries.append(event.id)
    return marked, summaries


def revalidate_missed_summary(db, event, dispatch):
    """Fence every immutable constituent before signing, authorizing or accepting a receipt."""
    members = dispatch.snapshot.get("missed_event_uids")
    count = event.payload.get("notification", {}).get("count")
    if "project_uid" in event.payload and (
        event.payload.get("external_destination", {}).get("kind") != "shared"
        or event.payload["project_uid"] != SnowflakeID(event.project_id).to_short_code()
    ):
        raise AppGovernanceConflict()
    if not isinstance(members, list) or not 1 <= len(members) <= _MAX_ITEMS or count != len(members):
        raise AppGovernanceDenied()
    if any(not isinstance(uid, str) or len(uid) != 11 for uid in members) or len(set(members)) != count:
        raise AppGovernanceDenied()
    now = SafeDateTime.now()
    for uid in members:
        try:
            identifier = SnowflakeID.from_short_code(uid)
        except (ValueError, TypeError) as exc:
            raise AppGovernanceDenied() from exc
        original = _current(db, AppExecutionOutbox, identifier)
        source, _ = _missed_source(db, original, now, summary=event)
        if (source.subscription_id != dispatch.subscription_id or original.project_id != event.project_id
                or original.payload.get("notification_fence") != event.payload.get("notification_fence")):
            raise AppGovernanceConflict()
    return event.project_id
