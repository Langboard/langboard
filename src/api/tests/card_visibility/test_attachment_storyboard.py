import json
from types import SimpleNamespace
from typing import cast
import pytest
from fastapi import Request
from langboard.routes.board import BoardCardAttachmentStoryboardApi as route
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import ApiException
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import FileModel
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.filter import RoleFilter


FILE = FileModel(
    storage_type="local",
    storage_name="card_attachment",
    original_filename="video.mp4",
    filename="opaque.mp4",
    path="/private/opaque.mp4",
)
USER = cast(User, SimpleNamespace(id=1))
REQUEST = Request({"type": "http", "collaboration_channel": CollaborationChannel.HumanUI})


@pytest.fixture(autouse=True)
def empty_cache():
    route._CACHE.clear()
    yield
    route._CACHE.clear()


def service(reader):
    return cast(DomainService, SimpleNamespace(card_attachment=SimpleNamespace(get_authorized_file=reader)))


def test_storyboard_uses_native_attachment_read_and_user_filters():
    assert AuthFilter.get_filtered(route.get_card_attachment_storyboard) == "user"
    model, actions, _, _ = RoleFilter.get_filtered(route.get_card_attachment_storyboard)
    assert model is ProjectRole
    assert actions == [ProjectRoleAction.Read.value]


@pytest.mark.parametrize("denial", ["missing", "forbidden"])
def test_denied_attachment_never_decodes_or_reads_storage(monkeypatch, denial):
    def read(*_, **kwargs):
        if denial == "forbidden":
            raise PermissionError()
        return None

    monkeypatch.setattr(route.Storage, "download_file", lambda *_: pytest.fail("Denied storage read"))
    monkeypatch.setattr(route, "generate_storyboard", lambda *_: pytest.fail("Denied decode"))
    error = ApiException.NotFound_404 if denial == "missing" else ApiException.Forbidden_403
    with pytest.raises(error):
        route.get_card_attachment_storyboard("project", "card", "attachment", REQUEST, USER, service(read))


def test_cache_hits_still_revalidate_authority_and_source(monkeypatch):
    calls = []
    current = [FILE]

    def read(*args, **kwargs):
        assert kwargs == {"channel": CollaborationChannel.HumanUI}
        calls.append(args)
        return current[0]

    def download(_, destination):
        destination.write(b"fixture")
        return True

    monkeypatch.setattr(route.Storage, "download_file", download)
    monkeypatch.setattr(
        route, "generate_storyboard", lambda _: {"available": True, "vtt": "WEBVTT\n", "sprite_base64": "AA=="}
    )
    response = route.get_card_attachment_storyboard("project", "card", "attachment", REQUEST, USER, service(read))
    assert response.headers["cache-control"] == "private, no-store"
    assert "Authorization" in response.headers["vary"]
    assert json.loads(bytes(response.body))["available"] is True
    assert len(calls) == 2
    monkeypatch.setattr(route.Storage, "download_file", lambda *_: pytest.fail("Cache hit reread storage"))
    route.get_card_attachment_storyboard("project", "card", "attachment", REQUEST, USER, service(read))
    assert len(calls) == 4
    current[0] = None
    with pytest.raises(ApiException.NotFound_404):
        route.get_card_attachment_storyboard("project", "card", "attachment", REQUEST, USER, service(read))


def test_revocation_during_decode_returns_no_derived_bytes(monkeypatch):
    current = [FILE]

    def download(_, destination):
        destination.write(b"fixture")
        return True

    def decode(_):
        current[0] = None
        return {"available": True, "vtt": "private", "sprite_base64": "private"}

    monkeypatch.setattr(route.Storage, "download_file", download)
    monkeypatch.setattr(route, "generate_storyboard", decode)
    with pytest.raises(ApiException.NotFound_404):
        route.get_card_attachment_storyboard(
            "project", "card", "attachment", REQUEST, USER, service(lambda *_, **kwargs: current[0])
        )
    assert not route._CACHE


def test_unavailable_decoder_preserves_video_as_an_independent_resource(monkeypatch):
    def download(_, destination):
        destination.write(b"fixture")
        return True

    def decode(_):
        raise route.StoryboardUnavailable()

    monkeypatch.setattr(route.Storage, "download_file", download)
    monkeypatch.setattr(route, "generate_storyboard", decode)
    response = route.get_card_attachment_storyboard(
        "project", "card", "attachment", REQUEST, USER, service(lambda *_, **kwargs: FILE)
    )
    assert json.loads(bytes(response.body)) == {"available": False}
    assert not route._CACHE


def test_unauthenticated_http_request_cannot_generate_previews(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard_shared.core.routing import AppRouter

    monkeypatch.setattr(route.Storage, "download_file", lambda *_: pytest.fail("Unauthenticated storage read"))
    monkeypatch.setattr(route, "generate_storyboard", lambda *_: pytest.fail("Unauthenticated decode"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    response = TestClient(app).get("/board/project/card/card/attachment/attachment/storyboard")
    assert response.status_code == 401


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("channel", [CollaborationChannel.HumanUI, CollaborationChannel.Api])
def test_native_private_attachment_http_channel_boundary(current_card, monkeypatch, channel):
    from langboard_shared.core.db import DbSession
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import CardAttachment
    from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService

    user, project, card, card_service = current_card
    CardAttachment.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.use(readonly=False) as db:
        db.insert(ProjectRole(project_id=project.id, user_id=user.id, actions=["read"]))
        attachment = CardAttachment(user_id=user.id, card_id=card.id, filename="clip.mp4", file=FILE)
        db.insert(attachment)
    attachment_service = CardAttachmentService(lambda _: card_service, lambda _: None, SimpleNamespace())
    native = cast(DomainService, SimpleNamespace(card_attachment=attachment_service))
    request = Request({"type": "http", "collaboration_channel": channel})
    stored = []

    def download(source, destination):
        stored.append(source)
        destination.write(b"fixture")
        return True

    monkeypatch.setattr(route.Storage, "download_file", download)
    monkeypatch.setattr(
        route, "generate_storyboard", lambda _: {"available": True, "vtt": "WEBVTT\n", "sprite_base64": "AA=="}
    )
    args = (project.get_uid(), card.get_uid(), attachment.get_uid(), request, user, native)
    if channel == CollaborationChannel.Api:
        with pytest.raises(ApiException.NotFound_404):
            route.get_card_attachment_storyboard(*args)
        assert stored == []
        return
    response = route.get_card_attachment_storyboard(*args)
    assert json.loads(bytes(response.body))["available"] is True
    assert stored == [FILE]
    # A cached preview must not survive a real account revocation.
    with DbSession.use(readonly=False) as db:
        user.activated_at = None
        db.update(user)
    with pytest.raises(ApiException.Forbidden_403):
        route.get_card_attachment_storyboard(*args)
    assert stored == [FILE]
