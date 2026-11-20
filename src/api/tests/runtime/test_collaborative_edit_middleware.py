import asyncio
from threading import Event
from unittest.mock import Mock
import orjson
import pytest
import requests
from langboard.middlewares.CollaborativeEditMiddleware import CollaborativeEditMiddleware
from langboard_shared.core.routing import ApiPermission, SocketTopic
from langboard_shared.core.routing.ApiSchemaHelper import ApiSchemaMap
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import User
from pytest import MonkeyPatch
from starlette.datastructures import Headers
from starlette.types import Message, Receive, Scope, Send


DOCUMENT_NAME = "card:example:title"
TEST_API_SCHEMA: ApiSchemaMap = {
    "name": "patch_card",
    "path": "/cards/example",
    "path_params": [],
    "method": "PATCH",
    "permission": ApiPermission.Edit,
    "content_type": "application/json",
    "description": "",
    "form": None,
    "query": None,
    "file_field": None,
    "request_schema_source": None,
    "collaborative_edit_targets": [
        {"document_name": DOCUMENT_NAME, "type": "text", "form_field": "title", "patch_field": "title"}
    ],
}


@pytest.mark.asyncio
async def test_editor_round_trips_do_not_block_api_authorization_callbacks(monkeypatch: MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    observed: list[str] = []

    def round_trip(stage: str) -> None:
        authorized = Event()
        loop.call_soon_threadsafe(authorized.set)
        assert authorized.wait(1), f"The {stage} blocked the API event loop"
        observed.append(stage)

    def guard(headers: Headers, request: object, schema: object) -> None:
        round_trip("guard")

    def cleanup(headers: Headers, request: object, schema: object) -> None:
        round_trip("cleanup")

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive() -> Message:
        return {"type": "http.request", "body": b'{"title":"Updated"}', "more_body": False}

    async def send(message: Message) -> None:
        pass

    middleware = CollaborativeEditMiddleware(downstream)
    monkeypatch.setattr(middleware, "_get_api_schema_by_path", lambda method, path: (TEST_API_SCHEMA, {}))
    monkeypatch.setitem(
        CollaborativeEditMiddleware.__call__.__globals__,
        "get_agent_allowed_permissions",
        lambda headers, default_read: {ApiPermission.Edit.value},
    )
    monkeypatch.setattr(middleware, "_get_collaborative_edit_guard_response", guard)
    monkeypatch.setattr(middleware, "_clear_inactive_collaborative_documents", cleanup)
    await middleware(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/cards/example",
            "query_string": b"",
            "headers": [(b"x-api-token", b"test-token")],
        },
        receive,
        send,
    )
    assert observed == ["guard", "cleanup"]


@pytest.mark.parametrize("failure", ["network", "status", "json", "shape"])
def test_active_document_lookup_failure_is_not_an_empty_result(monkeypatch: MonkeyPatch, failure: str) -> None:
    def post(*args: object, **kwargs: object) -> Mock:
        if failure == "network":
            raise requests.ConnectionError("socket unavailable")

        response = Mock()
        response.status_code = 503 if failure == "status" else 200
        if failure == "json":
            response.json.side_effect = ValueError("invalid json")
        else:
            response.json.return_value = {} if failure == "shape" else {"active_document_names": []}
        return response

    monkeypatch.setattr(requests, "post", post)
    headers = Headers(raw=[(b"x-api-token", b"test-token")])

    assert CollaborativeEditMiddleware._get_active_collaborative_document_names(headers, [DOCUMENT_NAME]) is None


def test_active_document_lookup_keeps_a_valid_empty_result(monkeypatch: MonkeyPatch) -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"active_document_names": []}
    monkeypatch.setattr(requests, "post", Mock(return_value=response))

    headers = Headers(raw=[(b"x-api-token", b"test-token")])
    assert CollaborativeEditMiddleware._get_active_collaborative_document_names(headers, [DOCUMENT_NAME]) == []


