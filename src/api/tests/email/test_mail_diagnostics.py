import asyncio
import importlib
import json
import socket
import ssl
import subprocess
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.settings import MailSettingsApi
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import User
from langboard_shared.domain.services.factory.EmailService import EmailService
from langboard_shared.Env import Env
from langboard_shared.helpers import MiddlewareHelper


@pytest.fixture
def settings(monkeypatch):
    def configure(**overrides):
        values = dict(
            MAIL_SERVER="127.0.0.1",
            MAIL_PORT=25,
            MAIL_STARTTLS=False,
            MAIL_SSL_TLS=False,
            MAIL_FROM="sender@example.test",
            MAIL_USERNAME="",
            MAIL_PASSWORD="",
        )
        values.update(overrides)
        for name, value in values.items():
            monkeypatch.setattr(type(Env), name, property(lambda self, value=value: value))

    configure()
    return configure


@pytest.fixture
def tls_context(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context, cert


async def probe_server(settings, *, tls=None, implicit=False, auth=False, reject_auth=False, stall=False):
    commands = []
    tasks = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            if stall in ("greeting", "implicit_tls"):
                await reader.read()
                return
            writer.write(b"220 local disposable SMTP\r\n")
            await writer.drain()
            while line := await reader.readline():
                command = line.split()[0].decode()
                commands.append(command)
                if command in ("EHLO", "HELO"):
                    writer.write(b"250-localhost\r\n250-STARTTLS\r\n250 AUTH PLAIN\r\n")
                elif command == "STARTTLS":
                    if stall == "tls":
                        await reader.read()
                        return
                    if tls is None:
                        writer.write(b"454 sensitive-server-content\r\n")
                    else:
                        writer.write(b"220 ready\r\n")
                        await writer.drain()
                        await writer.start_tls(tls)
                        continue
                elif command == "AUTH":
                    if stall == "auth":
                        await reader.read()
                        return
                    writer.write(b"535 sensitive-server-content\r\n" if reject_auth else b"235 authenticated\r\n")
                else:
                    writer.write(b"500 forbidden\r\n")
                await writer.drain()
        except (ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=tls if implicit else None)
    settings(
        MAIL_PORT=server.sockets[0].getsockname()[1],
        MAIL_STARTTLS=bool((tls and not implicit) or stall == "tls"),
        MAIL_SSL_TLS=implicit or stall == "implicit_tls",
        MAIL_USERNAME="secret-user" if auth else "",
        MAIL_PASSWORD="secret-password" if auth else "",
    )
    try:
        result = await EmailService.diagnose_connection()
    finally:
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    assert not {"MAIL", "RCPT", "DATA"}.intersection(commands)
    serialized = json.dumps(result)
    assert all(value not in serialized for value in ("secret-user", "secret-password", "sensitive-server-content"))
    return result, commands


@pytest.mark.asyncio
async def test_local_connection_does_not_send(settings):
    result, commands = await probe_server(settings)
    assert result["success"] is True
    assert result["stages"]["tcp"]["status"] == "passed"
    assert result["stages"]["auth"]["status"] == "skipped"
    assert result["smtp_send_acceptance"] == result["inbox_delivery"] == "not_checked"
    assert result["checked_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reject", [False, True])
async def test_actual_local_authentication(settings, reject):
    result, commands = await probe_server(settings, auth=True, reject_auth=reject)
    assert "AUTH" in commands
    assert result["success"] is not reject
    assert (
        result["stages"]["auth"] == {"status": "failed", "code": "authentication_failed"}
        if reject
        else result["stages"]["auth"]["status"] == "passed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("trusted", [False, True])
async def test_actual_local_tls(settings, tls_context, monkeypatch, implicit, trusted):
    context, cert = tls_context
    if trusted:
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    result, _ = await probe_server(settings, tls=context, implicit=implicit)
    assert result["success"] is trusted
    assert result["stages"]["tls"]["status"] == ("passed" if trusted else "failed")
    if not trusted:
        assert result["stages"]["tls"]["code"] == "tls_failed"


@pytest.mark.asyncio
async def test_dns_failure_is_safe(settings, monkeypatch):
    async def fail(*args, **kwargs):
        raise socket.gaierror("sensitive-server-content")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fail)
    result = await EmailService.diagnose_connection()
    assert result["stages"]["dns"] == {"status": "failed", "code": "dns_failed"}
    assert "sensitive" not in json.dumps(result)


@pytest.mark.asyncio
async def test_tcp_refusal(settings):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings(MAIL_PORT=port)
    result = await EmailService.diagnose_connection()
    assert result["stages"]["tcp"] == {"status": "failed", "code": "connection_failed"}


@pytest.mark.asyncio
async def test_bounded_stalled_server(settings):
    result, _ = await probe_server(settings, stall="greeting")
    assert result["stages"]["tcp"] == {"status": "failed", "code": "timeout"}


@pytest.mark.parametrize("admin", [None, False, True])
@pytest.mark.parametrize(
    "method,path", [("GET", "/settings/mail/configuration"), ("POST", "/settings/mail/diagnostics")]
)
def test_native_admin_middleware(settings, monkeypatch, admin, method, path):
    calls = []
    actor = 401 if admin is None else User.model_construct(id=1, is_admin=admin)

    def validate(scope, **kwargs):
        scope["auth"] = actor
        return actor

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    module = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")
    monkeypatch.setattr(module, "DomainService", lambda: SimpleNamespace(close=lambda: None))

    async def diagnose():
        calls.append("probe")
        return {"success": True}

    monkeypatch.setattr(EmailService, "diagnose_connection", diagnose)
    assert any(route.endpoint is MailSettingsApi.diagnose_mail_connection for route in AppRouter.api.routes)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == (401 if admin is None else 200 if admin else 403)
    assert calls == (["probe"] if admin and method == "POST" else [])


def test_effective_configuration_hides_credentials(settings):
    settings(MAIL_USERNAME="secret-user", MAIL_PASSWORD="secret-password")
    data = EmailService.effective_smtp_config()
    assert data["username_configured"] and data["password_configured"]
    assert "secret" not in json.dumps(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["tls", "auth"])
async def test_stage_timeout_is_bounded(settings, stage):
    result, _ = await probe_server(settings, stall=stage, auth=stage == "auth")
    assert result["stages"][stage] == {"status": "failed", "code": "timeout"}
    assert result["success"] is False


@pytest.mark.asyncio
async def test_conflicting_tls_configuration(settings):
    settings(MAIL_STARTTLS=True, MAIL_SSL_TLS=True)
    result = await EmailService.diagnose_connection()
    assert result["stages"]["dns"] == {"status": "failed", "code": "configuration_invalid"}
    assert result["success"] is False


@pytest.mark.asyncio
async def test_implicit_tls_handshake_timeout_is_tls(settings):
    result, _ = await probe_server(settings, stall="implicit_tls")
    assert result["stages"]["tcp"]["status"] == "passed"
    assert result["stages"]["tls"] == {"status": "failed", "code": "timeout"}
