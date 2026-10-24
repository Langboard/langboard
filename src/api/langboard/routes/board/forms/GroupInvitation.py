from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class GroupInvitationForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_uid: str = Field(strict=True, pattern=r"^[0-9A-Za-z]{11}$")
    snapshot: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    confirmed_count: int = Field(strict=True, ge=0)
    confirmed: bool = Field(strict=True)
