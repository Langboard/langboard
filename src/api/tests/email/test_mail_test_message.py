"""Explicit SMTP test submission: native loopback transport and administrator boundary."""

import asyncio
import importlib
import json
import ssl
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.settings import MailSettingsApi
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import User
from langboard_shared.domain.services.factory.EmailService import EmailService
from langboard_shared.helpers import MiddlewareHelper
from src.api.tests.email import test_mail_attempt_log, test_mail_diagnostics


settings = test_mail_diagnostics.settings
tls_context = test_mail_diagnostics.tls_context


@pytest.fixture(autouse=True)
def local_smtp_hostname(monkeypatch):
    # Keep machine DNS outside loopback tests while exercising the native default.
    monkeypatch.setattr("socket.getfqdn", lambda: "smtp-client.example.test")


async def submit(settings, *, reject=None, stall=None, tls=None, implicit=False, auth=False, app=None):
    commands, messages, tasks = [], [], set()

    async def handle(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            writer.write(b"220 local disposable SMTP\r\n")
            await writer.drain()
            while line := await reader.readline():
                command = line.split()[0].decode()
                commands.append(command)
                if command in ("EHLO", "HELO"):
                    writer.write(b"250-localhost\r\n250-STARTTLS\r\n250 AUTH PLAIN\r\n")
                elif command == "STARTTLS":
                    writer.write(b"220 ready\r\n")
                    await writer.drain()
                    await writer.start_tls(tls)
                    continue
                elif command == "AUTH":
                    writer.write(
                        b"535 secret-password sensitive-server-content\r\n" if reject == "auth" else b"235 OK\r\n"
                    )
                elif command == "MAIL":
                    writer.write(b"250 OK\r\n")
                elif command == "RCPT":
                    writer.write(
                        b"550 secret-password sensitive-server-content\r\n" if reject == "rcpt" else b"250 OK\r\n"
                    )
                elif command == "DATA":
                    writer.write(b"354 send message\r\n")
                    await writer.drain()
                    data = b""
                    while (line := await reader.readline()) != b".\r\n":
                        if not line:
                            return
                        data += line
                    messages.append(data)
                    if stall == "data":
                        await reader.read()
                        return
                    writer.write(b"554 sensitive-server-content\r\n" if reject == "data" else b"250 accepted\r\n")
                elif command == "QUIT":
                    writer.write(b"221 bye\r\n")
                    await writer.drain()
                    return
                elif command == "RSET":
                    writer.write(b"250 OK\r\n")
                else:
                    writer.write(b"500 unsupported\r\n")
                await writer.drain()
        except (ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=tls if implicit else None)
    settings(
        MAIL_PORT=server.sockets[0].getsockname()[1],
        MAIL_STARTTLS=bool(tls and not implicit),
        MAIL_SSL_TLS=implicit,
        MAIL_USERNAME="secret-user" if auth else "",
        MAIL_PASSWORD="secret-password" if auth else "",
    )
    try:
        if app is None:
            result = await EmailService.send_test_message("recipient@example.com")
        else:
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
                response = await http.post("/settings/mail/test-message", json={"recipient": "recipient@example.com"})
            assert response.status_code == 200
            result = response.json()
    finally:
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    assert result["checked_at"]
    assert result["inbox_delivery"] == "not_checked"
    assert all(
        secret not in json.dumps(result) for secret in ("secret-user", "secret-password", "sensitive-server-content")
    )
    assert commands.count("DATA") <= 1
    rows = test_mail_attempt_log.attempts(test_mail_attempt_log.DbEngine.get_main_engine())
    assert len(rows) == 1
    row = rows[0]
    assert (
        row.outcome
        == {"accepted": "accepted", "failed": "rejected", "unknown": "unknown"}[result["smtp_send_acceptance"]]
    )
    assert row.safe_error_code == result["code"]
    assert row.completed_at is not None and row.started_at <= row.completed_at
    assert row.subject == "Langboard SMTP test message" and row.trigger == "smtp_test"
    assert row.recipient_masked == "***@***"
    stored = json.dumps({key: str(getattr(row, key)) for key in row.model_fields})
    assert all(secret not in stored for secret in ("secret-user", "secret-password", "sensitive-server-content"))
    return result, commands, messages


@pytest.mark.asyncio
async def test_actual_native_acceptance(settings):
    result, commands, messages = await submit(settings)
    assert result["smtp_send_acceptance"] == "accepted"
    assert result["code"] is None
    assert commands.count("MAIL") == commands.count("RCPT") == commands.count("DATA") == 1
    message = BytesParser(policy=policy.default).parsebytes(messages[0])
    assert message["To"] == "recipient@example.com"
    assert message["Subject"] == "Langboard SMTP test message"
    assert "No action is required" in message.get_content()


@pytest.mark.asyncio
@pytest.mark.parametrize("reject", ["rcpt", "data", "auth"])
async def test_actual_rejection(settings, reject):
    result, commands, _ = await submit(settings, reject=reject, auth=reject == "auth")
    assert result["smtp_send_acceptance"] == "failed"
    assert result["code"] == ("authentication_failed" if reject == "auth" else "smtp_rejected")
    if reject == "auth":
        assert "DATA" not in commands


@pytest.mark.asyncio
async def test_lost_response_after_submission_unknown_no_retry(settings):
    result, commands, messages = await submit(settings, stall="data")
    assert result["smtp_send_acceptance"] == "unknown"
    assert result["code"] == "timeout"
    assert commands.count("DATA") == len(messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("trusted", [False, True])
async def test_actual_native_tls(settings, tls_context, monkeypatch, implicit, trusted):
    context, cert = tls_context
    if trusted:
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    result, commands, messages = await submit(settings, tls=context, implicit=implicit, auth=trusted)
    assert result["smtp_send_acceptance"] == ("accepted" if trusted else "failed")
    if trusted:
        assert "AUTH" in commands
        assert len(messages) == 1
    else:
        assert not messages
        assert result["code"] == "tls_failed"


@pytest.fixture
def client(monkeypatch, request):
    admin = getattr(request, "param", True)
    actor = 401 if admin is None else User.model_construct(id=1, is_admin=admin)

    def validate(scope, **kwargs):
        scope["auth"] = actor
        return actor

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    module = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")
    monkeypatch.setattr(module, "DomainService", lambda: SimpleNamespace(close=lambda: None))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as http:
        yield http


@pytest.mark.parametrize("client,status", [(None, 401), (False, 403), (True, 200)], indirect=["client"])
def test_native_admin_authorization(client, status, monkeypatch):
    calls = []

    async def send(recipient):
        calls.append(recipient)
        return {"smtp_send_acceptance": "accepted", "inbox_delivery": "not_checked", "code": None}

    monkeypatch.setattr(EmailService, "send_test_message", send)
    assert any(route.endpoint is MailSettingsApi.send_mail_test_message for route in AppRouter.api.routes)
    response = client.post("/settings/mail/test-message", json={"recipient": "recipient@example.com"})
    assert response.status_code == status
    assert calls == (["recipient@example.com"] if status == 200 else [])


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"recipient": "invalid"},
        {"recipient": ["a@example.com"]},
        {"recipient": 123},
        {"recipient": "a@example.com,b@example.com"},
        {"recipient": "A <a@example.com>"},
        {"recipient": "a@example.com\r\nBcc: b@example.com"},
        {"recipient": "a@example.com\x00"},
        {"recipient": "a@example.com", "subject": "arbitrary"},
        {"recipient": "a@example.com", "host": "arbitrary"},
    ],
)
def test_invalid_request_cannot_send(client, monkeypatch, body):
    async def forbidden(*args):
        pytest.fail("invalid request reached transport")

    monkeypatch.setattr(EmailService, "send_test_message", forbidden)
    assert client.post("/settings/mail/test-message", json=body).status_code == 400


