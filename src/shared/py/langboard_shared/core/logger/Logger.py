from logging import CRITICAL, ERROR, NOTSET, Handler, basicConfig, getLevelNamesMapping, getLogger, setLoggerClass
from logging import Logger as LoggingLogger
from re import fullmatch
from typing import Any
from rich.logging import RichHandler
from sentry_sdk import init as sentry_init
from ...Env import Env
from ..utils.decorators import class_instance, thread_safe_singleton
from .LogFileHandler import LogFileHandler


def diagnostic_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Keep error coordinates and explicit diagnostics, never application payloads."""
    safe = {
        key: event[key]
        for key in ("event_id", "timestamp", "platform", "level", "release", "environment")
        if key in event
    }
    safe["logentry"] = {"message": "Application error (content omitted)"}
    values = []
    for exception in event.get("exception", {}).get("values", []):
        name = exception.get("type", "Error")
        values.append(
            {
                "type": name if isinstance(name, str) and fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,79}", name) else "Error",
                "value": "Application error (content omitted)",
                "stacktrace": {
                    "frames": [
                        {key: frame[key] for key in ("module", "function", "lineno", "colno", "in_app") if key in frame}
                        for frame in exception.get("stacktrace", {}).get("frames", [])
                    ]
                },
            }
        )
    if values:
        safe["exception"] = {"values": values}
    # These fields are set by the tool dispatcher, not copied from tool arguments.
    extra = event.get("extra", {})
    tags = {}
    for key, pattern in {
        "request_id": r"[a-f0-9]{32}",
        "tool": r"[a-z][a-z0-9_]{0,79}",
        "cause_type": r"[A-Za-z][A-Za-z0-9_.]{0,79}",
    }.items():
        value = extra.get(key)
        if isinstance(value, str) and fullmatch(pattern, value):
            tags[key] = value
    if tags:
        safe["tags"] = tags
        if not values and "tool" in tags and "cause_type" in tags:
            safe["fingerprint"] = ["mcp-tool", tags["tool"], tags["cause_type"]]
    trace = event.get("contexts", {}).get("trace", {})
    if fullmatch(r"[a-f0-9]{32}", str(trace.get("trace_id", ""))) and fullmatch(
        r"[a-f0-9]{16}", str(trace.get("span_id", ""))
    ):
        safe["contexts"] = {"trace": {key: trace[key] for key in ("trace_id", "span_id")}}
    return safe


def diagnostic_transaction(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Retain timing and correlation without query text or application content."""
    safe = diagnostic_event(event, hint)
    safe.pop("logentry", None)
    safe.update(type="transaction", transaction="Application request", start_timestamp=event["start_timestamp"])
    safe["spans"] = []
    for span in event.get("spans", []):
        timing = {
            key: span[key]
            for key in ("trace_id", "span_id", "parent_span_id", "start_timestamp", "timestamp")
            if key in span
        }
        category = str(span.get("op", "")).split(".")[0]
        timing["op"] = category if category in {"db", "http", "cache", "middleware", "queue", "function"} else "other"
        safe["spans"].append(timing)
    return safe


class _LoggerWrapper(LoggingLogger):
    def __init__(self, name, level=NOTSET):
        if not name.startswith(Env.PROJECT_NAME) and not name.startswith("sqlalchemy"):
            level = ERROR
        super().__init__(name, level)


setLoggerClass(_LoggerWrapper)


