"""Authorized storyboard assets for native card attachments."""

from collections import OrderedDict
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, Semaphore
from fastapi import Request
from langboard.media.storyboard import StoryboardUnavailable, generate_storyboard
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiErrorCode, ApiException, AppRouter, JsonResponse
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from langboard_shared.filter import RoleFilter
from langboard_shared.security import Auth, RoleFinder


_CACHE: OrderedDict[str, dict] = OrderedDict()
_CACHE_LOCK = Lock()
_GENERATOR = Semaphore(1)
_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Authorization, Cookie, X-Api-Key, X-Api-Token"}


class _BoundedWriter:
    def __init__(self, file):
        self.file = file

    def write(self, data):
        if self.file.tell() + len(data) > Env.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise StoryboardUnavailable()
        return self.file.write(data)

    def __getattr__(self, name):
        return getattr(self.file, name)


def _authorized_file(service, user, project_uid, card_uid, attachment_uid, channel):
    try:
        file = service.card_attachment.get_authorized_file(user, project_uid, card_uid, attachment_uid, channel=channel)
    except PermissionError as error:
        raise ApiException.Forbidden_403(ApiErrorCode.PE2002) from error
    if file is None:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)
    return file


@AppRouter.api.get(
    "/board/{project_uid}/card/{card_uid}/attachment/{attachment_uid}/storyboard",
    tags=["Board.Card.Attachment"],
)
@RoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
@AuthFilter.add("user")
def get_card_attachment_storyboard(
    project_uid: str,
    card_uid: str,
    attachment_uid: str,
    request: Request,
    user: User = Auth.scope("user"),
    service: DomainService = DomainService.scope(),
) -> JsonResponse:
    channel = request.scope.get("collaboration_channel", CollaborationChannel.Api)
    source = _authorized_file(service, user, project_uid, card_uid, attachment_uid, channel)
    key = source.model_dump_json()
    with _CACHE_LOCK:
        result = _CACHE.get(key)
        if result is not None:
            _CACHE.move_to_end(key)
    if result is None:
        if not _GENERATOR.acquire(blocking=False):
            return JsonResponse({"available": False}, headers=_HEADERS)
        try:
            with NamedTemporaryFile(prefix="langboard-preview-") as file:
                if not Storage.download_file(source, _BoundedWriter(file)):
                    raise ApiException.NotFound_404(ApiErrorCode.NF2009)
                file.flush()
                result = generate_storyboard(Path(file.name))
        except StoryboardUnavailable:
            result = {"available": False}
        finally:
            _GENERATOR.release()
    # Revalidate even cache hits, after potentially slow storage/decoder work.
    if _authorized_file(service, user, project_uid, card_uid, attachment_uid, channel) != source:
        raise ApiException.NotFound_404(ApiErrorCode.NF2009)
    if result.get("available"):
        with _CACHE_LOCK:
            _CACHE[key] = result
            _CACHE.move_to_end(key)
            while len(_CACHE) > 16:
                _CACHE.popitem(last=False)
    return JsonResponse(result, headers=_HEADERS)