@pytest.mark.parametrize(("active_names", "expected_status"), [(None, 503), ([], 200)])
@pytest.mark.asyncio
async def test_active_lookup_controls_the_underlying_write(
    monkeypatch: MonkeyPatch, active_names: list[str] | None, expected_status: int
) -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = CollaborativeEditMiddleware(downstream)
    monkeypatch.setattr(middleware, "_get_api_schema_by_path", lambda method, path: (TEST_API_SCHEMA, {}))
    monkeypatch.setitem(
        CollaborativeEditMiddleware.__call__.__globals__,
        "get_agent_allowed_permissions",
        lambda headers, default_read: {ApiPermission.Edit.value},
    )
    monkeypatch.setattr(middleware, "_get_active_collaborative_document_names", lambda headers, names: active_names)
    monkeypatch.setattr(middleware, "_clear_inactive_collaborative_documents", lambda headers, request, schema: None)

    async def receive() -> Message:
        return {"type": "http.request", "body": b'{"title":"Updated"}', "more_body": False}

    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "method": "PATCH",
        "path": "/cards/example",
        "query_string": b"",
        "headers": [(b"x-api-token", b"test-token")],
    }

    await middleware(scope, receive, send)

    assert called is (expected_status == 200)
    assert (
        next(message["status"] for message in messages if message["type"] == "http.response.start") == expected_status
    )
    if expected_status == 503:
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        assert orjson.loads(body) == {"message": "Unable to verify active collaborative documents."}


def test_cleanup_does_not_clear_documents_when_active_lookup_fails(monkeypatch: MonkeyPatch) -> None:
    middleware = CollaborativeEditMiddleware(Mock())
    monkeypatch.setattr(
        middleware, "_get_requested_collaborative_document_names", lambda request, schema: [DOCUMENT_NAME]
    )
    monkeypatch.setattr(middleware, "_get_active_collaborative_document_names", lambda headers, names: None)
    post = Mock()
    monkeypatch.setattr(requests, "post", post)

    middleware._clear_inactive_collaborative_documents(
        Headers(raw=[(b"x-api-token", b"test-token")]),
        Mock(),
        TEST_API_SCHEMA,
    )

    post.assert_not_called()


def test_rich_patch_from_middleware_waits_for_the_document_owner(monkeypatch: MonkeyPatch) -> None:
    user = User.model_construct(activated_at=SafeDateTime.now(), deleted_at=None)
    service = Mock()
    owner_patch = Mock(return_value=None)
    authorize = Mock(return_value=(SocketTopic.BoardCard, "card-uid"))
    globals_map = CollaborativeEditMiddleware._patch_collaborative_documents.__globals__
    monkeypatch.setattr(globals_map["Auth"], "validate_user_by_api_token", lambda headers: user)
    monkeypatch.setitem(globals_map, "DomainService", lambda: service)
    monkeypatch.setitem(globals_map, "authorized_editor_document_subscription", authorize)
    monkeypatch.setattr(CollaborativeEditMiddleware, "_patch_collaborative_document", owner_patch)
    patch = {"document_name": "card:card-uid:description", "value": "New draft"}

    response = CollaborativeEditMiddleware._patch_collaborative_documents(
        Headers(raw=[(b"x-api-token", b"test-token")]), [], [patch]
    )

    assert response is None
    authorize.assert_called_once_with(service, user, patch["document_name"])
    owner_patch.assert_called_once_with("/editor-sync/rich/patch-request", patch, {"X-Api-Token": "test-token"})
    service.close.assert_called_once()


def test_rich_patch_denial_prevents_all_patches(monkeypatch: MonkeyPatch) -> None:
    user = User.model_construct(activated_at=SafeDateTime.now(), deleted_at=None)
    service = Mock()
    publish = Mock()
    globals_map = CollaborativeEditMiddleware._patch_collaborative_documents.__globals__
    monkeypatch.setattr(globals_map["Auth"], "validate_user_by_api_token", lambda headers: user)
    monkeypatch.setitem(globals_map, "DomainService", lambda: service)
    monkeypatch.setitem(
        globals_map,
        "authorized_editor_document_subscription",
        lambda service, user, name: (SocketTopic.BoardCard, "allowed") if name == "card:allowed:description" else None,
    )
    monkeypatch.setattr(CollaborativeEditMiddleware, "_patch_collaborative_document", publish)

    response = CollaborativeEditMiddleware._patch_collaborative_documents(
        Headers(raw=[(b"x-api-token", b"test-token")]),
        [],
        [
            {"document_name": "card:allowed:description", "value": "First"},
            {"document_name": "card:denied:description", "value": "Second"},
        ],
    )

    assert response == {"status": 403, "body": {"message": "Permission denied for editor sync patch."}}
    publish.assert_not_called()
    service.close.assert_called_once()


