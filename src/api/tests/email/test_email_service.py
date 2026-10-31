from email.message import EmailMessage
from typing import Any
import pytest
from langboard_shared.domain.services.factory.EmailService import EmailService
from langboard_shared.Env import Env
from src.api.tests.email import test_mail_attempt_log


def test_send_template_preserves_smtp_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The direct SMTP adapter preserves sender, reply-to, TLS, and credentials."""

    sent: dict[str, Any] = {}

    async def send(message: EmailMessage, **kwargs: Any) -> None:
        sent.update(message=message, **kwargs)

    settings = {
        "MAIL_FROM": "noreply@example.test",
        "MAIL_FROM_NAME": "Langboard",
        "MAIL_USERNAME": "smtp-user",
        "MAIL_PASSWORD": "smtp-password",
        "MAIL_SERVER": "smtp.example.test",
        "MAIL_PORT": 587,
        "MAIL_STARTTLS": True,
        "MAIL_SSL_TLS": False,
        "PROJECT_NAME": "langboard",
        "PUBLIC_UI_URL": "https://board.example.test",
    }
    for name, value in settings.items():
        monkeypatch.setattr(type(Env), name, property(lambda self, value=value: value))
    monkeypatch.setattr(
        EmailService,
        "_EmailService__get_template",
        lambda self, *args: ("Subject", "<strong>Hello</strong>"),
    )
    monkeypatch.setattr("aiosmtplib.send", send)

    service = EmailService(lambda *_: None, lambda *_: None, None)  # type: ignore[arg-type]
    assert service.send_template("en", "person@example.test", "verify", {}, reply_to="reply@example.test")

    message = sent["message"]
    assert message["From"] == "Langboard <noreply@example.test>"
    assert message["To"] == "person@example.test"
    assert message["Reply-To"] == "reply@example.test"
    assert sent["hostname"] == "smtp.example.test"
    assert sent["start_tls"] is True
    assert "local_hostname" not in sent


@pytest.mark.parametrize("language", ["ko-KR", "ja-JP", "zh-CN"])
def test_missing_locale_uses_english_template(monkeypatch: pytest.MonkeyPatch, tmp_path, language: str) -> None:
    import importlib

    english = tmp_path / "en-US"
    english.mkdir()
    (english / "lang.json").write_text('{"subjects": {"verify": "Hello {recipient}"}}')
    (english / "verify_email.html").write_text("<p>Hello {recipient}</p>")
    module = importlib.import_module("langboard_shared.domain.services.factory.EmailService")
    monkeypatch.setattr(module, "get_resource_path", lambda resource, locale: tmp_path / locale)
    monkeypatch.setattr(type(Env), "PROJECT_NAME", property(lambda self: "langboard"))
    service = EmailService(lambda *_: None, lambda *_: None, None)
    assert service._EmailService__get_template(language, "verify", {"recipient": "Tester"}) == (
        "[Langboard] Hello Tester",
        "<p>Hello Tester</p>",
    )


def test_strict_template_send_does_not_claim_development_smtp_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(Env), "MAIL_FROM", property(lambda self: "noreply@example.test"))

    async def fail(message: EmailMessage, **kwargs: Any) -> None:
        raise OSError("SMTP unavailable")

    monkeypatch.setattr(type(Env), "ENVIRONMENT", property(lambda self: "development"))
    monkeypatch.setattr(EmailService, "_EmailService__get_template", lambda self, *args: ("Subject", "Body"))
    monkeypatch.setattr("aiosmtplib.send", fail)
    service = EmailService(lambda *_: None, lambda *_: None, None)  # type: ignore[arg-type]

    assert service.send_template("en-US", "person@example.test", "assigned_to_card", {}, strict=True) is False


@pytest.mark.parametrize(
    "environment,strict,expected",
    [
        ("development", False, True),
        ("development", True, False),
        ("production", False, False),
        ("production", True, False),
    ],
)
def test_send_message_native_failure_contract(monkeypatch, environment, strict, expected):
    """Legacy boolean callers keep their explicit development and strict behavior."""
    calls = []

    async def fail(message, **kwargs):
        calls.append(message)
        raise OSError("secret-password sensitive-server-content")

    monkeypatch.setattr(type(Env), "ENVIRONMENT", property(lambda self: environment))
    monkeypatch.setattr("aiosmtplib.send", fail)
    service = EmailService(lambda *_: None, lambda *_: None, None)
    message = EmailMessage()
    assert service.send_message(message, strict=strict) is expected
    assert calls == [message]


mail_audit_db = test_mail_attempt_log.mail_audit_db
pytestmark = pytest.mark.usefixtures("mail_audit_db")
