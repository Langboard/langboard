"""Built-in workflow metadata editor and explicit board column semantics."""

from typing import Annotated
from fastapi import Path
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, BaseFormModel, form_model
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services.BoardWorkflowSettings import (
    read_column_workflow,
    save_column_workflow,
)
from langboard_shared.security import Auth
from pydantic import Field
from .AppBoardConsentApi import _run


@form_model
class ColumnWorkflowForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    workflow_stage: str | None = Field(max_length=32)
    description: str = Field(max_length=4000)
    translations: dict[str, dict[str, str]]
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


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
