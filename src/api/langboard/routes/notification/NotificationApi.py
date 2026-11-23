from datetime import datetime
from typing import Literal, cast
from fastapi import Depends, Query, Request
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.SqlBuilder import SqlBuilder
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.schema import OpenApiSchema
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import User, UserNotification
from langboard_shared.domain.models.UserNotification import NotificationInboxState
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.SocketCardAudience import can_receive_card_event
from langboard_shared.infrastructure.repositories.factory.UserNotificationRepository import MAX_INBOX_READ_EVENTS
from langboard_shared.security import Auth
from pydantic import BaseModel, Field
from .NotificationForm import NotificationForm


@AppRouter.api.get(
    "/notifications",
    tags=["Notification"],
    responses=OpenApiSchema().suc({"notifications": [UserNotification]}).auth().forbidden().get(),
)
@AuthFilter.add("user")
def toggle_all_notification_subscription(
    request: Request,
    form: NotificationForm = Depends(), user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    if form.time_range not in ["3d", "7d", "1m", "all"]:
        form.time_range = "3d"
    notifications, has_more, unread_count = service.notification.get_api_list(
        user,
        cast(Literal["3d", "7d", "1m", "all"], form.time_range),
        form.page,
        form.limit,
        unread_only=form.unread_only,
        channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )
    return JsonResponse(content={"notifications": notifications, "has_more": has_more, "unread_count": unread_count})


@AppRouter.api.get("/notifications/{notification_uid}/dispatch-context", tags=["Notification"])
@AuthFilter.add("user")
def notification_dispatch_context(
    notification_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
) -> JsonResponse:
    try:
        notification_id = SnowflakeID.from_short_code(notification_uid)
    except (TypeError, ValueError):
        return JsonResponse(content={"allowed": False})
    context = service.notification.get_dispatch_context(user, notification_id)
    return JsonResponse(content={"allowed": context is not None, "recipient": context})


class SocketCardDispatchForm(BaseModel):
    card_uids: list[str] = Field(min_length=1, max_length=2)
    recipient_uids: list[str] = Field(max_length=100)
    operation: Literal["read", "remove", "edit"] = "read"


@AppRouter.api.post("/socket/card-dispatch-context", tags=["Notification"])
@AuthFilter.add("user")
def socket_card_dispatch_context(
    request: Request, form: SocketCardDispatchForm,
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope(),
) -> JsonResponse:
    """Resolve a bounded native socket audience through the current shared policy."""
    try:
        claims = AuthSecurity.decode_access_token(request.headers.get(AuthSecurity.API_TOKEN_HEADER, ""))
        if claims.get("internal") != "socket_dispatch" or int(claims["sub"]) != int(user.id):
            return JsonResponse(content={"allowed_recipient_uids": []}, status_code=403)
    except Exception:
        return JsonResponse(content={"allowed_recipient_uids": []}, status_code=403)
    try:
        allowed = []
        for uid in dict.fromkeys(form.recipient_uids):
            recipient_id = SnowflakeID.from_short_code(uid)
            with DbSession.use(readonly=False) as db:
                recipient = db.exec(SqlBuilder.select.table(User).where(User.id == recipient_id)).first()
            if recipient is not None and all(
                can_receive_card_event(service, recipient, card_uid, form.operation) for card_uid in form.card_uids
            ):
                allowed.append(uid)
        return JsonResponse(content={"allowed_recipient_uids": allowed})
    except (TypeError, ValueError):
        return JsonResponse(content={"allowed_recipient_uids": []})


def _parse_notification_uid(notification_uid: str) -> SnowflakeID:
    try:
        notification_id = SnowflakeID.from_short_code(notification_uid)
    except ValueError as error:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from error

    if notification_id <= 0:
        raise ApiException.BadRequest_400(ApiErrorCode.VA0000)
    return notification_id


@AppRouter.api.get("/notifications/inbox", tags=["Notification"])
@AuthFilter.add("user")
def list_notification_inbox(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=80),
    unread_only: bool = False,
    state: NotificationInboxState = "inbox",
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    before = None
    if cursor is not None:
        try:
            raw_timestamp, uid = cursor.split("~", 1)
            timestamp = datetime.fromisoformat(raw_timestamp)
            if timestamp.utcoffset() is None:
                raise ValueError("Timezone required")
            before = (SafeDateTime.fromisoformat(raw_timestamp), _parse_notification_uid(uid))
        except (ValueError, OverflowError) as error:
            raise ApiException.BadRequest_400(ApiErrorCode.VA0000) from error

    rows, unread_count = service.notification.get_inbox_threads(
        user, limit, before, unread_only,
        channel=request.scope.get("collaboration_channel", CollaborationChannel.Api), state=state,
    )
    page = rows[:limit]
    tail = page[-1] if len(rows) > limit else None
    return JsonResponse(content={
        "threads": [
            {
                "latest_event_uid": SnowflakeID(row.event_id).to_short_code(),
                "latest_event_type": row.latest_type.value,
                "latest_at": row.created_at.isoformat(),
                "project_uid": SnowflakeID(row.project_id).to_short_code() if row.project_id is not None else None,
                "card_uid": SnowflakeID(row.card_id).to_short_code() if row.card_id is not None else None,
                "card_title": row.card_title,
                "unread_events": row.unread_events,
                "unread_mention": row.unread_mention,
                "observed_unread_event_uids": [
                    SnowflakeID(event_id).to_short_code() for event_id in row.observed_unread_event_ids
                ],
                "has_more_unread_events": row.unread_events > len(row.observed_unread_event_ids),
                "observed_state_event_uids": [
                    SnowflakeID(event_id).to_short_code() for event_id in row.observed_state_event_ids
                ],
                "has_more_state_events": row.state_event_count > len(row.observed_state_event_ids),
            }
            for row in page
        ],
        "unread_count": unread_count,
        "next_cursor": f"{tail.created_at.isoformat()}~{SnowflakeID(tail.event_id).to_short_code()}" if tail else None,
    })


class InboxReadEventsForm(BaseModel):
    event_uids: list[str] = Field(min_length=1, max_length=MAX_INBOX_READ_EVENTS)


class InboxStateForm(InboxReadEventsForm):
    expected_state: NotificationInboxState
    state: NotificationInboxState


@AppRouter.api.put("/notifications/inbox/state", tags=["Notification"])
@AuthFilter.add("user")
def set_inbox_event_state(
    request: Request,
    form: InboxStateForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    event_ids = {_parse_notification_uid(uid) for uid in form.event_uids}
    updated = service.notification.set_inbox_event_state(
        user, event_ids, form.expected_state, form.state,
        channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )
    return JsonResponse(content={"updated_count": updated})


@AppRouter.api.put("/notifications/inbox/read-events", tags=["Notification"])
@AuthFilter.add("user")
def read_inbox_events(
    request: Request,
    form: InboxReadEventsForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    event_ids = {_parse_notification_uid(uid) for uid in form.event_uids}
    updated = service.notification.read_inbox_events(
        user, event_ids, channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
    )
    return JsonResponse(content={"updated_count": updated})


@AppRouter.api.put("/notifications/read-all", tags=["Notification"], responses=OpenApiSchema().auth().forbidden().get())
@AuthFilter.add("user")
def read_all_notifications(
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    mutation = service.notification.read_all(user)
    return JsonResponse(content=mutation)


@AppRouter.api.put(
    "/notifications/{notification_uid}/read",
    tags=["Notification"],
    responses=OpenApiSchema().auth().forbidden().err(400, ApiErrorCode.VA0000).get(),
)
@AuthFilter.add("user")
def read_notification(
    notification_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    mutation = service.notification.read(user, _parse_notification_uid(notification_uid))
    return JsonResponse(content=mutation)


@AppRouter.api.delete("/notifications", tags=["Notification"], responses=OpenApiSchema().auth().forbidden().get())
@AuthFilter.add("user")
def delete_all_notifications(
    user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    mutation = service.notification.delete_all(user)
    return JsonResponse(content=mutation)


@AppRouter.api.delete(
    "/notifications/{notification_uid}",
    tags=["Notification"],
    responses=OpenApiSchema().auth().forbidden().err(400, ApiErrorCode.VA0000).get(),
)
@AuthFilter.add("user")
def delete_notification(
    notification_uid: str, user: User = Auth.scope("user"), service: DomainService = DomainService.scope()
) -> JsonResponse:
    mutation = service.notification.delete(user, _parse_notification_uid(notification_uid))
    return JsonResponse(content=mutation)
