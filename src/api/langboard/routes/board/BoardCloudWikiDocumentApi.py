"""Authenticated native Cloud document reads, processing and semantic queries."""

from typing import Annotated
from fastapi import Query
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, AppRouter, JsonResponse
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService, WikiDocumentQuery
from langboard_shared.security import Auth
from pydantic import BaseModel, ConfigDict, Field


class WikiDocumentSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=1000)
    max_tokens: int = Field(default=2000, ge=128, le=4000)


def _response(operation, *args, **kwargs):
    try:
        result = operation(*args, **kwargs)
    except ValueError:
        return JsonResponse(
            content=ApiErrorCode.VA0000,
            status_code=400,
            headers={
                "Cache-Control": "private, no-store",
                "Vary": "Authorization, Cookie, X-Api-Key, X-Api-Token",
            },
        )
    return JsonResponse(
        content=result.model_dump(),
        headers={
            "Cache-Control": "private, no-store",
            "Vary": "Authorization, Cookie, X-Api-Key, X-Api-Token",
        },
    )


@AppRouter.api.get(
    "/board/{project_uid}/cloud/wiki/{wiki_uid}/attachment/{attachment_uid}/document", tags=["Board.Cloud"]
)
@AuthFilter.add("user")
def read_wiki_document(
    project_uid: str,
    wiki_uid: str,
    attachment_uid: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=8000)] = 4000,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return _response(
        WikiDocumentQuery.read_wiki_document,
        service,
        user,
        project_uid,
        wiki_uid,
        attachment_uid,
        offset=offset,
        limit=limit,
    )


@AppRouter.api.post(
    "/board/{project_uid}/cloud/wiki/{wiki_uid}/attachment/{attachment_uid}/document/process", tags=["Board.Cloud"]
)
@AuthFilter.add("user")
def request_wiki_document_processing(
    project_uid: str,
    wiki_uid: str,
    attachment_uid: str,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return _response(
        WikiDocumentQuery.request_wiki_document_processing, service, user, project_uid, wiki_uid, attachment_uid
    )


@AppRouter.api.post(
    "/board/{project_uid}/cloud/wiki/{wiki_uid}/attachment/{attachment_uid}/document/search", tags=["Board.Cloud"]
)
@AuthFilter.add("user")
def search_wiki_document(
    project_uid: str,
    wiki_uid: str,
    attachment_uid: str,
    form: WikiDocumentSearchInput,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    return _response(
        WikiDocumentQuery.search_wiki_document,
        service,
        user,
        project_uid,
        wiki_uid,
        attachment_uid,
        form.query,
        max_tokens=form.max_tokens,
    )
