from fastapi import Query
from pydantic import BaseModel, ConfigDict, field_validator
from ..types import SafeDateTime


class Pagination(BaseModel):
    page: int = Query(default=1, ge=1)
    limit: int = Query(default=1, ge=1)


class TimeBasedPagination(Pagination):
    model_config = ConfigDict(validate_default=True)

    refer_time: SafeDateTime = Query(default=None)

    @field_validator("refer_time", mode="before")
    @classmethod
    def _default_refer_time(cls, value):
        return SafeDateTime.now() if value is None else value
