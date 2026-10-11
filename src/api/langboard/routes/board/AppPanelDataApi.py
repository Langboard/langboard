"""Generic read bridge for independently registered app panels; no provider credentials."""

from datetime import datetime
from fastapi import Request
from langboard_shared.core.db import DbSession
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.AppCardInteractions import (
    AppCardInteractionConflict,
    list_card_interactions,
    respond_to_app_card_interaction,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppPanel import get_panel
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field
from ...apps.GitHubManifest import GitHubManifestUnavailable
from ...apps.SignalInbox import list_board_signals
from .CardAppInteractionsApi import HumanResponseBody


class PanelSignalsForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Installed adapter support is validated by the authorized native read.
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    after: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,11}$")


class PanelResourcesForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    cursor: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{23}$")


@AppRouter.api.post("/board/{project_uid}/apps/{app_key}/panel/resources", tags=["Board.Apps"])
@AuthFilter.add("user")
def read_panel_resources(project_uid: str, app_key: str, form: PanelResourcesForm,
                         user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    with DbSession.atomic():
        panel = get_panel(service.workflow_stage, user, project_uid, app_key, lock=True)
        if panel is None or "resources.read" not in panel["granted_capabilities"]:
            raise ApiException.NotFound_404()
        if panel["app_revision"] != form.app_revision or panel["binding_revision"] != form.binding_revision:
            raise ApiException.Conflict_409()
        try:
            result = service.workflow_stage.get_connection_context(user, project_uid, form.cursor, app_key=app_key)
        except ValueError:
            raise ApiException.BadRequest_400() from None
        if result is None:
            raise ApiException.NotFound_404()
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/board/{project_uid}/apps/{app_key}/panel/signals", tags=["Board.Apps"])
@AuthFilter.add("user")
def read_panel_signals(request: Request, project_uid: str, app_key: str, form: PanelSignalsForm,
                       user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    with DbSession.atomic():
        # Lock current authority and consent until the native bounded read finishes.
        panel = get_panel(service.workflow_stage, user, project_uid, app_key, lock=True)
        if panel is None or "signals.read" not in panel["granted_capabilities"]:
            raise ApiException.NotFound_404()
        if form.provider not in panel["signal_sources"]:
            raise ApiException.NotFound_404()
        if panel["app_revision"] != form.app_revision or panel["binding_revision"] != form.binding_revision:
            raise ApiException.Conflict_409()
        try:
            result = list_board_signals(service, user, project_uid, form.after,
                channel=request.scope.get("collaboration_channel", CollaborationChannel.Api), provider=form.provider,
                page_size=10, resource_types=panel["resource_types"])
        except GitHubManifestUnavailable:
            raise ApiException.NotFound_404() from None
        except ValueError:
            raise ApiException.BadRequest_400() from None
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


class PanelInteractionsForm(PanelResourcesForm):
    card_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    cursor: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{11}$")
    limit: int = Field(default=10, strict=True, ge=1, le=25)
    interaction_uid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{11}$")


@AppRouter.api.post("/board/{project_uid}/apps/{app_key}/panel/interactions", tags=["Board.Apps"])
@AuthFilter.add("user")
def read_panel_interactions(request: Request, project_uid: str, app_key: str, form: PanelInteractionsForm,
                            user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    """Read only this app's retained requests, under current panel consent and native card ACL."""
    with DbSession.atomic():
        panel = get_panel(service.workflow_stage, user, project_uid, app_key, lock=True)
        if panel is None or "interactions.request" not in panel["granted_capabilities"]:
            raise ApiException.NotFound_404()
        if panel["app_revision"] != form.app_revision or panel["binding_revision"] != form.binding_revision:
            raise ApiException.Conflict_409()
        try:
            result = list_card_interactions(
                service, user, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(form.card_uid),
                limit=form.limit, before=SnowflakeID.from_short_code(form.cursor) if form.cursor else None,
                channel=request.scope.get("collaboration_channel", CollaborationChannel.Api), app_key=app_key,
                interaction_id=SnowflakeID.from_short_code(form.interaction_uid) if form.interaction_uid else None,
            )
        except AppGovernanceDenied:
            raise ApiException.NotFound_404() from None
        except ValueError:
            raise ApiException.BadRequest_400() from None
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


class PanelHumanResponseForm(HumanResponseBody):
    app_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@AppRouter.api.post(
    "/board/{project_uid}/apps/{app_key}/panel/cards/{card_uid}/interactions/{interaction_uid}/responses",
    tags=["Board.Apps"],
)
@AuthFilter.add("user")
def respond_from_panel(request: Request, project_uid: str, app_key: str, card_uid: str, interaction_uid: str,
                       form: PanelHumanResponseForm, user: User = Auth.scope("user"),
                       service: DomainService = DomainService.scope()):
    """The host human form submits; a panel cannot inject an actor or another app's request."""
    with DbSession.atomic():
        panel = get_panel(service.workflow_stage, user, project_uid, app_key, lock=True)
        if panel is None or "interactions.request" not in panel["granted_capabilities"]:
            raise ApiException.NotFound_404()
        if panel["app_revision"] != form.app_revision or panel["binding_revision"] != form.binding_revision:
            raise ApiException.Conflict_409()
        try:
            project_id, card_id, interaction_id = (SnowflakeID.from_short_code(value)
                                                   for value in (project_uid, card_uid, interaction_uid))
            channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
            page = list_card_interactions(service, user, project_id, card_id, app_key=app_key,
                                          interaction_id=interaction_id, limit=1, channel=channel)
            if not page["items"]:
                raise ApiException.NotFound_404()
            result = respond_to_app_card_interaction(
                service, user, project_id, card_id, interaction_id, form.expected_revision,
                form.idempotency_key, form.values, form.execution_generation, channel,
            )
        except AppGovernanceDenied:
            raise ApiException.Forbidden_403() from None
        except AppCardInteractionConflict:
            raise ApiException.Conflict_409() from None
        except ValueError:
            raise ApiException.BadRequest_400() from None
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


class PanelRuntimesForm(PanelResourcesForm):
    card_uid: str = Field(pattern=r"^[A-Za-z0-9]{11}$")
    cursor: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{11}$")


class PanelRuntimeControlForm(PanelResourcesForm):
    generation: int = Field(strict=True, ge=1)
    expected_generation: int | None = Field(default=None, strict=True, ge=1)
    expected_revision: datetime | None = None
    cursor: None = None


def _runtime_panel(service, user, project_uid, app_key, form):
    panel = get_panel(service.workflow_stage, user, project_uid, app_key, lock=True)
    if panel is None or "execution.run" not in panel["granted_capabilities"]:
        raise ApiException.NotFound_404()
    if panel["app_revision"] != form.app_revision or panel["binding_revision"] != form.binding_revision:
        raise ApiException.Conflict_409()


@AppRouter.api.post("/board/{project_uid}/apps/{app_key}/panel/runtimes", tags=["Board.Apps"])
@AuthFilter.add("user")
def read_panel_runtimes(request: Request, project_uid: str, app_key: str, form: PanelRuntimesForm,
                        user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    from langboard_shared.domain.services.AppExecutionControl import list_human_card_runtimes
    with DbSession.atomic():
        _runtime_panel(service, user, project_uid, app_key, form)
        try:
            result = list_human_card_runtimes(service, user, SnowflakeID.from_short_code(project_uid),
                SnowflakeID.from_short_code(form.card_uid), request.scope.get("collaboration_channel", CollaborationChannel.Api),
                before=SnowflakeID.from_short_code(form.cursor) if form.cursor else None, app_key=app_key)
        except AppGovernanceDenied:
            raise ApiException.NotFound_404() from None
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})


@AppRouter.api.post("/board/{project_uid}/apps/{app_key}/panel/cards/{card_uid}/runtimes/{request_uid}/{action}", tags=["Board.Apps"])
@AuthFilter.add("user")
def control_panel_runtime(request: Request, project_uid: str, app_key: str, card_uid: str, request_uid: str,
                          action: str, form: PanelRuntimeControlForm,
                          user: User = Auth.scope("user"), service: DomainService = DomainService.scope()):
    from langboard_shared.domain.services.AppExecutionControl import (
        request_human_runtime_resume,
        request_human_runtime_stop,
    )
    from langboard_shared.domain.services.AppExecutionRequests import AppExecutionRequestConflict
    if action not in ("stop", "resume", "new"):
        raise ApiException.NotFound_404()
    if (action == "new") != (form.expected_generation is not None and form.expected_revision is not None):
        raise ApiException.BadRequest_400()
    if action != "new" and (form.expected_generation is not None or form.expected_revision is not None):
        raise ApiException.BadRequest_400()
    with DbSession.atomic() as db:
        if action == "new":
            from langboard_shared.core.db.DbEngine import DbEngine
            from langboard_shared.tasks.webhooks.ExecutionReadinessUow import ExecutionReadinessUow
            if DbEngine.get_main_engine().dialect.name != "postgresql":
                raise ApiException.Conflict_409()
            ExecutionReadinessUow(db).watch([SnowflakeID.from_short_code(card_uid)])
        _runtime_panel(service, user, project_uid, app_key, form)
        operation = request_human_runtime_stop if action == "stop" else request_human_runtime_resume
        try:
            result = operation(service, user, SnowflakeID.from_short_code(project_uid), SnowflakeID.from_short_code(card_uid),
                SnowflakeID.from_short_code(request_uid), form.generation,
                request.scope.get("collaboration_channel", CollaborationChannel.Api), app_key=app_key,
                **({"new_run": True, "expected_generation": form.expected_generation, "expected_revision": form.expected_revision} if action == "new" else {}))
        except AppGovernanceDenied:
            raise ApiException.NotFound_404() from None
        except AppExecutionRequestConflict:
            raise ApiException.Conflict_409() from None
        except ValueError:
            raise ApiException.BadRequest_400() from None
        return JsonResponse(content=result, headers={"Cache-Control": "no-store"})
