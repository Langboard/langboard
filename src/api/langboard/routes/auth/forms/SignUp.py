from langboard_shared.core.routing import BaseFormModel, form_model
from pydantic import field_validator
from ...FormValidation import validate_email_address


@form_model
class CheckEmailForm(BaseFormModel):
    email: str


@form_model
class SignUpForm(BaseFormModel):
    firstname: str
    lastname: str
    email: str
    password: str
    industry: str
    purpose: str
    affiliation: str | None = None
    position: str | None = None

    validate_email = field_validator("email")(validate_email_address)


@form_model
class ResendLinkForm(BaseFormModel):
    email: str


@form_model
class ActivateUserForm(BaseFormModel):
    signup_token: str
