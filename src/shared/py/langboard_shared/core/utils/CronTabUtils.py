import os
from contextlib import contextmanager
from functools import wraps
from os import close, environ, replace
from pathlib import Path
from stat import S_IMODE
from subprocess import Popen
from subprocess import run as subprocess_run
from sys import executable
from tempfile import mkstemp
from threading import RLock, local
from typing import Callable, Iterator, ParamSpec, TypeVar
from zoneinfo import ZoneInfo
import crontab
from crontab import SPECIALS, CronItem, CronTab
from psutil import process_iter
from ...Env import Env
from ..types import SafeDateTime


crontab.SPECIALS_CONVERSION = False
_cron_lock = RLock()
_lock_state = local()
_P = ParamSpec("_P")
_R = TypeVar("_R")
_scheduler_process: Popen | None = None

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None


try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None


class CronTabUtils:
    def __init__(self, file_path: str | Path | None = None):
        self.file_path = Path(file_path) if file_path else Env.CRON_TAB_FILE

    def reload_cron(self):
        if Env.ENVIRONMENT == "development":
            return
        with self.__lock():
            self.__write_atomic(self.get_cron())
            self.__reload_cron()

    def convert_valid_interval_str(self, interval_str: str) -> str:
        """Convert a string to a valid cron interval string.

        If the string is not valid, return an empty string.
        """
        try:
            job = CronItem()
            job.setall(interval_str)
            interval_str = str(job.slices)
            special = SPECIALS.get(interval_str.replace("@", ""), None)
            if special:
                return special
            return interval_str
        except Exception:
            return ""

    def get_cron(self):
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.file_path.touch()
        cron = CronTab(user=False, tabfile=str(self.file_path))
        cron_environment = cron.env
        if cron_environment is None:
            raise RuntimeError("Cron environment was not initialized")
        cron_environment.clear()
        for job in cron:
            job.env.clear()
        if Env.ENVIRONMENT != "development":
            cron_environment.update(environ)

        return cron

    def create_job(self, cron: CronTab, interval_str: str, command: str, filterable_comment: str) -> bool:
        has_job = False
        for job in cron.find_comment(filterable_comment):
            has_job = True
            break

        if has_job:
            return False

        job = cron.new(
            command=command,
            comment=filterable_comment,
            user="/bin/bash",
        )
        job.setall(interval_str)
        return True

    def remove_job(self, cron: CronTab, filterable_comment: str):
        cron.remove_all(comment=filterable_comment)

    def save_cron(self, cron: CronTab):
        with self.__lock():
            self.__write_atomic(cron)
            if Env.ENVIRONMENT != "development":
                self.__reload_cron()

    def mutation(self, operation: Callable[_P, _R]) -> Callable[_P, _R]:
        """Serialize the entire read, mutate, and save sequence for a cron writer."""

        @wraps(operation)
        def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with self.__lock():
                return operation(*args, **kwargs)

        return serialized

    def static_mutation(self, operation: Callable[_P, _R]) -> "staticmethod[_P, _R]":
        """Keep a cron mutation static when compiled as a native module."""
        return staticmethod(self.mutation(operation))

    def __write_atomic(self, cron: CronTab) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_path = mkstemp(prefix=f".{self.file_path.name}.", dir=self.file_path.parent)
        try:
            close(descriptor)
            if self.file_path.exists():
                Path(temp_path).chmod(S_IMODE(self.file_path.stat().st_mode))
            cron.write(filename=temp_path)
            replace(temp_path, self.file_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def __reload_cron(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            global _scheduler_process
            if _scheduler_process is not None and _scheduler_process.poll() is None:
                return
            _scheduler_process = Popen(
                [executable, "-m", "langboard_shared.core.utils.CronScheduler", str(self.file_path)],
                start_new_session=True,
            )
            return
        subprocess_run(["crontab", str(self.file_path)], check=True)
        if any(process.info.get("name") == "cron" for process in process_iter(["pid", "name"])):
            return
        subprocess_run(["cron"], check=True)

    @contextmanager
    def __lock(self) -> Iterator[None]:
        lock_path = self.file_path.with_suffix(f"{self.file_path.suffix}.lock")
        with _cron_lock:
            held_paths = getattr(_lock_state, "held_paths", None)
            if held_paths is None:
                held_paths = set()
                _lock_state.held_paths = held_paths
            resolved_path = lock_path.resolve()
            if resolved_path in held_paths:
                yield
                return
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+") as lock_file:
                if _fcntl is not None:
                    getattr(_fcntl, "flock")(lock_file.fileno(), getattr(_fcntl, "LOCK_EX"))
                elif _msvcrt is not None:
                    lock_file.seek(0)
                    getattr(_msvcrt, "locking")(lock_file.fileno(), getattr(_msvcrt, "LK_LOCK"), 1)
                else:
                    raise RuntimeError("Cross-process cron file locking is unavailable")
                held_paths.add(resolved_path)
                try:
                    yield
                finally:
                    held_paths.remove(resolved_path)
                    if _fcntl is not None:
                        getattr(_fcntl, "flock")(lock_file.fileno(), getattr(_fcntl, "LOCK_UN"))
                    elif _msvcrt is not None:
                        lock_file.seek(0)
                        getattr(_msvcrt, "locking")(lock_file.fileno(), getattr(_msvcrt, "LK_UNLCK"), 1)

    def adjust_interval_for_utc(self, interval_str: str, tz: str | float) -> str:
        if isinstance(tz, str):
            try:
                tz = float(tz)
            except Exception:
                try:
                    info = ZoneInfo(tz)
                    delta = info.utcoffset(SafeDateTime.now())
                    if delta is None:
                        tz = 0.0
                    else:
                        tz = delta.total_seconds() / 3600.0
                except Exception:
                    tz = 0.0

        if tz == 0.0:
            return interval_str

        real_interval_str = interval_str
        special = SPECIALS.get(interval_str.replace("@", ""), None)
        if special:
            real_interval_str = special

        if real_interval_str.lower() == "@reboot":
            return interval_str

        cron_item = CronItem()
        cron_item.setall(interval_str)
        cron_chunks = real_interval_str.split(" ")

        diff_minutes = int((tz - int(tz)) * 60)
        diff_hours = int(tz)

        if diff_minutes != 0 and cron_item.minutes != "*":
            cron_chunks[0] = self.__adjust_interval_for_utc_chunk(
                str(cron_item.minutes),
                diff_minutes,
                self.__ensure_valid_minute,
                is_minute=True,
            )

        if diff_hours != 0 and cron_item.hours != "*":
            cron_chunks[1] = self.__adjust_interval_for_utc_chunk(
                str(cron_item.hours),
                diff_hours,
                self.__ensure_valid_hour,
                is_minute=False,
            )

        return " ".join(cron_chunks)

    def __adjust_interval_for_utc_chunk(
        self, chunk: str, diff: int, ensure: Callable[[int], int], is_minute: bool
    ) -> str:
        parts = chunk.split(",")
        new_chunks: list[str] = []

        for part in parts:
            if part.startswith("*/"):
                interval = int(part[2:])
                max_interval = 60 if is_minute else 24
                if interval == 1:
                    new_chunks.append(part)
                    continue

                tz_intervals = [time for time in range(0, max_interval, interval)]
                new_intervals = sorted({ensure(time - diff) for time in tz_intervals})
                new_chunks.append(",".join(str(time) for time in new_intervals))
            elif part.count("-") == 1:
                start, end = map(int, part.split("-"))
                new_start = ensure(start - diff)
                new_end = ensure(end - diff)
                new_chunks.append(f"{new_start}-{new_end}")
            else:
                new_value = ensure(int(part) - diff)
                new_chunks.append(str(new_value))
        return ",".join(new_chunks)

    def __ensure_valid_minute(self, minute: int) -> int:
        return ((minute % 60) + 60) % 60

    def __ensure_valid_hour(self, hour: int) -> int:
        return ((hour % 24) + 24) % 24
