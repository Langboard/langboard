"""Native kafka-python transport options shared by publishers and operator tools."""

import logging
import ssl
import traceback
from collections.abc import Mapping
from typing import Any


class _KafkaAuthenticationLogFilter(logging.Filter):
    """Sanitize the locked client's actual authentication log emitters."""

    def __init__(self, username: str, password: str):
        super().__init__()
        self.credentials = (username, password)

    def _redact(self, value: str) -> str:
        for credential in self.credentials:
            value = value.replace(credential, "[redacted]").replace(repr(credential)[1:-1], "[redacted]")
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        # conn and protocol.parser log native request/response repr at DEBUG.
        # These contain wire authentication bytes, including SCRAM material.
        if any(marker in message for marker in ("SaslAuthenticate", "auth_bytes=", "Authenticated as ")):
            record.msg = "Kafka authentication diagnostic (content omitted)"
            record.exc_info = None
            record.exc_text = None
        else:
            record.msg = self._redact(message)
            if record.exc_info:
                record.exc_text = self._redact("".join(traceback.format_exception(*record.exc_info)))
                record.exc_info = None
        record.args = ()
        return True


def _protect_authentication_logs(username: str, password: str) -> None:
    # A filter on the parent kafka logger would not see propagated child records.
    # These are the two emitters in the locked kafka-python authentication path.
    for name in ("kafka.conn", "kafka.protocol.parser"):
        logger = logging.getLogger(name)
        existing = next((item for item in logger.filters if isinstance(item, _KafkaAuthenticationLogFilter)), None)
        if existing is None:
            logger.addFilter(_KafkaAuthenticationLogFilter(username, password))
        else:
            existing.credentials = (username, password)


def transport_options(values: Mapping[str, str]) -> dict[str, Any]:
    protocol = values.get("BROADCAST_KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    tls = protocol in {"SSL", "SASL_SSL"}
    sasl = protocol in {"SASL_PLAINTEXT", "SASL_SSL"}
    if protocol not in {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}:
        raise ValueError("BROADCAST_KAFKA_SECURITY_PROTOCOL is unsupported")
    ca, cert, key = (values.get(f"BROADCAST_KAFKA_SSL_{name}", "") for name in ("CAFILE", "CERTFILE", "KEYFILE"))
    mechanism, username, password = (
        values.get(f"BROADCAST_KAFKA_SASL_{name}", "") for name in ("MECHANISM", "USERNAME", "PASSWORD")
    )
    if not tls and any((ca, cert, key)):
        raise ValueError("Kafka TLS files require SSL or SASL_SSL")
    if bool(cert) != bool(key):
        raise ValueError("Kafka TLS client certificate and key must be configured together")
    if not sasl and any((mechanism, username, password)):
        raise ValueError("Kafka SASL settings require a SASL security protocol")
    if sasl and (mechanism not in {"PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"} or not username or not password):
        raise ValueError("Kafka SASL requires a supported mechanism, username and password")
    options: dict[str, Any] = {"security_protocol": protocol}
    if tls:
        try:
            context = ssl.create_default_context(cafile=ca or None)
            if cert:
                context.load_cert_chain(cert, key)
        except (OSError, ValueError, ssl.SSLError):
            raise ValueError("Kafka TLS trust or client certificate configuration is invalid") from None
        options.update(ssl_context=context, ssl_check_hostname=True)
    if sasl:
        _protect_authentication_logs(username, password)
        options.update(sasl_mechanism=mechanism, sasl_plain_username=username, sasl_plain_password=password)
    return options