def test_rich_patch_rejects_oversized_payload_before_authentication(monkeypatch: MonkeyPatch) -> None:
    globals_map = CollaborativeEditMiddleware._patch_collaborative_documents.__globals__
    authenticate = Mock()
    monkeypatch.setattr(globals_map["Auth"], "validate_user_by_api_token", authenticate)
    monkeypatch.setitem(globals_map, "rich_patch_request_fits_limit", lambda name, value: False)

    response = CollaborativeEditMiddleware._patch_collaborative_documents(
        Headers(raw=[(b"x-api-token", b"test-token")]),
        [],
        [{"document_name": "card:card-uid:description", "value": "Too large"}],
    )

    assert response is not None and response["status"] == 413
    authenticate.assert_not_called()


@pytest.mark.parametrize("status_code", [409, 503, 504])
def test_rich_patch_owner_failure_is_not_acknowledged(monkeypatch: MonkeyPatch, status_code: int) -> None:
    user = User.model_construct(activated_at=SafeDateTime.now(), deleted_at=None)
    globals_map = CollaborativeEditMiddleware._patch_collaborative_documents.__globals__
    monkeypatch.setattr(globals_map["Auth"], "validate_user_by_api_token", lambda headers: user)
    monkeypatch.setitem(globals_map, "DomainService", Mock)
    monkeypatch.setitem(
        globals_map,
        "authorized_editor_document_subscription",
        lambda service, user, name: (SocketTopic.BoardCard, "card-uid"),
    )
    failure = {"status": status_code, "body": {"message": "Not applied"}}
    monkeypatch.setattr(CollaborativeEditMiddleware, "_patch_collaborative_document", Mock(return_value=failure))

    response = CollaborativeEditMiddleware._patch_collaborative_documents(
        Headers(raw=[(b"x-api-token", b"test-token")]),
        [],
        [{"document_name": "card:card-uid:description", "value": "New draft"}],
    )

    assert response == failure


def test_text_patch_still_uses_the_editor_owner(monkeypatch: MonkeyPatch) -> None:
    node_patch = Mock(return_value=None)
    monkeypatch.setattr(CollaborativeEditMiddleware, "_patch_collaborative_document", node_patch)
    patch = {"document_name": DOCUMENT_NAME, "field": "title", "value": "New title"}
    headers = Headers(raw=[(b"x-api-token", b"test-token")])

    response = CollaborativeEditMiddleware._patch_collaborative_documents(headers, [patch], [])

    assert response is None
    node_patch.assert_called_once_with("/editor-sync/text/patch", patch, {"X-Api-Token": "test-token"})


@pytest.mark.asyncio
async def test_active_rich_field_is_patched_without_a_regular_api_write(monkeypatch: MonkeyPatch) -> None:
    document_name = "card:card-uid:description"
    rich_schema: ApiSchemaMap = {
        **TEST_API_SCHEMA,
        "collaborative_edit_targets": [{"document_name": document_name, "type": "rich", "form_field": "description"}],
    }
    downstream = Mock()
    middleware = CollaborativeEditMiddleware(downstream)
    monkeypatch.setattr(middleware, "_get_api_schema_by_path", lambda method, path: (rich_schema, {}))
    monkeypatch.setitem(
        CollaborativeEditMiddleware.__call__.__globals__,
        "get_agent_allowed_permissions",
        lambda headers, default_read: {ApiPermission.Edit.value},
    )
    monkeypatch.setattr(middleware, "_get_active_collaborative_document_names", lambda headers, names: [document_name])
    user = User.model_construct(activated_at=SafeDateTime.now(), deleted_at=None)
    globals_map = CollaborativeEditMiddleware._patch_collaborative_documents.__globals__
    monkeypatch.setattr(globals_map["Auth"], "validate_user_by_api_token", lambda headers: user)
    monkeypatch.setitem(globals_map, "DomainService", Mock)
    monkeypatch.setitem(
        globals_map,
        "authorized_editor_document_subscription",
        lambda service, user, name: (SocketTopic.BoardCard, "card-uid"),
    )
    publish = Mock(return_value=None)
    monkeypatch.setattr(CollaborativeEditMiddleware, "_patch_collaborative_document", publish)
    scope: Scope = {
        "type": "http",
        "method": "PATCH",
        "path": "/cards/example",
        "query_string": b"",
        "headers": [(b"x-api-token", b"test-token")],
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b'{"description":{"content":"New draft"}}', "more_body": False}

    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    await middleware(scope, receive, send)

    downstream.assert_not_called()
    publish.assert_called_once_with(
        "/editor-sync/rich/patch-request",
        {"document_name": document_name, "value": "New draft"},
        {"X-Api-Token": "test-token"},
    )
    assert next(message["status"] for message in messages if message["type"] == "http.response.start") == 200
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    assert orjson.loads(body)["patched_rich_documents"] == [{"document_name": document_name, "value": "New draft"}]


