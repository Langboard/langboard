"""Optional native card-stage binding. Approval never dispatches execution."""

from fastapi import Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.DecisionGate import DecisionGate, DecisionGateBlocked
from langboard_shared.security import Auth
from pydantic import BaseModel, Field
from .BoardDecisionApi import _channel
from .BoardDecisionApi import _result as _decision_result


def _result(operation):
    try:
        return _decision_result(operation)
    except DecisionGateBlocked as exc:
        return JsonResponse(content=exc.payload(), status_code=409)


class ConfigureDecisionGateForm(BaseModel):
    target_stage: str = Field(min_length=1, max_length=128)
    decision_uid: str = Field(pattern=r"^[0-9A-Za-z]{11}$")
    round_id: str = Field(min_length=1, max_length=128)
    target_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/decision-gate", tags=["Board.Decision"])
@AuthFilter.add("user")
def read_decision_gate(
    project_uid: str,
    card_uid: str,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(lambda: DecisionGate(service).read(user, project_uid, card_uid, channel=_channel(request)))


@AppRouter.api.put("/board/{project_uid}/card/{card_uid}/decision-gate", tags=["Board.Decision"])
@AuthFilter.add("user")
def configure_decision_gate(
    project_uid: str,
    card_uid: str,
    request: Request,
    form: ConfigureDecisionGateForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        lambda: DecisionGate(service).configure(user, project_uid, card_uid, form, channel=_channel(request))
    )


@AppRouter.api.delete("/board/{project_uid}/card/{card_uid}/decision-gate/{target_stage}", tags=["Board.Decision"])
@AuthFilter.add("user")
def remove_decision_gate(
    project_uid: str,
    card_uid: str,
    target_stage: str,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _result(
        lambda: DecisionGate(service).remove(user, project_uid, card_uid, target_stage, channel=_channel(request))
    )
