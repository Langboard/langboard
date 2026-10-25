from typing import Annotated
from fastapi import HTTPException, Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.MailDeliveryHistory import MailDeliveryHistory
from langboard_shared.domain.services.MailDeliveryResourceLinks import MailDeliveryResourceLinks
from langboard_shared.security import Auth


@AppRouter.api.get("/settings/mail/deliveries", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
def get_mail_deliveries(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    view: Annotated[str, Query(max_length=12)] = "legacy",
    status: Annotated[str | None, Query(max_length=20)] = None,
    created_from: Annotated[str | None, Query(max_length=40)] = None,
    created_to: Annotated[str | None, Query(max_length=40)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> JsonResponse:
    try:
        page = MailDeliveryHistory.page(
            limit=limit, view=view, status=status, created_from=created_from, created_to=created_to, cursor=cursor
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid mail history filters or cursor") from None
    return JsonResponse(content=page, headers={"Cache-Control": "no-store"})


@AppRouter.api.get("/settings/mail/deliveries/{source}/{uid}", tags=["AppSettings.Mail"])
@AuthFilter.add("admin")
def get_mail_delivery(
    source: str,
    uid: str,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    item = MailDeliveryHistory.detail(source, uid)
    if item is None:
        raise HTTPException(status_code=404, detail="Mail delivery not found")
    item["resources"] = MailDeliveryResourceLinks.resolve(
        source, uid, user, service, request.scope.get("collaboration_channel", CollaborationChannel.Api)
    )
    return JsonResponse(content=item, headers={"Cache-Control": "no-store"})
