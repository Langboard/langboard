"""Wiki task entry points reuse the native document worker and cache fences."""

from ...core.broker import Broker
from ...domain.services import DomainService
from .ProjectDocumentTask import _embed_project_attachment, _index_project_attachment, _payload


@Broker.wrap_async_task_decorator
async def index_wiki_attachment(request: str):
    service = DomainService()
    try:
        payload = _payload(request)
        if payload:
            _index_project_attachment(
                service,
                *payload,
                documents=service.wiki_document,
                embedding_task="langboard_shared.tasks.docling.WikiDocumentTask.embed_wiki_attachment",
            )
    finally:
        service.close()


@Broker.wrap_async_task_decorator
async def embed_wiki_attachment(request: str):
    service = DomainService()
    try:
        payload = _payload(request)
        if payload:
            await _embed_project_attachment(
                service, *payload, documents=service.wiki_document, source_factory=service.wiki_document.vector_source
            )
    finally:
        service.close()
