"""Human-selected built-in workflow mapping; independent from app read grants."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.AppWorkflowMapping import read_workflow_mapping, save_workflow_mapping
from langboard_shared.security import Auth
from pydantic import Field
from .AppOperation import run_consent_operation as _run


@form_model
class WorkflowMappingForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    mapping: dict[str, str]
    enabled: bool = Field(strict=True)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.get("/board/{project_uid}/apps/{app_key}/workflow-mapping", tags=["AppSettings.WorkflowMapping"])
@AuthFilter.add("user")
def read_mapping(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    user: User = Auth.scope("user"),
):
    return _run(read_workflow_mapping, user, SnowflakeID.from_short_code(project_uid), app_key)


@AppRouter.api.put("/board/{project_uid}/apps/{app_key}/workflow-mapping", tags=["AppSettings.WorkflowMapping"])
@AuthFilter.add("user")
def save_mapping(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    app_key: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,31}$")],
    form: WorkflowMappingForm,
    user: User = Auth.scope("user"),
):
    return _run(
        save_workflow_mapping,
        user,
        SnowflakeID.from_short_code(project_uid),
        app_key,
        form.mapping,
        form.enabled,
        form.expected_revision,
        form.expected_app_revision,
    )
