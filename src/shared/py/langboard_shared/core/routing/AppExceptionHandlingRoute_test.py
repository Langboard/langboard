"""Framework HTTP failures retain their status; actual endpoint failures remain errors."""

import asyncio
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi import HTTPException as FastApiHTTPException
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from langboard_shared.core.logger import Logger
from langboard_shared.core.routing import ApiErrorCode, ApiException
from langboard_shared.core.routing.AppExceptionHandlingRoute import AppExceptionHandlingRoute


class Payload(BaseModel):
    card_uids: list[str]


@pytest.mark.parametrize(
    "mode,expected_status,expected_calls,expected_errors",
    [
        ("disconnect_body", 400, 0, 0),
        ("invalid_json", 400, 0, 0),
        ("native_http", 403, 1, 0),
        ("fastapi_http", 429, 1, 0),
        ("api_error", 404, 1, 0),
        ("endpoint_failure", 500, 1, 1),
        ("disconnect_endpoint", 500, 1, 1),
    ],
)
def test_actual_asgi_route_preserves_framework_and_endpoint_failure_boundaries(
    monkeypatch, mode, expected_status, expected_calls, expected_errors
):
    app = FastAPI()
    app.router.route_class = AppExceptionHandlingRoute
    endpoint_calls = []
    logged = Mock()
    monkeypatch.setattr(Logger.main, "exception", logged)

    @app.post("/cards/available")
    def available(payload: Payload):
        endpoint_calls.append(payload)
        if mode == "native_http":
            raise HTTPException(403, "Access denied", headers={"X-Denial": "current-access"})
        if mode == "fastapi_http":
            raise FastApiHTTPException(429, "Try later", headers={"Retry-After": "1"})
        if mode == "api_error":
            error = ApiException.NotFound_404(ApiErrorCode.NF2003)
            error.headers = {"Cache-Control": "no-store"}
            raise error
        if mode == "disconnect_endpoint":
            # A disconnect during execution may leave an unknown write outcome.
            raise ClientDisconnect()
        raise RuntimeError("Unexpected endpoint failure")

    async def run():
        received = False
        messages = []

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {
                "type": "http.request",
                "body": b'{"card_uids":[' if mode in {"disconnect_body", "invalid_json"} else b'{"card_uids":[]}',
                "more_body": mode == "disconnect_body",
            }

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/cards/available",
                "raw_path": b"/cards/available",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "server": ("testserver", 443),
                "client": ("127.0.0.1", 1234),
                "root_path": "",
            },
            receive,
            send,
        )
        return next(message for message in messages if message["type"] == "http.response.start")

    response = asyncio.run(run())
    assert response["status"] == expected_status
    assert len(endpoint_calls) == expected_calls
    assert logged.call_count == expected_errors
    if mode == "native_http":
        assert (b"x-denial", b"current-access") in response["headers"]
    if mode == "fastapi_http":
        assert (b"retry-after", b"1") in response["headers"]

    if mode == "api_error":
        assert (b"cache-control", b"no-store") in response["headers"]
