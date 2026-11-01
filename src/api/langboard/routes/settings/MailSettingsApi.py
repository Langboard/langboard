from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.services.factory.EmailService import EmailService
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


@AppRouter.api.get("/settings/mail/configuration", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
def get_mail_configuration() -> JsonResponse:
    return JsonResponse(content=EmailService.effective_smtp_config())


@AppRouter.api.post("/settings/mail/diagnostics", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
async def diagnose_mail_connection() -> JsonResponse:
    return JsonResponse(content=await EmailService.diagnose_connection())


class MailTestMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: EmailStr = Field(max_length=254)

    @field_validator("recipient", mode="before")
    @classmethod
    def single_address(cls, value):
        # EmailStr accepts display names; this endpoint requires a bare mailbox.
        if not isinstance(value, str) or any(char in value for char in "\r\n,;<>\x00"):
            raise ValueError("A single bare email address is required")
        return value


@AppRouter.api.post("/settings/mail/test-message", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
async def send_mail_test_message(body: MailTestMessageBody) -> JsonResponse:
    return JsonResponse(content=await EmailService.send_test_message(str(body.recipient)))
