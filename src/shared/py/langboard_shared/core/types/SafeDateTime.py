from datetime import datetime, timezone
from time import time


class SafeDateTime(datetime):
    def __new__(cls, year, month=None, day=None, hour=0, minute=0, second=0, microsecond=0, tzinfo=None, *, fold=0):
        if isinstance(year, bytes):
            self = super().__new__(cls, year, month)
        else:
            self = super().__new__(cls, year, month, day, hour, minute, second, microsecond, tzinfo, fold=fold)

        if self.tzinfo is None:
            self = self.replace(tzinfo=timezone.utc)
        return self

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        return handler(datetime)

    @classmethod
    def now(cls, tz=None):
        t = time()
        if tz is None:
            tz = timezone.utc
        return cls.fromtimestamp(t, tz)
