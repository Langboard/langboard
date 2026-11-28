"""Explicit personal test subscriptions; generic app delivery fences and receipts."""

from typing import Annotated, Literal
from fastapi import Header, Path, Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppNotificationDelivery import (
    read_shared_notification_scope,
    create_personal_subscription, configure_personal_subscription, revoke_personal_subscription, create_test_notification,
    authorize_notification_delivery, record_notification_receipt,
)
from langboard_shared.domain.services.AppNotificationOverview import (
    list_personal_notification_outbox, read_personal_notification_outbox, read_connection_notification_settings,
    read_notification_onboarding,
)
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field
from .AppOperation import run_app_operation as _run

UID = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")]
CURSOR = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9]{11}$")]


@AppRouter.api.get("/settings/apps/connections/{connection_uid}/notification-onboarding", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def notification_onboarding(connection_uid: UID, user: User = Auth.scope("user")):
    return _run(read_notification_onboarding, user, SnowflakeID.from_short_code(connection_uid))


@AppRouter.api.get("/settings/apps/connections/{connection_uid}/notification-settings", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def notification_settings(connection_uid: UID, section: Literal["destinations", "challenges", "subscriptions"] = "destinations",
                          after: CURSOR = None, limit: Annotated[int, Query(ge=1, le=50)] = 25,
                          request_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._:-]{1,128}$")] = None,
                          user: User = Auth.scope("user")):
    return _run(read_connection_notification_settings, user, SnowflakeID.from_short_code(connection_uid),
                section=section, after=SnowflakeID.from_short_code(after) if after else None, limit=limit, request_id=request_id)


@AppRouter.api.get("/settings/apps/notification-outbox", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def notification_outbox(
    after: CURSOR = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
    subscription_uid: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9]{11}$")] = None,
    request_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._:-]{1,128}$")] = None,
    user: User = Auth.scope("user"),
):
    if (subscription_uid is None) != (request_id is None) or (subscription_uid is not None and after is not None):
        raise ApiException.BadRequest_400()
    return _run(
        list_personal_notification_outbox,
        user,
        after=SnowflakeID.from_short_code(after) if after else None,
        limit=limit,
        subscription_id=SnowflakeID.from_short_code(subscription_uid) if subscription_uid is not None else None,
        request_id=request_id,
    )


@AppRouter.api.get("/settings/apps/notification-outbox/{dispatch_uid}", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def notification_outbox_detail(dispatch_uid: UID, user: User = Auth.scope("user")):
    return _run(read_personal_notification_outbox, user, SnowflakeID.from_short_code(dispatch_uid))


@form_model
class SubscriptionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    destination_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    expected_destination_revision: int = Field(strict=True, ge=1)
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    scope: Literal["personal", "shared"]
    project_uids: list[str] = Field(default_factory=list, max_length=50)


@form_model
class SubscriptionGenerationForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_generation: int = Field(strict=True, ge=1)


@form_model
class SubscriptionScopeForm(SubscriptionGenerationForm):
    event_kinds: list[Literal["test", "card_activity", "card_deadline", "card_reminder", "missed_summary"]] = Field(min_length=1, max_length=5)
    project_uids: list[str] = Field(default_factory=list, max_length=50)
    private_deadline_project_uids: list[str] | None = Field(default=None, max_length=50)
    reminder_project_uids: list[str] | None = Field(default=None, max_length=50)


@form_model
class TestNotificationForm(SubscriptionGenerationForm):
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=2000)


class DeliveryFenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_revision: int = Field(strict=True, ge=1)
    subscription_generation: int = Field(strict=True, ge=1)
    destination_revision: int = Field(strict=True, ge=1)


class DeliveryReceiptBody(DeliveryFenceBody):
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    status: Literal["adapter_accepted", "confirmed", "unknown", "rejected"]
    expected_revision: int | None = Field(default=None, strict=True, ge=1)
    reconciled: bool = Field(default=False, strict=True)
    provider_message_id: str | None = Field(default=None, max_length=200)
    reason_code: str | None = Field(default=None, max_length=64)


def _token(value):
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiException.Unauthorized_401()
    return token


@AppRouter.api.post("/settings/apps/notification-subscriptions", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def subscribe(form: SubscriptionForm, user: User = Auth.scope("user")):
    return _run(create_personal_subscription, user, SnowflakeID.from_short_code(form.destination_uid),
        expected_destination_revision=form.expected_destination_revision, request_id=form.request_id,
        scope=form.scope, project_uids=form.project_uids)


@AppRouter.api.post("/settings/apps/notification-subscriptions/{subscription_uid}/scope", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def configure_scope(subscription_uid: UID, form: SubscriptionScopeForm, user: User = Auth.scope("user")):
    return _run(configure_personal_subscription, user, SnowflakeID.from_short_code(subscription_uid), **form.model_dump())


@AppRouter.api.post("/settings/apps/notification-subscriptions/{subscription_uid}/revoke", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def revoke(subscription_uid: UID, form: SubscriptionGenerationForm, user: User = Auth.scope("user")):
    return _run(revoke_personal_subscription, user, SnowflakeID.from_short_code(subscription_uid),
                expected_generation=form.expected_generation)


@AppRouter.api.post("/settings/apps/notification-subscriptions/{subscription_uid}/test", tags=["AppSettings.Notifications"])
@AuthFilter.add("user")
def test_notification(subscription_uid: UID, form: TestNotificationForm, user: User = Auth.scope("user")):
    return _run(create_test_notification, user, SnowflakeID.from_short_code(subscription_uid), **form.model_dump())


@AppRouter.api.get("/apps/v1/notification-subscriptions/{subscription_uid}/scope", tags=["App.Notifications"])
def read_shared_scope(subscription_uid: UID, authorization: str = Header(default="", max_length=256)):
    return _run(read_shared_notification_scope, _token(authorization), SnowflakeID.from_short_code(subscription_uid))


@AppRouter.api.post("/apps/v1/notification-events/{event_uid}/delivery-authorization", tags=["App.Notifications"])
def authorize(event_uid: UID, body: DeliveryFenceBody, authorization: str = Header(default="", max_length=256)):
    return _run(authorize_notification_delivery, _token(authorization), SnowflakeID.from_short_code(event_uid), **body.model_dump())


@AppRouter.api.post("/apps/v1/notification-events/{event_uid}/receipts", tags=["App.Notifications"])
def receipt(event_uid: UID, body: DeliveryReceiptBody, authorization: str = Header(default="", max_length=256)):
    return _run(record_notification_receipt, _token(authorization), SnowflakeID.from_short_code(event_uid), **body.model_dump())
