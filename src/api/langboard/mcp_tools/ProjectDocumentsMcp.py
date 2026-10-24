"""Typed native Cloud project document tools."""

from typing import Annotated
from langboard_shared.domain.models import User
from langboard_shared.domain.services import ProjectDocumentQuery as document_query
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.domain.services.ProjectDocumentQuery import (
    ProjectDocumentRead,
    ProjectDocumentReceipt,
    ProjectDocumentSearch,
)
from pydantic import Field
from ..mcp_integration import McpTool


Uid = Annotated[str, Field(min_length=1, max_length=80, strict=True)]


@McpTool.add(
    "user", description="Read a bounded native project Cloud document with current membership and source checks."
)
def read_project_document(
    project_uid: Uid,
    attachment_uid: Uid,
    user: User,
    service: DomainService,
    offset: Annotated[int, Field(strict=True, ge=0)] = 0,
    limit: Annotated[int, Field(strict=True, ge=1, le=8000)] = 4000,
) -> ProjectDocumentRead:
    return document_query.read_project_document(service, user, project_uid, attachment_uid, offset=offset, limit=limit)


@McpTool.add(
    "user", description="Search an authorized native project document's current vectors with bounded native citations."
)
def search_project_document(
    project_uid: Uid,
    attachment_uid: Uid,
    query: Annotated[str, Field(strict=True, min_length=1, max_length=1000)],
    user: User,
    service: DomainService,
    max_tokens: Annotated[int, Field(strict=True, ge=128, le=4000)] = 2000,
) -> ProjectDocumentSearch:
    return document_query.search_project_document(
        service, user, project_uid, attachment_uid, query, max_tokens=max_tokens
    )


@McpTool.add(
    "user",
    description="Request configured native project document processing; compatible pending and indexed requests reuse their generation.",
)
def request_project_document_processing(
    project_uid: Uid,
    attachment_uid: Uid,
    user: User,
    service: DomainService,
) -> ProjectDocumentReceipt:
    return document_query.request_project_document_processing(service, user, project_uid, attachment_uid)
