# ruff: noqa: F811
"""Manual upload routes keep storage waits off the API event loop."""

import asyncio
import json
from contextvars import ContextVar
from inspect import signature
from tempfile import SpooledTemporaryFile
from threading import Event, get_ident
import httpx
import pytest
from fastapi import FastAPI, UploadFile
from langboard.apps import AppInstallationRequests as requests
from langboard.routes.settings import AppPackageSettingsApi as routes
from langboard_shared.core.routing import ApiErrorCode, ApiException
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict, AppRegistryDenied
from test_app_installation_requests import setup_requests  # noqa: F401
from test_app_package import bundle


@pytest.mark.parametrize("board", ["sqlite-http"], indirect=True)
@pytest.mark.parametrize("kind", ["admin", "requester"])
async def test_storage_wait_keeps_real_upload_route_and_heartbeat_responsive(setup_requests, monkeypatch, kind):
    actor, admin, _ = setup_requests
    user = admin if kind == "admin" else actor
    entered, release = Event(), Event()
    context = ContextVar("upload_request_context")
    token = context.set("request-context")
    loop_thread = get_ident()
    storage_threads = []
    storage_contexts = []
    storage_calls = []

    def upload(body, storage_name, filename, original_filename):
        storage_threads.append(get_ident())
        storage_contexts.append(context.get(None))
        storage_calls.append(body.read())
        entered.set()
        # Bound the reproduction even if the route blocks the event loop.
        release.wait(1)
        return FileModel(
            storage_type="local",
            storage_name=storage_name,
            original_filename=original_filename,
            filename=filename,
            path="test-package.zip",
        )

    monkeypatch.setattr(Storage, "upload_named", upload)
    app = FastAPI()
    endpoint = routes.upload_package if kind == "admin" else routes.request_manual_package
    app.add_api_route("/upload", endpoint, methods=["POST"])
    app.dependency_overrides[signature(endpoint).parameters["user"].default.dependency] = lambda: user

    @app.get("/heartbeat")
    async def heartbeat():
        return {"alive": True}

    body = bundle()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            task = asyncio.create_task(
                client.post("/upload", files={"file": ("app.zip", body)}, data={"request_key": "async-upload"})
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2), "Upload did not reach storage"
                pulse = await asyncio.wait_for(client.get("/heartbeat"), timeout=0.5)
                assert pulse.json() == {"alive": True}
                assert not task.done(), "Storage wait blocked the API event loop until upload finished"
                assert len(storage_threads) == 1 and storage_threads[0] != loop_thread
                assert storage_contexts == ["request-context"]
            finally:
                release.set()
                response = await task
            assert response.status_code == 200, response.text
            result = response.json()
            assert set(result) == ({"app"} if kind == "admin" else {"request"})
            assert storage_calls == [body]
            if kind == "requester":
                assert response.headers["cache-control"] == "no-store"
                replay = await client.post(
                    "/upload", files={"file": ("app.zip", body)}, data={"request_key": "async-upload"}
                )
                assert replay.json() == result
                assert storage_calls == [body]
    finally:
        context.reset(token)


@pytest.mark.parametrize("kind", ["admin", "requester"])
async def test_upload_passes_only_bounded_native_file_and_preserves_response(monkeypatch, kind):
    monkeypatch.setattr(routes, "MAX_PACKAGE_BYTES", 8)
    actor, calls = object(), []

    def owner(*args, **kwargs):
        calls.append((args, kwargs))
        return {"revision": "unchanged"}

    monkeypatch.setattr(
        routes if kind == "admin" else requests, "install_package" if kind == "admin" else "submit_request", owner
    )
    with SpooledTemporaryFile(max_size=1024) as stream:
        stream.write(b"0123456789abcdef")
        stream.seek(0)
        file = UploadFile(stream, filename="app.zip")
        if kind == "admin":
            response = await routes.upload_package(file, "a" * 64, actor)
            assert calls == [((actor, b"012345678", "a" * 64), {})]
        else:
            response = await routes.request_manual_package(file, "bounded-upload", "a" * 64, "project0001", actor)
            assert calls == [
                (
                    (actor, "bounded-upload"),
                    {"body": b"012345678", "expected_revision": "a" * 64, "project_uid": "project0001"},
                )
            ]
        assert stream.tell() == 9
        key = "app" if kind == "admin" else "request"
        assert json.loads(response.body) == {key: {"revision": "unchanged"}}


@pytest.mark.parametrize("kind", ["admin", "requester"])
@pytest.mark.parametrize(
    "error,status,code",
    [
        (ValueError, 400, ApiErrorCode.VA0000),
        (AppRegistryDenied, 403, ApiErrorCode.PE1001),
        (AppRegistryConflict, 409, ApiErrorCode.EX3004),
    ],
)
async def test_upload_owner_errors_keep_safe_http_response(monkeypatch, kind, error, status, code):
    def owner(*args, **kwargs):
        raise error("private storage and database details")

    monkeypatch.setattr(
        routes if kind == "admin" else requests, "install_package" if kind == "admin" else "submit_request", owner
    )
    app = FastAPI()
    endpoint = routes.upload_package if kind == "admin" else routes.request_manual_package
    app.add_api_route("/upload", endpoint, methods=["POST"])
    app.dependency_overrides[signature(endpoint).parameters["user"].default.dependency] = lambda: object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/upload", files={"file": ("app.zip", b"zip")}, data={"request_key": "error-upload"}
        )
    assert response.status_code == status
    assert response.json() == {"detail": code.to_dict()}
    assert "private" not in response.text


async def test_request_governance_denial_keeps_forbidden_conversion(monkeypatch):
    def owner(*args, **kwargs):
        raise AppGovernanceDenied("private governance details")

    monkeypatch.setattr(requests, "submit_request", owner)
    with SpooledTemporaryFile() as stream:
        with pytest.raises(ApiException.Forbidden_403) as caught:
            await routes.request_manual_package(UploadFile(stream), "denied-upload", None, None, object())
    assert caught.value.detail == ApiErrorCode.PE1001.to_dict()


async def test_invalid_admin_revision_rejected_before_file_read():
    with SpooledTemporaryFile() as stream:
        stream.write(b"unchanged")
        stream.seek(0)
        with pytest.raises(ApiException.BadRequest_400):
            await routes.upload_package(UploadFile(stream), "invalid", object())
        assert stream.tell() == 0