@pytest.mark.asyncio
async def test_invalid_configuration_never_submits(settings, monkeypatch):
    settings(MAIL_STARTTLS=True, MAIL_SSL_TLS=True)

    async def forbidden(*args):
        pytest.fail("invalid configuration reached transport")

    monkeypatch.setattr(EmailService, "_send_native", forbidden)
    result = await EmailService.send_test_message("recipient@example.com")
    assert result["smtp_send_acceptance"] == "failed"
    assert result["code"] == "configuration_invalid"


@pytest.mark.asyncio
async def test_admin_http_request_reaches_native_loopback(settings, monkeypatch):
    def validate(scope, **kwargs):
        actor = User.model_construct(id=1, is_admin=True)
        scope["auth"] = actor
        return actor

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    module = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")
    monkeypatch.setattr(module, "DomainService", lambda: SimpleNamespace(close=lambda: None))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    result, commands, messages = await submit(settings, app=app)
    assert result["smtp_send_acceptance"] == "accepted"
    assert commands.count("DATA") == len(messages) == 1


@pytest.mark.asyncio
async def test_unexpected_value_error_during_send_stays_unknown(settings, monkeypatch):
    calls = []

    async def fail(message):
        calls.append(message)
        raise ValueError("secret-password sensitive-server-content")

    monkeypatch.setattr(EmailService, "_send_native", fail)
    result = await EmailService.send_test_message("recipient@example.com")
    assert result["smtp_send_acceptance"] == "unknown"
    assert result["code"] == "transport_error"
    assert len(calls) == 1
    assert "secret" not in json.dumps(result)


mail_audit_db = test_mail_attempt_log.mail_audit_db
pytestmark = pytest.mark.usefixtures("mail_audit_db")