@pytest.fixture
def registered_wiki_schema(monkeypatch: MonkeyPatch) -> ApiSchemaMap:
    from langboard.routes.board import BoardWikiApi
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.routing.ApiSchemaHelper import ApiSchemaHelper

    route = next(route for route in AppRouter.api.routes if route.endpoint is BoardWikiApi.change_project_wiki_details)
    schema = ApiSchemaHelper.create_schema(route)
    monkeypatch.setattr(AppRouter, "api_routes", {route.endpoint.__name__: schema})
    return schema


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "active",
        "inactive",
        "title",
        "title_active",
        "missing",
        "string",
        "bool",
        "negative",
        "large",
        "stale",
        "unknown",
        "query",
        "query_override",
        "combined",
    ],
)
async def test_registered_wiki_generation_routes_the_actual_middleware(
    monkeypatch: MonkeyPatch, registered_wiki_schema: ApiSchemaMap, case: str
) -> None:
    downstream_forms = []
    calls = []
    document_name = "wiki:wiki-uid:content:7"
    form = {"content": {"content": "Updated draft"}, "editor_generation": 7, "expected_revision": "a" * 64}
    if case == "combined":
        form["title"] = "New title"
    elif case in {"title", "title_active"}:
        form = {"title": "New title"}
    elif case in {"missing", "query"}:
        form.pop("editor_generation")
    elif case in {"string", "bool", "negative", "large", "stale"}:
        form["editor_generation"] = {"string": "7", "bool": True, "negative": -1, "large": 2147483648, "stale": 6}[case]

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        downstream_forms.append(orjson.loads((await receive())["body"]))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    def post(url, *, json, **kwargs):
        calls.append((url.rsplit("/editor-sync/", 1)[-1], json))
        response = Mock(status_code=200)
        if url.endswith("/active"):
            if case in {"stale", "unknown"}:
                response.status_code = 403 if case == "stale" else 503
            if case == "title_active":
                response.json.return_value = {"active_document_names": ["wiki:wiki-uid:title"]}
                return response
            response.json.return_value = {
                "active_document_names": [document_name]
                if document_name in json["document_names"] and case != "inactive"
                else []
            }
        return response

    monkeypatch.setattr(requests, "post", post)
    globals_map = CollaborativeEditMiddleware.__call__.__globals__
    monkeypatch.setitem(
        globals_map, "get_agent_allowed_permissions", lambda headers, default_read: {ApiPermission.Edit.value}
    )
    user = User.model_construct(activated_at=SafeDateTime.now(), deleted_at=None)
    monkeypatch.setattr(globals_map["Auth"], "validate_user_by_api_token", lambda headers: user)
    monkeypatch.setitem(globals_map, "DomainService", Mock)
    monkeypatch.setitem(
        globals_map,
        "authorized_editor_document_subscription",
        lambda service, user, name: (SocketTopic.BoardWikiPrivate, "wiki-uid") if name == document_name else None,
    )
    messages = []

    async def receive() -> Message:
        return {"type": "http.request", "body": orjson.dumps(form), "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await CollaborativeEditMiddleware(downstream)(
        {
            "type": "http",
            "method": "PUT",
            "path": "/board/project-uid/wiki/wiki-uid/details",
            "query_string": b"editor_generation=7"
            if case == "query"
            else b"editor_generation=6"
            if case == "query_override"
            else b"",
            "headers": [(b"x-api-token", b"test-token")],
        },
        receive,
        send,
    )
    response_status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    if case in {"active", "query_override"}:
        assert ("rich/patch-request", {"document_name": document_name, "value": "Updated draft"}) in calls
        assert downstream_forms == []
        response_body = orjson.loads(
            b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        )
        assert response_body["draft_updated"] is True
        assert response_status == 200
    elif case == "combined":
        assert response_status == 200
        assert downstream_forms == [{"title": "New title", "editor_generation": 7, "expected_revision": "a" * 64}]
        assert ("rich/patch-request", {"document_name": document_name, "value": "Updated draft"}) in calls
    elif case == "title_active":
        assert downstream_forms == []
        assert response_status == 200
        assert ("text/patch", {"document_name": "wiki:wiki-uid:title", "field": "title", "value": "New title"}) in calls
    elif case in {"inactive", "title"}:
        assert downstream_forms == [form]
        assert response_status == 200
        if case == "inactive":
            assert ("clear", {"document_names": [document_name]}) in calls
    else:
        assert response_status == (503 if case in {"stale", "unknown"} else 409)
        assert downstream_forms == []
        assert all(path != "rich/patch-request" for path, _ in calls)
    if case in {"title", "title_active"}:
        assert all(not any(":content" in name for name in payload.get("document_names", [])) for _, payload in calls)


