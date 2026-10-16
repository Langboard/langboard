"""Administrator creation and signup preserve the same public email errors."""

import pytest
from fastapi.exceptions import RequestValidationError
from langboard.routes.auth.forms.SignUp import SignUpForm
from langboard.routes.settings.Form import CreateUserForm
from pydantic import ValidationError


@pytest.mark.parametrize("form_type", [CreateUserForm, SignUpForm])
@pytest.mark.parametrize(
    "email,error",
    [("user@example.com", None), ("", ValidationError), ("invalid-email", RequestValidationError)],
)
def test_email_validation_at_both_form_entrypoints(form_type, email, error):
    values = dict(firstname="Test", lastname="User", email=email, password="fixture", industry="Other", purpose="test")
    if error:
        with pytest.raises(error) as exc:
            form_type(**values)
        assert exc.value.errors()[0]["loc"] == (("email", "body", "email") if email == "" else ("body", "email"))
        assert exc.value.errors()[0]["input"] == {"email": email}
    else:
        assert form_type(**values).email == email
