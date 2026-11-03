"""Request-local DBAPI execute costs; excludes pool acquisition and result fetching."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter
from sqlalchemy import Engine, event


@dataclass
class ReadQueryTiming:
    count: int = 0
    errors: int = 0
    elapsed_ms: float = 0.0
    active: bool = True
    lock: Lock = field(default_factory=Lock, repr=False)

    def snapshot(self, *, close: bool = False) -> dict[str, int | float]:
        with self.lock:
            if close:
                self.active = False
            return {"sql_count": self.count, "sql_errors": self.errors, "sql_execute_ms": round(self.elapsed_ms, 3)}


read_query_timing: ContextVar[ReadQueryTiming | None] = ContextVar("read_query_timing", default=None)


def _before_execute(conn, cursor, statement, parameters, context, executemany):
    timing = read_query_timing.get()
    if timing is not None and timing.active and context is not None:
        context._langboard_read_timing = (timing, perf_counter())


def _finish(context, *, failed: bool):
    measured = getattr(context, "_langboard_read_timing", None)
    if measured is None:
        return
    del context._langboard_read_timing
    timing, started = measured
    elapsed_ms = (perf_counter() - started) * 1000
    with timing.lock:
        if timing.active:
            timing.count += 1
            timing.errors += int(failed)
            timing.elapsed_ms += elapsed_ms


def _after_execute(conn, cursor, statement, parameters, context, executemany):
    _finish(context, failed=False)


def _handle_error(exception_context):
    _finish(exception_context.execution_context, failed=True)


def instrument_read_queries(engine: Engine) -> Engine:
    event.listen(engine, "before_cursor_execute", _before_execute)
    event.listen(engine, "after_cursor_execute", _after_execute)
    event.listen(engine, "handle_error", _handle_error)
    return engine
