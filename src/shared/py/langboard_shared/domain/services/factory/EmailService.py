import asyncio
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from json import loads as json_loads
import aiosmtplib
from ....core.domain import BaseDomainService
from ....core.resources import get_resource_path
from ....core.resources.locales.EmailTemplateNames import TEmailTemplateName
from ....Env import Env


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
        return message

    def send_message(self, message: EmailMessage, *, strict: bool = False) -> bool:
        try:
            asyncio.run(
                aiosmtplib.send(
                    message,
                    hostname=Env.MAIL_SERVER,
                    port=int(Env.MAIL_PORT),
                    username=Env.MAIL_USERNAME or None,
                    password=Env.MAIL_PASSWORD or None,
                    start_tls=Env.MAIL_STARTTLS,
                    use_tls=Env.MAIL_SSL_TLS,
                    timeout=5,
                )
            )
        except Exception:
            if Env.ENVIRONMENT == "development" and not strict:
                return True
            return False

        return True

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
