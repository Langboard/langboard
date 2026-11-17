"""Administrator service template and owner-scoped configuration inheritance."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppEventDestinationTemplates import (
    bind_app_service_template, disable_app_service_template,
    prepare_connection_service_destination, read_connection_service_template,
)
from langboard_shared.security import Auth
from pydantic import Field
from .AppOperation import run_app_operation as _run


UID = Annotated[str, Path(pattern=r"^[A-Za-z0-9]{11}$")]
KEY = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]


@form_model
class ServiceTemplateForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    webhook_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    expected_revision: int | None = Field(default=None, strict=True, ge=1)


@form_model
class ServiceTemplateRevisionForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(strict=True, ge=1)


@form_model
class PrepareServiceDestinationForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_template_revision: int = Field(strict=True, ge=1)
    expected_destination_revision: int | None = Field(default=None, strict=True, ge=1)


@AppRouter.api.post("/settings/apps/registry/{app_key}/service-template", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def bind_template(app_key: KEY, form: ServiceTemplateForm, user: User = Auth.scope("user")):
    return _run(bind_app_service_template, user, app_key, SnowflakeID.from_short_code(form.webhook_uid),
                expected_revision=form.expected_revision)


@AppRouter.api.post("/settings/apps/registry/{app_key}/service-template/disable", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def disable_template(app_key: KEY, form: ServiceTemplateRevisionForm, user: User = Auth.scope("user")):
    return _run(disable_app_service_template, user, app_key, expected_revision=form.expected_revision)


@AppRouter.api.get("/settings/apps/connections/{connection_uid}/service-template", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def read_template(connection_uid: UID, user: User = Auth.scope("user")):
    return _run(read_connection_service_template, user, SnowflakeID.from_short_code(connection_uid))


@AppRouter.api.post("/settings/apps/connections/{connection_uid}/service-destination", tags=["AppSettings.Events"])
@AuthFilter.add("user")
def prepare_destination(connection_uid: UID, form: PrepareServiceDestinationForm, user: User = Auth.scope("user")):
    return _run(prepare_connection_service_destination, user, SnowflakeID.from_short_code(connection_uid),
                expected_template_revision=form.expected_template_revision,
                expected_destination_revision=form.expected_destination_revision)