@class_instance()
@thread_safe_singleton
class Logger:
    """Manages the application's logging."""

    main: LoggingLogger

    @property
    def terminal_level(self) -> int:
        levels = getLevelNamesMapping()
        return (
            levels[Env.TERMINAL_LOGGING_LEVEL]
            if Env.TERMINAL_LOGGING_LEVEL in levels
            else (levels["INFO"] if Env.ENVIRONMENT == "production" else levels["INFO"])
        )

    @property
    def file_level(self) -> int:
        levels = getLevelNamesMapping()
        return (
            levels[Env.FILE_LOGGING_LEVEL]
            if Env.FILE_LOGGING_LEVEL in levels
            else (levels["ERROR"] if Env.ENVIRONMENT == "production" else levels["WARNING"])
        )

    def __init__(self):
        basicConfig(
            level=self.terminal_level,
            format="%(name)s: %(message)s",
            datefmt="[%Y-%m-%d %X]",
            handlers=self.get_handlers(),
        )

        self.main = getLogger(Env.PROJECT_NAME)

        getLogger("asyncio").setLevel(ERROR)
        getLogger("multipart").setLevel(ERROR)
        getLogger("python_multipart.multipart").setLevel(ERROR)
        getLogger("aiosqlite").setLevel(ERROR)
        getLogger("asyncpg").setLevel(ERROR)
        getLogger("httpx").setLevel(ERROR)
        getLogger("urllib3.connectionpool").setLevel(ERROR)
        getLogger("httpcore.connection").setLevel(ERROR)
        getLogger("httpcore.http11").setLevel(ERROR)
        getLogger("strawberry.execution").setLevel(CRITICAL)

        if Env.SENTRY_DSN:
            sentry_init(
                dsn=Env.SENTRY_DSN,
                environment=Env.SENTRY_ENVIRONMENT,
                release=Env.SENTRY_RELEASE,
                send_default_pii=False,
                include_local_variables=False,
                # Short-lived recovery jobs retain default error/log capture
                # without importing every optional framework/provider SDK.
                auto_enabling_integrations=str(Env.get_from_env("SENTRY_AUTO_ENABLING_INTEGRATIONS", "true")).lower()
                != "false",
                before_send=diagnostic_event,
                # Apply the local opt-in even when an incoming trace was sampled.
                traces_sampler=lambda _context: Env.SENTRY_TRACES_SAMPLE_RATE,
                before_send_transaction=diagnostic_transaction,
            )

    def get_config(self) -> dict[str, Any]:
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "mcp_telemetry": {"format": "%(message)s"},
                "default": {
                    "()": "uvicorn.logging.DefaultFormatter",
                    "fmt": f"{Env.PROJECT_NAME}: %(message)s",
                    "datefmt": "[%Y-%m-%d %X]",
                    "use_colors": False,
                },
                "access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "fmt": f'{Env.PROJECT_NAME}: %(client_addr)s - "%(request_line)s" %(status_code)s',  # noqa: E501
                    "datefmt": "[%Y-%m-%d %X]",
                    "use_colors": False,
                },
            },
            "handlers": {
                "mcp_telemetry": {
                    "class": "logging.StreamHandler",
                    "formatter": "mcp_telemetry",
                    "level": "INFO",
                    "stream": "ext://sys.stdout",
                },
                "default": {
                    "formatter": "default",
                    "class": "rich.logging.RichHandler",
                    "level": self.terminal_level,
                    "omit_repeated_times": False,
                    "markup": True,
                    "rich_tracebacks": True,
                    "tracebacks_show_locals": False,
                },
                "access": {
                    "formatter": "access",
                    "class": "rich.logging.RichHandler",
                    "level": self.terminal_level,
                    "omit_repeated_times": False,
                    "markup": True,
                    "rich_tracebacks": True,
                    "tracebacks_show_locals": False,
                },
                "file": {
                    "class": "langboard_shared.core.logger.LogFileHandler.LogFileHandler",
                    "level": self.file_level,
                    "log_dir": str(Env.LOGGING_DIR),
                },
                "executable_file": {
                    "class": "langboard_shared.core.logger.LogFileHandler.LogFileHandler",
                    "level": self.file_level,
                    "log_dir": str(Env.LOGGING_DIR),
                    "is_terminal": True,
                },
            },
            "loggers": {
                f"{Env.PROJECT_NAME}.http.telemetry": {
                    "handlers": ["mcp_telemetry"],
                    "level": "INFO",
                    "propagate": False,
                },
                f"{Env.PROJECT_NAME}.mcp.telemetry": {
                    "handlers": ["mcp_telemetry"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn": {"handlers": ["default", "file"], "level": self.terminal_level, "propagate": False},
                "uvicorn.error": {"handlers": ["default", "file"], "level": self.terminal_level, "propagate": False},
                "uvicorn.access": {"handlers": ["access", "file"], "level": self.terminal_level, "propagate": False},
            },
        }

    @staticmethod
    def use(name: str) -> LoggingLogger:
        """Returns a logger with the given name."""
        return getLogger(f"{Env.PROJECT_NAME}.{name}")

    def get_handlers(self) -> list[Handler]:
        handlers = [
            RichHandler(
                level=self.terminal_level,
                omit_repeated_times=False,
                markup=True,
                rich_tracebacks=True,
                tracebacks_show_locals=False,
            ),
            LogFileHandler(level=self.file_level, log_dir=Env.LOGGING_DIR, is_terminal=Env.IS_EXECUTABLE),
        ]

        return handlers
