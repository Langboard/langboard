"""Bounded wiki projections over the native shared retrieval implementation."""

from types import SimpleNamespace
from typing import Literal
from ...helpers import InfraHelper
from . import ProjectDocumentQuery as shared


class WikiDocumentSource(shared.ProjectDocumentSource):
    source_type: Literal["wiki_attachment"] = "wiki_attachment"
    wiki_uid: str


class WikiDocumentRead(shared.ProjectDocumentRead):
    source: WikiDocumentSource


class WikiDocumentMatch(shared.ProjectDocumentMatch):
    source: WikiDocumentSource


class WikiDocumentSearch(shared.ProjectDocumentSearch):
    source: WikiDocumentSource
    matches: list[WikiDocumentMatch]


WikiDocumentReceipt = shared.ProjectDocumentReceipt


def _source(project, attachment):
    wiki_uid = InfraHelper.convert_uid(attachment.project_wiki_id)
    return WikiDocumentSource(
        project_uid=project.get_uid(),
        wiki_uid=wiki_uid,
        attachment_uid=attachment.get_uid(),
        name=attachment.filename,
        url=attachment.file.path,
        board_url=f"/board/{project.get_uid()}/wiki/{wiki_uid}",
    )


def _scoped(service, wiki_uid):
    documents = service.wiki_document

    def readable(user, project, attachment):
        source = documents.get_readable(user, project, attachment)
        if source is None or InfraHelper.convert_uid(source[1].project_wiki_id) != wiki_uid:
            return None
        return source

    return SimpleNamespace(project_document=SimpleNamespace(get_readable=readable), internal_bot=service.internal_bot)


def read_wiki_document(service, user, project_uid, wiki_uid, attachment_uid, *, offset=0, limit=4000):
    return shared.read_project_document(
        _scoped(service, wiki_uid),
        user,
        project_uid,
        attachment_uid,
        offset=offset,
        limit=limit,
        source_factory=_source,
        read_model=WikiDocumentRead,
    )


def request_wiki_document_processing(service, user, project_uid, wiki_uid, attachment_uid):
    from ...core.db import DbSession

    with DbSession.atomic() as db:
        documents = service.wiki_document
        source = documents._authorized_source(db, user, project_uid, attachment_uid, lock=True)
        if source is None or InfraHelper.convert_uid(source[1].project_wiki_id) != wiki_uid:
            raise ValueError("Wiki document unavailable")
        receipt = documents.request(user, project_uid, attachment_uid)
        if receipt is None:
            raise ValueError("Wiki document unavailable")
        return WikiDocumentReceipt.model_validate(receipt)


def search_wiki_document(service, user, project_uid, wiki_uid, attachment_uid, query, *, max_tokens=2000):
    return shared.search_project_document(
        _scoped(service, wiki_uid),
        user,
        project_uid,
        attachment_uid,
        query,
        max_tokens=max_tokens,
        source_factory=_source,
        search_model=WikiDocumentSearch,
        match_model=WikiDocumentMatch,
        source_identity=service.wiki_document.vector_source,
    )
