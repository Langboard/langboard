from typing import Annotated
from fastapi import HTTPException, Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.services.MailDeliveryHistory import MailDeliveryHistory


@AppRouter.api.get("/settings/mail/deliveries", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
def get_mail_deliveries(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    status: Annotated[str | None, Query(max_length=20)] = None,
    created_from: Annotated[str | None, Query(max_length=40)] = None,
    created_to: Annotated[str | None, Query(max_length=40)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> JsonResponse:
    try:
        page = MailDeliveryHistory.page(
            limit=limit, status=status, created_from=created_from, created_to=created_to, cursor=cursor
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid mail history filters or cursor") from None
    return JsonResponse(content=page)


@AppRouter.api.get("/settings/mail/deliveries/{source}/{uid}", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
def get_mail_delivery(source: str, uid: str) -> JsonResponse:
    item = MailDeliveryHistory.detail(source, uid)
    if item is None:
        raise HTTPException(status_code=404, detail="Mail delivery not found")
    return JsonResponse(content=item)
