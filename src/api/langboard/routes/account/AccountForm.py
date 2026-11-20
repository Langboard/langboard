from langboard_shared.core.routing import BaseFormModel, form_model
from langboard_shared.domain.services.ProjectCollections import ProjectCollection
from pydantic import BaseModel, Field, field_validator


class PinnedAppPreference(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=80)


@form_model
class UpdateAppPreferencesForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)
    pinned_apps: list[PinnedAppPreference] = Field(max_length=32)

    @field_validator("pinned_apps")
    @classmethod
    def unique_keys(cls, pins):
        if len({pin.key for pin in pins}) != len(pins):
            raise ValueError("App shortcuts must have unique keys")
        return pins


@form_model
class UpdateProfileForm(BaseFormModel):
    firstname: str
    lastname: str
    affiliation: str | None = None
    position: str | None = None
    delete_avatar: bool = False
    expected_avatar_revision: int | None = Field(default=None, ge=0)


@form_model
class AddNewEmailForm(BaseFormModel):
    new_email: str
    is_resend: bool = False


@form_model
class VerifyNewEmailForm(BaseFormModel):
    verify_token: str


@form_model
class EmailForm(BaseFormModel):
    email: str


@form_model
class ChangePasswordForm(BaseFormModel):
    current_password: str
    new_password: str


@form_model
class CreateUserGroupForm(BaseFormModel):
    name: str


@form_model
class UpdateUserGroupAssignedEmailForm(BaseFormModel):
    emails: list[str]


@form_model
class UpdatePreferredLangForm(BaseFormModel):
    lang: str


@form_model
class UpdateProjectCollectionsForm(BaseFormModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)
    collections: list[ProjectCollection] = Field(max_length=64)
