"""Human UI discussion endpoints. No MCP facade or public-comment conversion."""

from typing import Annotated, Literal
from fastapi import Query, Request
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardVisibilityPolicy import CollaborationChannel
from langboard_shared.domain.services.WhisperThreads import (
    WhisperUnavailable,
    add_participants,
    create_thread,
    list_participants,
    list_threads,
    mark_read,
    read_messages,
    reply,
    set_state,
)
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class WhisperContentForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16000)
    mention_uids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=10)


class WhisperCreateForm(WhisperContentForm):
    relationship_uid: str | None = Field(default=None, max_length=100)
    subanchor_type: Literal["content_block", "comment", "checklist", "attachment"] | None = None
    subanchor_uid: str | None = Field(default=None, max_length=100)


class WhisperStateForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["OPEN", "RESOLVED"]


class WhisperReadForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_uid: str = Field(min_length=1, max_length=100)


def _run(operation, request, user, service, project_uid, card_uid, *args, **kwargs):
    headers = {"Cache-Control": "no-store"}
    try:
        result = operation(
            service,
            user,
            project_uid,
            card_uid,
            request.scope.get("collaboration_channel", CollaborationChannel.Api),
            *args,
            **kwargs,
        )
        return JsonResponse(content=result, headers=headers)
    except WhisperUnavailable:
        return JsonResponse(content={"detail": "Not found"}, status_code=404, headers=headers)
    except ValueError:
        return JsonResponse(content={"detail": "Invalid whisper request"}, status_code=400, headers=headers)


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/whispers", tags=["Board.Card"])
@AuthFilter.add("user")
def get_threads(
    request: Request,
    project_uid: str,
    card_uid: str,
    before_uid: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=25, ge=1, le=50),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(list_threads, request, user, service, project_uid, card_uid, before_uid=before_uid, limit=limit)


@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/whispers", tags=["Board.Card"])
@AuthFilter.add("user")
def post_thread(
    request: Request,
    project_uid: str,
    card_uid: str,
    form: WhisperCreateForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(create_thread, request, user, service, project_uid, card_uid, **form.model_dump())


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/whispers/{thread_uid}/messages", tags=["Board.Card"])
@AuthFilter.add("user")
def get_messages(
    request: Request,
    project_uid: str,
    card_uid: str,
    thread_uid: str,
    before_uid: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=25, ge=1, le=50),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(
        read_messages, request, user, service, project_uid, card_uid, thread_uid, before_uid=before_uid, limit=limit
    )


@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/whispers/{thread_uid}/messages", tags=["Board.Card"])
@AuthFilter.add("user")
def post_reply(
    request: Request,
    project_uid: str,
    card_uid: str,
    thread_uid: str,
    form: WhisperContentForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(
        reply, request, user, service, project_uid, card_uid, thread_uid, form.content, mention_uids=form.mention_uids
    )


@AppRouter.api.put("/board/{project_uid}/card/{card_uid}/whispers/{thread_uid}/read", tags=["Board.Card"])
@AuthFilter.add("user")
def put_read(
    request: Request,
    project_uid: str,
    card_uid: str,
    thread_uid: str,
    form: WhisperReadForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(mark_read, request, user, service, project_uid, card_uid, thread_uid, form.message_uid)


@AppRouter.api.patch("/board/{project_uid}/card/{card_uid}/whispers/{thread_uid}", tags=["Board.Card"])
@AuthFilter.add("user")
def patch_state(
    request: Request,
    project_uid: str,
    card_uid: str,
    thread_uid: str,
    form: WhisperStateForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(set_state, request, user, service, project_uid, card_uid, thread_uid, form.state)


class WhisperParticipantsForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_uids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1, max_length=10)


@AppRouter.api.post("/board/{project_uid}/card/{card_uid}/whispers/{thread_uid}/participants", tags=["Board.Card"])
@AuthFilter.add("user")
def post_participants(
    request: Request,
    project_uid: str,
    card_uid: str,
    thread_uid: str,
    form: WhisperParticipantsForm,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(add_participants, request, user, service, project_uid, card_uid, thread_uid, form.user_uids)


@AppRouter.api.get("/board/{project_uid}/card/{card_uid}/whispers/{thread_uid}/participants", tags=["Board.Card"])
@AuthFilter.add("user")
def get_participants(
    request: Request,
    project_uid: str,
    card_uid: str,
    thread_uid: str,
    before_uid: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=25, ge=1, le=50),
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
):
    return _run(
        list_participants, request, user, service, project_uid, card_uid, thread_uid, before_uid=before_uid, limit=limit
    )
