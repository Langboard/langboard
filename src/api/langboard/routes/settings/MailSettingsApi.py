from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.services.factory.EmailService import EmailService


@AppRouter.api.get("/settings/mail/configuration", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
def get_mail_configuration() -> JsonResponse:
    return JsonResponse(content=EmailService.effective_smtp_config())


@AppRouter.api.post("/settings/mail/diagnostics", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
async def diagnose_mail_connection() -> JsonResponse:
    return JsonResponse(content=await EmailService.diagnose_connection())
