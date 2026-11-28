from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class DecisionActorSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["user", "group", "team"]
    uid: Annotated[str, Field(strict=True, pattern=r"^[0-9A-Za-z]{11}$")]


class DecisionActorsPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["proposal", "poll"]
    sources: list[DecisionActorSource] = Field(min_length=1, max_length=64)
