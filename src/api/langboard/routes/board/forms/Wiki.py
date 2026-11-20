from langboard_shared.core.db import EditorContentModel
from langboard_shared.core.routing import BaseFormModel, form_model
from pydantic import Field


@form_model
class WikiForm(BaseFormModel):
    title: str = Field(..., description="Wiki title")
    content: EditorContentModel | None = Field(None, description="Wiki content")


@form_model
class ChangeWikiDetailsForm(BaseFormModel):
    editor_generation: int | None = Field(None, ge=0, strict=True)
    expected_revision: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    title: str | None = Field(None, description="Wiki title")
    content: EditorContentModel | None = Field(None, description="Wiki content")


@form_model
class ChangeWikiPublicForm(BaseFormModel):
    is_public: bool = Field(..., description="Wiki public status")
