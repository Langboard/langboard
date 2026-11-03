from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


UID = Annotated[str, Field(strict=True, pattern=r"^[0-9A-Za-z]{11}$")]


class TeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class TeamCreate(TeamRequest):
    name: str = Field(strict=True, min_length=1, max_length=200)


class TeamRevision(TeamRequest):
    expected_revision: int = Field(strict=True, ge=1)


class TeamRename(TeamRevision):
    name: str = Field(strict=True, min_length=1, max_length=200)


class TeamMemberDelta(TeamRevision):
    user_uids: list[UID] = Field(min_length=1, max_length=100)


class TeamGroupImport(TeamRevision):
    group_uid: UID
    snapshot: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")
    confirmed_count: int = Field(strict=True, ge=0)
    confirmed: bool = Field(strict=True)
