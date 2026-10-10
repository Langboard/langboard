"""Human configuration of existing generic card resources; never execution authority."""

from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.CardAppGovernance import CardAppOwnershipConflict
from langboard_shared.domain.services.CardAppResources import (
    CardAppResourcesUnavailable,
    configure_card_app_resources,
    list_card_app_resource_selections,
    read_card_app_resources,
    set_card_app_resources,
)
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class CardResourcesForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_uids: list[str] = Field(max_length=20)
    expected_revision: int | None = Field(default=None, strict=True, ge=1)


def _configure(operation, request, service, actor, project_uid, card_uid, connection_uid, *args):
    try:
        result = configure_card_app_resources(
            operation,
            service,
            actor,
            project_uid,
            card_uid,
            connection_uid,
            request.scope.get("collaboration_channel", CollaborationChannel.Api),
            *args,
        )
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
    except CardAppResourcesUnavailable:
        raise ApiException.NotFound_404() from None
    except AppGovernanceDenied:
        raise ApiException.Forbidden_403() from None
    except CardAppOwnershipConflict:
        raise ApiException.Conflict_409() from None
    except ValueError:
        raise ApiException.BadRequest_400() from None


@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/apps/connections/{connection_uid}/resources", tags=["Board.Card"]
)
@AuthFilter.add("user")
def read_resources(
    request: Request,
    project_uid: str,
    card_uid: str,
    connection_uid: str,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _configure(read_card_app_resources, request, service, user, project_uid, card_uid, connection_uid)


@AppRouter.api.put(
    "/board/{project_uid}/card/{card_uid}/apps/connections/{connection_uid}/resources", tags=["Board.Card"]
)
@AuthFilter.add("user")
def select_resources(
    request: Request,
    project_uid: str,
    card_uid: str,
    connection_uid: str,
    form: CardResourcesForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _configure(
        set_card_app_resources,
        request,
        service,
        user,
        project_uid,
        card_uid,
        connection_uid,
        form.resource_uids,
        form.expected_revision,
    )


@AppRouter.api.get("/board/{project_uid}/apps/resource-context", tags=["Board.Apps"])
@AuthFilter.add("user")
def read_resource_context(
    project_uid: str,
    cursor: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{23}$"),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    try:
        result = service.workflow_stage.get_connection_context(user, project_uid, cursor)
    except ValueError:
        raise ApiException.BadRequest_400() from None
    if result is None:
        raise ApiException.NotFound_404()
    return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/apps/resource-selections", tags=["Board.Card"])
@AuthFilter.add("user")
def list_resource_selections(
    request: Request,
    project_uid: str,
    card_uid: str,
    after: str | None = Query(default=None, pattern=r"^[A-Za-z0-9]{11}$"),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _configure(list_card_app_resource_selections, request, service, user, project_uid, card_uid, None, after)
