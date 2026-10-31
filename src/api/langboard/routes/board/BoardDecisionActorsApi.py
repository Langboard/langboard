"""Authenticated proposal and poll responder selection. No round creation or execution."""

from typing import Annotated, Literal
from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.DecisionActors import DecisionActors
from langboard_shared.domain.services.DecisionFlow import DecisionConflict, DecisionDenied, DecisionMissing
from langboard_shared.security import Auth
from .forms.DecisionActors import DecisionActorsPreview


def _result(operation):
    try:
        return JsonResponse(content=operation(), headers={"Cache-Control": "no-store"})
    except DecisionMissing as exc:
        raise ApiException.NotFound_404() from exc
    except DecisionDenied as exc:
        raise ApiException.Forbidden_403() from exc
    except DecisionConflict as exc:
        raise ApiException.Conflict_409() from exc
    except ValueError as exc:
        raise ApiException.BadRequest_400() from exc


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/decision-actors/candidates", tags=["Board.Decision"])
@AuthFilter.add("user")
def actor_candidates(
    project_uid: str,
    card_uid: str,
    request: Request,
    source_kind: Literal["proposal", "poll"],
    kind: Literal["user", "group", "team"],
    query: Annotated[str, Query(max_length=200)] = "",
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        lambda: DecisionActors(service).candidates(
            user,
            project_uid,
            card_uid,
            source_kind=source_kind,
            kind=kind,
            query=query,
            page=page,
            limit=limit,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    )


@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/decision-actors/preview", tags=["Board.Decision"])
@AuthFilter.add("user")
def preview_actors(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: DecisionActorsPreview,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        lambda: DecisionActors(service).preview(
            user,
            project_uid,
            card_uid,
            [source.model_dump() for source in form.sources],
            source_kind=form.source_kind,
            channel=request.scope.get("collaboration_channel", CollaborationChannel.Api),
        )
    )
