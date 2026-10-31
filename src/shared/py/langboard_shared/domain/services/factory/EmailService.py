import asyncio
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from json import loads as json_loads
from typing import get_args
import aiosmtplib
from ....core.domain import BaseDomainService
from ....core.resources import get_resource_path
from ....core.resources.locales.EmailTemplateNames import TEmailTemplateName
from ....Env import Env
from ..MailDeliveryAttemptLog import MailAuditUnavailable, MailDeliveryAttemptLog


class EmailService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "email"

    @staticmethod
    def effective_smtp_config() -> dict:
        """Expose operator-owned configuration without credential values."""
        return {
            "server": Env.MAIL_SERVER,
            "port": Env.MAIL_PORT,
            "starttls": Env.MAIL_STARTTLS,
            "ssl_tls": Env.MAIL_SSL_TLS,
            "sender": Env.MAIL_FROM,
            "username_configured": bool(Env.MAIL_USERNAME),
            "password_configured": bool(Env.MAIL_PASSWORD),
            "authentication": "password" if Env.MAIL_USERNAME else "none",
            "source": "environment",
        }

    @staticmethod
    async def diagnose_connection() -> dict:
        """Check the configured transport only; never issue MAIL, RCPT or DATA.

        One deadline bounds the entire probe. Server responses and exception text
        are deliberately excluded from the diagnostic contract.
        """
        stages = {name: {"status": "not_checked", "code": None} for name in ("dns", "tcp", "tls", "auth")}
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "stages": stages,
            "smtp_send_acceptance": "not_checked",
            "inbox_delivery": "not_checked",
        }
        stage = "dns"
        smtp = None
        try:
            if not Env.MAIL_SERVER or not 1 <= Env.MAIL_PORT <= 65535 or (Env.MAIL_STARTTLS and Env.MAIL_SSL_TLS):
                stages[stage] = {"status": "failed", "code": "configuration_invalid"}
                return result
            async with asyncio.timeout(5):
                loop = asyncio.get_running_loop()
                await loop.getaddrinfo(Env.MAIL_SERVER, Env.MAIL_PORT, type=socket.SOCK_STREAM)
                stages["dns"]["status"] = "passed"
                stage = "tcp"
                # Separate the TCP preflight from implicit TLS so a stalled TLS
                # handshake cannot be mislabeled as a TCP timeout. The SMTP
                # session itself remains entirely owned by aiosmtplib.
                _, writer = await asyncio.open_connection(Env.MAIL_SERVER, Env.MAIL_PORT)
                writer.close()
                await writer.wait_closed()
                stages["tcp"]["status"] = "passed"
                if Env.MAIL_SSL_TLS:
                    stage = "tls"
                smtp = aiosmtplib.SMTP(
                    hostname=Env.MAIL_SERVER,
                    port=Env.MAIL_PORT,
                    use_tls=Env.MAIL_SSL_TLS,
                    start_tls=False,
                    local_hostname="localhost",
                    timeout=5,
                )
                await smtp.connect()
                stages["tcp"]["status"] = "passed"
                stage = "tls"
                if Env.MAIL_STARTTLS:
                    await smtp.starttls()
                stages["tls"]["status"] = "passed" if Env.MAIL_SSL_TLS or Env.MAIL_STARTTLS else "skipped"
                stage = "auth"
                if Env.MAIL_USERNAME:
                    await smtp.login(Env.MAIL_USERNAME, Env.MAIL_PASSWORD or "")
                    stages["auth"]["status"] = "passed"
                else:
                    stages["auth"]["status"] = "skipped"
                result["success"] = True
        except Exception as error:
            causes = []
            cause = error
            while cause is not None and len(causes) < 8:
                causes.append(cause)
                cause = cause.__cause__ or cause.__context__
            if any(isinstance(item, socket.gaierror) for item in causes):
                stage, code = "dns", "dns_failed"
            elif any(isinstance(item, ssl.SSLError) for item in causes):
                stage, code = "tls", "tls_failed"
                stages["tcp"]["status"] = "passed"
            elif isinstance(error, (TimeoutError, aiosmtplib.errors.SMTPTimeoutError)):
                code = "timeout"
            elif isinstance(error, aiosmtplib.errors.SMTPAuthenticationError):
                code = "authentication_failed"
            else:
                code = {
                    "dns": "dns_failed",
                    "tcp": "connection_failed",
                    "tls": "tls_failed",
                    "auth": "authentication_failed",
                }[stage]
            stages[stage] = {"status": "failed", "code": code}
        finally:
            if smtp is not None:
                smtp.close()
        return result

    def send_template(
        self,
        lang: str,
        to: str,
        template_name: TEmailTemplateName,
        formats: dict[str, str],
        *,
        reply_to: str | None = None,
        strict: bool = False,
    ) -> bool:
        """Render and send one localized template through the configured SMTP transport."""

        message = self.prepare_template_message(lang, to, template_name, formats, reply_to=reply_to)
        return self.send_message(message, strict=strict)

    def prepare_template_message(
        self,
        lang: str,
        to: str,
        template_name: TEmailTemplateName,
        formats: dict[str, str],
        *,
        reply_to: str | None = None,
    ) -> EmailMessage:
        subject, template = self.__get_template(
            lang,
            template_name,
            {
                **formats,
                "app_name": Env.PROJECT_NAME.capitalize(),
                "logo_url": f"{Env.PUBLIC_UI_URL}/images/logo.png",
            },
        )

        message = EmailMessage()
        message["From"] = formataddr((Env.MAIL_FROM_NAME, Env.MAIL_FROM))
        message["To"] = to
        message["Subject"] = subject
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(template, subtype="html")
        if template_name in get_args(TEmailTemplateName):
            # Historical subject is an owned label, never formatted user input.
            message._langboard_attempt_metadata = {
                "template_name": template_name,
                "trigger": template_name,
                "subject": f"Langboard: {template_name.replace('_', ' ')}",
            }
        return message

    @staticmethod
    def attach_attempt_metadata(
        message: EmailMessage, *, source: str, source_uid: str, linked_ids: dict | None = None
    ) -> None:
        """Attach bounded native provenance without transmitting extra headers."""
        from ....core.types import SnowflakeID

        if source not in {"notification", "project_activity"}:
            raise ValueError("Invalid mail attempt source")
        if (
            not isinstance(source_uid, str)
            or len(source_uid) != 11
            or not source_uid.isascii()
            or not source_uid.isalnum()
            or SnowflakeID.from_short_code(source_uid) <= 0
        ):
            raise ValueError("Invalid mail attempt source UID")
        allowed = {"notification_id", "project_activity_id", "project_id", "receiver_id", "user_id", "card_id"}
        safe_ids = {
            key: value
            for key, value in (linked_ids or {}).items()
            if key in allowed and type(value) is int and 0 < value <= SnowflakeID.MAX_VALUE
        }
        metadata = dict(getattr(message, "_langboard_attempt_metadata", {}))
        metadata.update(source=source, source_uid=source_uid, linked_ids=safe_ids or None)
        message._langboard_attempt_metadata = metadata

    @staticmethod
    def _attempt_error(error: BaseException) -> tuple[str, str]:
        if isinstance(error, aiosmtplib.errors.SMTPAuthenticationError):
            return "rejected", "authentication_failed"
        if isinstance(
            error,
            (
                aiosmtplib.errors.SMTPDataError,
                aiosmtplib.errors.SMTPRecipientsRefused,
                aiosmtplib.errors.SMTPSenderRefused,
                aiosmtplib.errors.SMTPHeloError,
            ),
        ):
            return "rejected", "smtp_rejected"
        if isinstance(error, ssl.SSLError):
            return "rejected", "tls_failed"
        if isinstance(error, aiosmtplib.errors.SMTPConnectError):
            cause = error
            for _ in range(8):
                if isinstance(cause, ssl.SSLError):
                    return "rejected", "tls_failed"
                cause = cause.__cause__ or cause.__context__
                if cause is None:
                    break
            return "rejected", "connection_failed"
        if isinstance(error, (TimeoutError, aiosmtplib.errors.SMTPTimeoutError)):
            return "unknown", "timeout"
        return "unknown", "transport_error"

    @staticmethod
    async def _send_native(message: EmailMessage):
        """Commit start before the single native invocation; never retry it.

        Missing audit storage prevents sending. Completion write failure leaves
        the started row unknown and preserves the authoritative SMTP result.
        """
        row = MailDeliveryAttemptLog.start(message)
        outcome, code = "unknown", "transport_error"
        try:
            result = await aiosmtplib.send(
                message,
                hostname=Env.MAIL_SERVER,
                port=int(Env.MAIL_PORT),
                username=Env.MAIL_USERNAME or None,
                password=Env.MAIL_PASSWORD or None,
                start_tls=Env.MAIL_STARTTLS,
                use_tls=Env.MAIL_SSL_TLS,
                timeout=5,
            )
            outcome, code = ("rejected", "smtp_rejected") if result and result[0] else ("accepted", None)
            return result
        except BaseException as error:
            outcome, code = EmailService._attempt_error(error)
            raise
        finally:
            try:
                MailDeliveryAttemptLog.finish(row, outcome, code)
            except Exception:
                # A second send would risk duplicating an accepted message.
                pass

    def send_message(self, message: EmailMessage, *, strict: bool = False) -> bool:
        try:
            asyncio.run(self._send_native(message))
        except Exception:
            if Env.ENVIRONMENT == "development" and not strict:
                return True
            return False
        return True

    @staticmethod
    async def send_test_message(recipient: str) -> dict:
        """Submit exactly once; SMTP acceptance never proves inbox delivery.

        Native send includes session cleanup. A lost response (including during
        cleanup) cannot establish rejection, so uncertain results must not retry.
        """
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "smtp_send_acceptance": "failed",
            "inbox_delivery": "not_checked",
            "code": None,
        }
        submission_started = False
        try:
            if (
                not Env.MAIL_SERVER
                or not Env.MAIL_FROM
                or not 1 <= int(Env.MAIL_PORT) <= 65535
                or (Env.MAIL_STARTTLS and Env.MAIL_SSL_TLS)
            ):
                result["code"] = "configuration_invalid"
                return result
            message = EmailMessage()
            message["From"] = formataddr((Env.MAIL_FROM_NAME, Env.MAIL_FROM))
            message["To"] = recipient
            message["Subject"] = "Langboard SMTP test message"
            message.set_content(
                "This test message was explicitly requested by a Langboard administrator. "
                "No action is required. SMTP acceptance does not confirm inbox delivery."
            )
            message._langboard_attempt_metadata = {"trigger": "smtp_test", "subject": "Langboard SMTP test message"}
            # One total deadline also bounds multi-stage SMTP stalls.
            submission_started = True
            async with asyncio.timeout(15):
                refused, _ = await EmailService._send_native(message)
            result["smtp_send_acceptance"] = "failed" if refused else "accepted"
            result["code"] = "smtp_rejected" if refused else None
        except MailAuditUnavailable:
            result["code"] = "audit_storage_unavailable"
        except aiosmtplib.errors.SMTPAuthenticationError:
            result["code"] = "authentication_failed"
        except (
            aiosmtplib.errors.SMTPDataError,
            aiosmtplib.errors.SMTPRecipientsRefused,
            aiosmtplib.errors.SMTPSenderRefused,
            aiosmtplib.errors.SMTPHeloError,
        ):
            result["code"] = "smtp_rejected"
        except ssl.SSLError:
            result["code"] = "tls_failed"
        except aiosmtplib.errors.SMTPConnectError as error:
            cause = error
            tls_failure = False
            for _ in range(8):
                if isinstance(cause, ssl.SSLError):
                    tls_failure = True
                    break
                cause = cause.__cause__ or cause.__context__
                if cause is None:
                    break
            result["code"] = "tls_failed" if tls_failure else "connection_failed"
        except ValueError:
            result["smtp_send_acceptance"] = "unknown" if submission_started else "failed"
            result["code"] = "transport_error" if submission_started else "configuration_invalid"
        except Exception as error:
            result["smtp_send_acceptance"] = "unknown"
            result["code"] = (
                "timeout"
                if isinstance(error, (TimeoutError, aiosmtplib.errors.SMTPTimeoutError))
                else "transport_error"
            )
        return result

    def __get_template(self, lang: str, template_name: TEmailTemplateName, formats: dict[str, str]) -> tuple[str, str]:
        locale_path = get_resource_path("locales", lang)
        if not (locale_path / "lang.json").is_file() or not (locale_path / f"{template_name}_email.html").is_file():
            locale_path = get_resource_path("locales", "en-US")
        template_path = locale_path / f"{template_name}_email.html"
        lang_path = locale_path / "lang.json"

        locale = json_loads(lang_path.read_text())
        subject: str = locale["subjects"][template_name]
        subject = self.__create_subject(subject.format_map(formats))

        template = template_path.read_text()
        template = template.format_map(formats)

        return subject, template

    def __create_subject(self, subject: str) -> str:
        return f"[{Env.PROJECT_NAME.capitalize()}] {subject}"
