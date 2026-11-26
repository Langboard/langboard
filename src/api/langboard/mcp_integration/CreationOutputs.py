"""Native card creation and public cardification keep distinct result shapes."""

from datetime import datetime
from functools import wraps
from inspect import signature
from typing import Literal
from fastmcp.tools import ToolResult
from pydantic import Field, RootModel, model_validator
from .Outputs import CommandOutput
from .WorkOutputs import (
    CheckitemOutput,
    ChecklistOutput,
    ChecklistPageOutput,
    LabelOutput,
    ProjectionOutput,
    PublicCardOutput,
    RelationshipOutput,
)


class EditorBodyOutput(CommandOutput):
    content: str


class CreatorOutput(CommandOutput):
    uid: str
    type: str
    name: str
    avatar: str | None
    created_at: str


class NativeCardCreationOutput(CommandOutput):
    uid: str
    created_at: datetime | str | None
    updated_at: datetime | str | None
    project_uid: str
    project_column_uid: str
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    next_action: str | None = None
    title: str
    description: EditorBodyOutput
    ai_description: str | None
    deadline_at: datetime | str | None
    order: int = Field(ge=0)
    archived_at: datetime | str | None
    source_type: str | None
    source_uid: str | None
    last_change_seq: int = Field(ge=0)
    last_change_target_type: str
    last_change_target_uid: str | None
    last_change_at: datetime | str | None
    count_comment: int = Field(ge=0)
    member_uids: list[str]
    relationships: list[RelationshipOutput]
    labels: list[LabelOutput]
    creator: CreatorOutput | None
    has_description: bool
    completed: bool
    is_check_card: bool
    visibility: Literal["INTERNAL", "SHARED", "PRIVATE"]


class CardifiedCardOutput(PublicCardOutput):
    member_uids: list[str] | None = None
    project_column_uid: str | None = None
    project_column_name: str | None = None
    order: int | None = Field(default=None, ge=0)
    deadline_at: datetime | str | None = None
    archived_at: datetime | str | None = None


class CardCreationReplayOutput(CommandOutput):
    uid: str
    project_uid: str
    replayed: Literal[True]


class CardCreationOutput(RootModel[NativeCardCreationOutput | CardCreationReplayOutput]):
    pass


def with_card_creation_output(handler):
    """Keep both creation outcomes as top-level MCP objects."""

    @wraps(handler)
    async def validated(**kwargs):
        result = CardCreationOutput.model_validate(await handler(**kwargs))
        return ToolResult(structured_content=result.model_dump(mode="json"))

    validated.__signature__ = signature(handler).replace(return_annotation=ToolResult)
    return validated


class CardificationOutput(CommandOutput):
    card: CardifiedCardOutput
    source_checkitem_uid: str


class ChecklistActionOutput(ProjectionOutput):
    checklist: ChecklistOutput | None = None
    checkitem: CheckitemOutput | None = None
    checklists: ChecklistPageOutput | None = None
    deleted: Literal[True] | None = None
    card: CardifiedCardOutput | None = None
    source_checkitem_uid: str | None = None

    @model_validator(mode="after")
    def require_one_result(self):
        if (
            sum(
                value is not None
                for value in (self.checklist, self.checkitem, self.checklists, self.deleted, self.card)
            )
            != 1
        ):
            raise ValueError("Expected exactly one checklist action result")
        if (self.card is not None) != (self.source_checkitem_uid is not None):
            raise ValueError("Cardification requires its source checkitem")
        return self


CREATION_OUTPUTS = {
    "change_card_checklist": ChecklistActionOutput,
    "create_card": CardCreationOutput,
    "cardify_card_checkitem": CardificationOutput,
}
