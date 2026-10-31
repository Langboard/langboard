from typing import Literal
from langboard_shared.core.db import EditorContentModel
from langboard_shared.core.routing import BaseFormModel, form_model
from langboard_shared.domain.contracts.CardNextAction import NextAction
from pydantic import Field, StrictBool


@form_model
class CreateCardForm(BaseFormModel):
    next_action: NextAction = None
    title: str = Field(..., title="Title of the card")
    project_column_uid: str = Field(..., title="UID of the column")
    description: EditorContentModel | None = Field(default=None, title="Description of the card")
    assign_users: list[str] | None = Field(default=None, title="List of user UIDs to assign to the card")


@form_model
class ChangeCardDetailsForm(BaseFormModel):
    next_action: NextAction = None
    title: str | None = Field(default=None, title="Title of the card")
    deadline_at: str | None = Field(default=None, title="Deadline of the card")
    description: EditorContentModel | None = Field(default=None, title="Description of the card")


@form_model
class SetCardCompletedForm(BaseFormModel):
    completed: bool = Field(..., title="Whether the check card's completion item is checked")


@form_model
class UpdateCardLabelsForm(BaseFormModel):
    labels: list[str] = Field(..., title="List of label UIDs")


@form_model
class UpdateCardRelationshipsForm(BaseFormModel):
    is_parent: bool = Field(..., title="Is the card that is being updated the parent card?")
    relationships: list[tuple[str, str]] = Field(..., title="List of tuples of card UID and relationship type UID")


class CardGraphNewCardForm(BaseFormModel):
    client_ref: str = Field(..., title="Request-local reference beginning with new:")
    title: str = Field(..., title="Title of the new card")
    description: str | None = Field(default=None, title="Description of the new card")


class CardGraphEdgeForm(BaseFormModel):
    parent_ref: str = Field(..., title="Existing card UID or request-local new: reference")
    child_ref: str = Field(..., title="Existing card UID or request-local new: reference")
    relationship_type_uid: str = Field(..., title="Relationship type UID")


@form_model
class PatchCardGraphForm(BaseFormModel):
    new_cards: list[CardGraphNewCardForm] = Field(default_factory=list, max_length=7)
    add_edges: list[CardGraphEdgeForm] = Field(default_factory=list, max_length=25)
    remove_relationship_uids: list[str] = Field(default_factory=list, max_length=25)


@form_model
class CardifySelectionForm(BaseFormModel):
    selected_markdown: str = Field(
        ..., min_length=3, max_length=32000, title="Selected body markdown to extract into a child card"
    )


@form_model
class ConvertCheckboxesForm(BaseFormModel):
    pass


@form_model
class CopySelectionToWikiForm(BaseFormModel):
    selected_markdown: str = Field(
        ..., min_length=3, max_length=32000, title="Selected body markdown to copy into a wiki"
    )
    wiki_title: str | None = Field(default=None, max_length=300, title="Optional title for the new wiki")


@form_model
class ChangeCardVisibilityForm(BaseFormModel):
    visibility: Literal["INTERNAL", "SHARED"]
    expected_visibility: Literal["INTERNAL", "SHARED"]
    confirmed: bool = False


@form_model
class ChangeCardOrderForm(BaseFormModel):
    order: int = Field(..., ge=0)
    parent_uid: str = ""
    move_descendants: StrictBool | None = None
    confirmation_revision: str | None = Field(default=None, max_length=64)
