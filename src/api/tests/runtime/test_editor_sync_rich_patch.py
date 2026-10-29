from unittest.mock import Mock
import orjson
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langboard.middlewares import ApiAuthMiddleware
from langboard.routes.auth import SocketAuthorization
from langboard.routes.editor import EditorSyncApi
from langboard.routes.editor.EditorSyncApi import PatchEditorSyncRichForm, request_editor_sync_rich_patch
from langboard_shared.core.publisher import BaseSocketPublisher
from langboard_shared.core.routing import ApiException, AppRouter, JsonResponse, SocketTopic
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.helpers import MiddlewareHelper
from pydantic import ValidationError
from pytest import MonkeyPatch


def create_request() -> Request:
    return Request({"type": "http", "headers": []})


def test_restore_precondition_uses_the_existing_authenticated_owner_request(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(SocketAuthorization, "is_subscription_authorized", lambda service, user, topic, topic_id: True)
    response = Mock(status_code=409)
    response.json.return_value = {"message": "Changed"}
    post = Mock(return_value=response)
    monkeypatch.setattr(EditorSyncApi.requests, "post", post)
    form = PatchEditorSyncRichForm(
        document_name="card:card-uid:description", value="Retained draft", restore_snapshot="AAA="
    )

    result = request_editor_sync_rich_patch(create_request(), form, User.model_construct(), DomainService())

    assert result.status_code == 409
    assert post.call_args.kwargs["json"] == {
        "document_name": "card:card-uid:description",
        "value": "Retained draft",
        "restore_snapshot": "AAA=",
    }
    assert "x-editor-route-key" in {key.lower() for key in post.call_args.kwargs["headers"]}


@pytest.mark.parametrize("document_name", ["card:card-uid:description", "wiki:wiki-uid:content:1"])
def test_restore_precondition_cannot_bypass_acl_or_generation(monkeypatch: MonkeyPatch, document_name: str) -> None:
    monkeypatch.setattr(SocketAuthorization, "is_subscription_authorized", lambda *args: False)
    monkeypatch.setattr(SocketAuthorization, "editor_document_generation_is_current", lambda *args: False)
    forward = Mock()
    monkeypatch.setattr(EditorSyncApi, "_forward_to_socket", forward)

    with pytest.raises(ApiException.Forbidden_403):
        request_editor_sync_rich_patch(
            create_request(),
            PatchEditorSyncRichForm(document_name=document_name, value="Retained draft", restore_snapshot="AAA="),
            User.model_construct(),
            DomainService(),
        )

    forward.assert_not_called()


def test_restore_snapshot_counts_toward_the_existing_request_limit(monkeypatch: MonkeyPatch) -> None:
    forward = Mock()
    monkeypatch.setattr(EditorSyncApi, "_forward_to_socket", forward)
    result = request_editor_sync_rich_patch(
        create_request(),
        PatchEditorSyncRichForm(
            document_name="card:card-uid:description",
            value="Small draft",
            restore_snapshot="A" * (EditorSyncApi.Env.EDITOR_SYNC_MAX_REQUEST_SIZE_MB * 1024 * 1024),
        ),
        User.model_construct(),
        DomainService(),
    )

    assert result.status_code == 413
    forward.assert_not_called()


@pytest.mark.parametrize("snapshot", [None, "", 42, False])
def test_invalid_supplied_restore_snapshot_cannot_fall_back_to_an_ordinary_patch(snapshot) -> None:
    with pytest.raises(ValidationError):
        PatchEditorSyncRichForm(document_name="card:card-uid:description", value="Retained", restore_snapshot=snapshot)


@pytest.mark.parametrize(
    ("document_name", "topic", "topic_id"),
    [
        ("card:card-uid:description", SocketTopic.BoardCard, "card-uid"),
        ("wiki:wiki-uid:content", SocketTopic.BoardWikiPrivate, "wiki-uid"),
    ],
)
def test_rich_patch_forwards_to_the_authorized_document_owner(
    monkeypatch: MonkeyPatch, document_name: str, topic: SocketTopic, topic_id: str
) -> None:
    checked: list[tuple[SocketTopic, str]] = []

    def authorize(service: DomainService, user: User, requested_topic: SocketTopic, requested_topic_id: str) -> bool:
        checked.append((requested_topic, requested_topic_id))
        return True

    monkeypatch.setattr(SocketAuthorization, "is_subscription_authorized", authorize)
    publish = Mock()
    monkeypatch.setattr(BaseSocketPublisher, "put_dispather", publish)
    socket_response = Mock(status_code=200)
    socket_response.json.return_value = {}
    post = Mock(return_value=socket_response)
    monkeypatch.setattr(EditorSyncApi.requests, "post", post)

    response = request_editor_sync_rich_patch(
        create_request(),
        PatchEditorSyncRichForm(document_name=document_name, value="New draft"),
        User.model_construct(),
        DomainService(),
    )

    assert orjson.loads(response.body) == {}
    assert checked == [(topic, topic_id)]
    publish.assert_not_called()
    post.assert_called_once()
    assert post.call_args.args[0].endswith("/editor-sync/rich/patch-request")
    assert post.call_args.kwargs["json"] == {"document_name": document_name, "value": "New draft"}


@pytest.mark.parametrize("document_name", ["unknown:one", "card:", "card:one:two:three"])
def test_rich_patch_rejects_invalid_document_names(monkeypatch: MonkeyPatch, document_name: str) -> None:
    publish = Mock()
    monkeypatch.setattr(BaseSocketPublisher, "put_dispather", publish)

    with pytest.raises(ApiException.Forbidden_403):
        request_editor_sync_rich_patch(
            create_request(),
            PatchEditorSyncRichForm(document_name=document_name, value="New draft"),
            User.model_construct(),
            DomainService(),
        )

    publish.assert_not_called()


def test_rich_patch_rejects_denied_document_access(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(SocketAuthorization, "is_subscription_authorized", lambda service, user, topic, topic_id: False)
    publish = Mock()
    monkeypatch.setattr(BaseSocketPublisher, "put_dispather", publish)

    with pytest.raises(ApiException.Forbidden_403):
        request_editor_sync_rich_patch(
            create_request(),
            PatchEditorSyncRichForm(document_name="card:card-uid:description", value="New draft"),
            User.model_construct(),
            DomainService(),
        )

    publish.assert_not_called()


def test_rich_patch_rejects_payloads_above_the_editor_request_limit(monkeypatch: MonkeyPatch) -> None:
    publish = Mock()
    active_lookup = Mock()
    monkeypatch.setattr(BaseSocketPublisher, "put_dispather", publish)
    monkeypatch.setattr(EditorSyncApi, "_forward_to_socket", active_lookup)

    response = request_editor_sync_rich_patch(
        create_request(),
        PatchEditorSyncRichForm(
            document_name="card:card-uid:description",
            value="x" * (EditorSyncApi.Env.EDITOR_SYNC_MAX_REQUEST_SIZE_MB * 1024 * 1024),
        ),
        User.model_construct(),
        DomainService(),
    )

    assert response.status_code == 413
    active_lookup.assert_not_called()
    publish.assert_not_called()


@pytest.mark.parametrize("status_code", [409, 413, 503, 504])
def test_rich_patch_propagates_owner_failure_without_publishing(monkeypatch: MonkeyPatch, status_code: int) -> None:
    monkeypatch.setattr(SocketAuthorization, "is_subscription_authorized", lambda service, user, topic, topic_id: True)
    monkeypatch.setattr(
        EditorSyncApi,
        "_forward_to_socket",
        lambda request, path, data: JsonResponse({"message": "Not applied"}, status_code=status_code),
    )
    publish = Mock()
    monkeypatch.setattr(BaseSocketPublisher, "put_dispather", publish)

    response = request_editor_sync_rich_patch(
        create_request(),
        PatchEditorSyncRichForm(document_name="card:card-uid:description", value="New draft"),
        User.model_construct(),
        DomainService(),
    )

    assert response.status_code == status_code
    assert orjson.loads(response.body) == {"message": "Not applied"}
    publish.assert_not_called()


def test_rich_patch_owner_transport_failure_is_not_acknowledged(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(SocketAuthorization, "is_subscription_authorized", lambda service, user, topic, topic_id: True)
    monkeypatch.setattr(EditorSyncApi.requests, "post", Mock(side_effect=EditorSyncApi.requests.Timeout("Unavailable")))
    publish = Mock()
    monkeypatch.setattr(BaseSocketPublisher, "put_dispather", publish)

    response = request_editor_sync_rich_patch(
        create_request(),
        PatchEditorSyncRichForm(document_name="card:card-uid:description", value="New draft"),
        User.model_construct(),
        DomainService(),
    )

    assert response.status_code == 503
    publish.assert_not_called()


def test_rich_patch_http_route_requires_authentication_before_publishing(monkeypatch: MonkeyPatch) -> None:
    user = User.model_construct()

    def authenticate(scope, *, allow_oidc=False):
        assert allow_oidc is False
        if any(name == b"authorization" for name, _value in scope["headers"]):
            scope["auth"] = user
            return user
        return 401

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", authenticate)
    monkeypatch.setattr(SocketAuthorization, "is_subscription_authorized", lambda service, user, topic, topic_id: True)
    monkeypatch.setattr(
        EditorSyncApi,
        "_forward_to_socket",
        lambda request, path, data: JsonResponse({}),
    )
    publish = Mock()
    monkeypatch.setattr(BaseSocketPublisher, "put_dispather", publish)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    client = TestClient(app)
    body = {"document_name": "card:card-uid:description", "value": "New draft"}

    assert client.post("/editor-sync/rich/patch-request", json=body).status_code == 401
    publish.assert_not_called()

    response = client.post(
        "/editor-sync/rich/patch-request",
        json=body,
        headers={"Authorization": "Bearer access-token"},
    )

    assert response.status_code == 200
    assert response.json() == {}
    publish.assert_not_called()
