from mimetypes import guess_type
from tempfile import SpooledTemporaryFile
from typing import Iterator
from fastapi import Path, Request
from langboard_shared.core.routing import ApiException, AppRouter
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.core.storage.BaseStorage import BaseStorage
from langboard_shared.domain.models import User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from langboard_shared.helpers import MiddlewareHelper
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse


_DOWNLOAD_CHUNK_SIZE = 64 * 1024
_DOWNLOAD_SPOOL_MEMORY_LIMIT = 1024 * 1024


class _BoundedDownload:
    def __init__(self, file):
        self.file = file

    def write(self, chunk):
        if self.file.tell() + len(chunk) > Env.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise ApiException.NotFound_404()
        return self.file.write(chunk)

    def __getattr__(self, name):
        return getattr(self.file, name)


def _read_chunks(file: SpooledTemporaryFile[bytes]) -> Iterator[bytes]:
    while chunk := file.read(_DOWNLOAD_CHUNK_SIZE):
        yield chunk


@AppRouter.api.get("/file/{storage_type}/{storage_name}/{filename}", tags=["General"])
def get_file(
    request: Request,
    storage_type: str = Path(),
    storage_name: str = Path(),
    filename: str = Path(),
    service: DomainService = DomainService.scope(),
) -> StreamingResponse:
    if (
        storage_name not in {item.value for item in StorageName}
        or not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
    ):
        raise ApiException.NotFound_404()
    protected = storage_name in {
        StorageName.CardAttachment.value,
        StorageName.Wiki.value,
        StorageName.ProjectAttachment.value,
    }
    actor = None
    backend = None
    source = None
    if protected:
        actor = MiddlewareHelper.validate_auth(request.scope)
        if not isinstance(actor, User):
            raise ApiException.NotFound_404()
        backend = BaseStorage.decrypt_storage_type(storage_type)
        source = _readable_attachment(service, actor, request, backend, storage_name, filename)
        if source is None:
            raise ApiException.NotFound_404()
    media_type, _ = guess_type(
        source.filename
        if source is not None
        and storage_name
        in {StorageName.CardAttachment.value, StorageName.ProjectAttachment.value, StorageName.Wiki.value}
        else filename
    )

    file = SpooledTemporaryFile(max_size=_DOWNLOAD_SPOOL_MEMORY_LIMIT, mode="w+b")
    try:
        downloaded = (
            Storage.download_file(source.file, _BoundedDownload(file))
            if source is not None
            and storage_name
            in {StorageName.CardAttachment.value, StorageName.ProjectAttachment.value, StorageName.Wiki.value}
            else Storage.download(storage_type, storage_name, filename, _BoundedDownload(file) if protected else file)
        )
        if not downloaded:
            raise ApiException.NotFound_404()
        if protected:
            latest = _readable_attachment(service, actor, request, backend, storage_name, filename)
            if latest is None or latest.id != source.id or latest.file != source.file:
                raise ApiException.NotFound_404()
    except Exception:
        file.close()
        raise
    file.seek(0)

    return StreamingResponse(
        _read_chunks(file),
        media_type=media_type,
        background=BackgroundTask(file.close),
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization, Cookie, X-Api-Key, X-Api-Token"}
        if protected
        else None,
    )


def _readable_attachment(service, actor, request, backend, storage_name, filename):
    if storage_name == StorageName.ProjectAttachment.value:
        return service.project_attachment.resolve_readable_file(actor, backend, storage_name, filename)
    if storage_name == StorageName.Wiki.value:
        return service.project_wiki.resolve_readable_file(actor, backend, storage_name, filename)
    attachment = service.card_attachment.resolve_file_owner(backend, storage_name, filename)
    if attachment is None:
        return None
    resolved = service.card.resolve_readable_card(
        None, attachment.card_id, actor, request.scope.get("collaboration_channel", CollaborationChannel.Api)
    )
    if resolved is None:
        return None
    project, card, _ = resolved
    if card.is_linked_resource:
        return None
    actions = service.project.get_user_role_actions_by_project(actor, project)
    if "*" not in actions and ProjectRoleAction.Read.value not in actions:
        return None
    return attachment