@pytest.mark.parametrize("generation", [None, "7", True, -1, 2147483648, 7])
def test_wiki_target_format_uses_only_captured_body_generation(
    registered_wiki_schema: ApiSchemaMap, generation
) -> None:
    from langboard.routes.batcher.BatchForm import BatchFormRequestSchema

    request = BatchFormRequestSchema(
        path_or_api_name="/board/project-uid/wiki/wiki-uid/details",
        method="PUT",
        query={"wiki_uid": "wiki-uid", "editor_generation": "8"},
        form={"content": {"content": "draft"}, "editor_generation": generation},
    )
    names = [
        target["document_name"]
        for target in CollaborativeEditMiddleware._get_collaborative_edit_targets(request, registered_wiki_schema)
    ]
    assert [name for name in names if ":content" in name] == (
        ["wiki:wiki-uid:content:7"] if type(generation) is int and generation == 7 else []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["active", "inactive", "lookup_error", "missing_row", "legacy_active"])
async def test_registered_wiki_delete_captures_primary_namespace_once(monkeypatch: MonkeyPatch, case: str) -> None:
    from contextlib import contextmanager
    from copy import deepcopy
    from langboard.routes.board import BoardWikiApi
    from langboard_shared.core.db import DbSession
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.routing.ApiSchemaHelper import ApiSchemaHelper

    route = next(route for route in AppRouter.api.routes if route.endpoint is BoardWikiApi.delete_project_wiki)
    schema = ApiSchemaHelper.create_schema(route)
    original = deepcopy(schema)
    monkeypatch.setattr(AppRouter, "api_routes", {route.name: schema})
    document_name = "wiki:123:content:7"
    lookup_calls = []
    socket_calls = []
    deleted = []

    @contextmanager
    def primary(*, readonly):
        lookup_calls.append(readonly)
        assert readonly is False
        if case == "lookup_error":
            raise RuntimeError("primary unavailable")
        db = Mock()
        db.exec.return_value.first.return_value = None if case == "missing_row" else (7,)
        yield db

    monkeypatch.setattr(DbSession, "use", primary)
    monkeypatch.setitem(
        CollaborativeEditMiddleware.__call__.__globals__,
        "get_agent_allowed_permissions",
        lambda headers, default_read: {ApiPermission.Delete.value},
    )

    def post(url, *, json, **kwargs):
        path = url.rsplit("/editor-sync/", 1)[-1]
        socket_calls.append((path, json))
        response = Mock(status_code=200)
        if path == "active":
            name = "wiki:123:content" if case == "legacy_active" else document_name
            response.json.return_value = {
                "active_document_names": [name]
                if case in {"active", "legacy_active"} and name in json["document_names"]
                else []
            }
        return response

    monkeypatch.setattr(requests, "post", post)

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        deleted.append(True)
        # Authority may advance after deletion; cleanup must retain the captured namespace.
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive() -> Message:
        return {"type": "http.request", "body": b'{"editor_generation":999}', "more_body": False}

    messages = []

    async def send(message: Message) -> None:
        messages.append(message)

    await CollaborativeEditMiddleware(downstream)(
        {
            "type": "http",
            "method": "DELETE",
            "path": "/board/project-uid/wiki/123",
            "query_string": b"editor_generation=888",
            "headers": [(b"x-api-token", b"test-token")],
        },
        receive,
        send,
    )
    response_status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    assert lookup_calls == [False]
    assert schema == original
    if case == "inactive":
        assert response_status == 200 and deleted == [True]
        guard_names = socket_calls[0][1]["document_names"]
        cleared_names = next(payload["document_names"] for path, payload in socket_calls if path == "clear")
        assert document_name in guard_names
        assert cleared_names == guard_names
        assert not any(name.endswith((":999", ":888")) for name in cleared_names)
    else:
        assert response_status == (409 if case in {"active", "legacy_active"} else 503)
        assert deleted == []
        assert all(path != "clear" for path, _ in socket_calls)
