"""Administrator-managed app delivery destination using existing webhook settings."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppEventDestination import (
    bind_app_event_destination,
    manage_app_event_destination,
)
from langboard_shared.security import Auth
from pydantic import Field
from .AppOperation import run_app_operation as _run


@form_model
class BindDestinationForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    webhook_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    expected_revision: int | None = Field(default=None, strict=True, ge=1)


@form_model
class DisableDestinationForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(strict=True, ge=1)


@AppRouter.api.post("/settings/apps/connections/{connection_uid}/event-destination", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def bind_destination(
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: BindDestinationForm,
    user: User = Auth.scope("user"),
):
    return _run(
        bind_app_event_destination,
        user,
        SnowflakeID.from_short_code(connection_uid),
        SnowflakeID.from_short_code(form.webhook_uid),
        form.expected_revision,
    )


@AppRouter.api.get("/settings/apps/connections/{connection_uid}/event-destination", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def read_destination(
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")], user: User = Auth.scope("user")
):
    return _run(manage_app_event_destination, user, SnowflakeID.from_short_code(connection_uid))


@AppRouter.api.post(
    "/settings/apps/connections/{connection_uid}/event-destination/disable", tags=["AppSettings.Events"]
)
@AuthFilter.add("user")
def disable_destination(
    connection_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")],
    form: DisableDestinationForm,
    user: User = Auth.scope("user"),
):
    return _run(
        manage_app_event_destination,
        user,
        SnowflakeID.from_short_code(connection_uid),
        expected_revision=form.expected_revision,
        disable=True,
    )


@AppRouter.api.post("/settings/apps/events/{event_uid}/redeliver", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def redeliver_event(event_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")], user: User = Auth.scope("user")):
    from langboard_shared.domain.services.AppEventDelivery import request_event_redelivery

    return _run(request_event_redelivery, user, SnowflakeID.from_short_code(event_uid))


@AppRouter.api.get("/settings/apps/events/{event_uid}/delivery", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def read_event_delivery(event_uid: Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")], user: User = Auth.scope("user")):
    from langboard_shared.domain.services.AppEventDelivery import read_event_delivery as read_delivery

    return _run(read_delivery, user, SnowflakeID.from_short_code(event_uid))
