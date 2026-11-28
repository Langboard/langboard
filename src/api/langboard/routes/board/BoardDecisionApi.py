"""Authenticated native judgments, Polls and optional native approval policies. Responses never dispatch."""

from typing import Annotated
from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.DecisionFlow import (
    DecisionConflict,
    DecisionDenied,
    DecisionFlow,
    DecisionMissing,
)
from langboard_shared.security import Auth
from .forms.Decision import CreateDecisionForm, DecisionResponseForm


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


def _channel(request):
    return request.scope.get("collaboration_channel", CollaborationChannel.Api)


@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/decisions", tags=["Board.Decision"])
@AuthFilter.add("user")
def create_decision(
    project_uid: str,
    card_uid: str,
    form: CreateDecisionForm,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(lambda: DecisionFlow(service).create(user, project_uid, card_uid, form, channel=_channel(request)))


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/decisions", tags=["Board.Decision"])
@AuthFilter.add("user")
def list_decisions(
    project_uid: str,
    card_uid: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    before: Annotated[str | None, Query(pattern=r"^[0-9A-Za-z]{11}$")] = None,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        lambda: DecisionFlow(service).list(
            user, project_uid, card_uid, channel=_channel(request), limit=limit, before=before
        )
    )


@AppRouter.api.post("/board/{project_uid}/decisions/{decision_uid}/responses", tags=["Board.Decision"])
@AuthFilter.add("user")
def respond_to_decision(
    project_uid: str,
    decision_uid: str,
    form: DecisionResponseForm,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        lambda: DecisionFlow(service).respond(user, project_uid, decision_uid, form, channel=_channel(request))
    )


@AppRouter.api.get("/board/{project_uid}/my-decisions", tags=["Board.Decision"])
@AuthFilter.add("user")
def my_decisions(
    project_uid: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    before: Annotated[str | None, Query(pattern=r"^[0-9A-Za-z]{11}$")] = None,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        lambda: DecisionFlow(service).my_decisions(
            user, project_uid, channel=_channel(request), limit=limit, before=before
        )
    )
