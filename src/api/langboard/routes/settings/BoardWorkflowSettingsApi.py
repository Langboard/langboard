"""Built-in workflow metadata editor and explicit board column semantics."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.BoardWorkflowSettings import (
    list_workflow_stages,
    read_column_workflow,
    save_column_workflow,
    save_stage_metadata,
)
from langboard_shared.security import Auth
from pydantic import Field
from .AppBoardConsentApi import _run


@form_model
class StageMetadataForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=4000)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    translations: dict[str, dict[str, str]]
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@form_model
class ColumnWorkflowForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    workflow_stage: str | None = Field(max_length=32)
    description: str = Field(max_length=4000)
    translations: dict[str, dict[str, str]]
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.get("/settings/workflow-stages", tags=["WorkflowSettings"])
@AuthFilter.add("user")
def list_stages(user: User = Auth.scope("user")):
    return _run(list_workflow_stages, user)


@AppRouter.api.put("/settings/workflow-stages/{stage_key}", tags=["WorkflowSettings"])
@AuthFilter.add("user")
def update_stage(
    stage_key: Annotated[str, Path(pattern=r"^[a-z]{1,32}$")], form: StageMetadataForm, user: User = Auth.scope("user")
):
    return _run(
        save_stage_metadata,
        user,
        stage_key,
        form.name,
        form.description,
        form.color,
        form.translations,
        form.expected_revision,
    )


@AppRouter.api.get("/board/{project_uid}/column/{column_uid}/workflow", tags=["WorkflowSettings"])
@AuthFilter.add("user")
def read_column(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    column_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    user: User = Auth.scope("user"),
):
    return _run(
        read_column_workflow, user, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(column_uid)
    )


@AppRouter.api.put("/board/{project_uid}/column/{column_uid}/workflow", tags=["WorkflowSettings"])
@AuthFilter.add("user")
def update_column(
    project_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    column_uid: Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{11}$")],
    form: ColumnWorkflowForm,
    user: User = Auth.scope("user"),
):
    return _run(
        save_column_workflow,
        user,
        SnowflakeID.from_short_code(project_uid),
        SnowflakeID.from_short_code(column_uid),
        form.workflow_stage,
        form.description,
        form.translations,
        form.expected_revision,
    )
