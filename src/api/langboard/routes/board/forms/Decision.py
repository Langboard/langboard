"""Explicit concrete-user proposal and named single-choice Poll contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .DecisionActors import DecisionActorSource


class PollOptionForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    label: str = Field(strict=True, min_length=1, max_length=500)


class PollSettingsForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: list[PollOptionForm] = Field(min_length=2, max_length=20)
    participation_quorum: int = Field(strict=True, ge=1, le=256)
    participation_policy: Literal["vote_only", "all_responses"]
    winner_policy: Literal["plurality", "majority_of_votes"]
    tie_policy: Literal["needs_info", "rejected"]
    no_quorum_policy: Literal["expired", "rejected"]
    no_winner_policy: Literal["needs_info", "rejected"]
    needs_info_policy: Literal["record", "block_result"]
    allow_change: bool = Field(strict=True)
    tally_visibility: Literal["before", "after_vote", "after_close"]

    @model_validator(mode="after")
    def distinct_options(self):
        keys = [option.key for option in self.options]
        labels = [option.label.strip() for option in self.options]
        if len(set(keys)) != len(keys) or len(set(labels)) != len(labels) or not all(labels):
            raise ValueError("Distinct nonblank option keys and labels required")
        return self


class CreateDecisionForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    source_kind: Literal["proposal", "poll", "graph_approval"]
    source_ref: str = Field(strict=True, pattern=r"^[0-9A-Za-z]{11}$")
    approval_uid: str | None = Field(default=None, strict=True, pattern=r"^[0-9A-Za-z]{11}$")
    question: str = Field(strict=True, min_length=1, max_length=4000)
    target_revision: str = Field(strict=True, min_length=1, max_length=128)
    participant_uids: list[str] = Field(min_length=1, max_length=256)
    policy: dict | None = None
    deadline: datetime | None = None
    poll: PollSettingsForm | None = None
    actor_sources: list[DecisionActorSource] | None = Field(default=None, min_length=1, max_length=64)
    expected_actor_snapshot: str | None = Field(default=None, strict=True, pattern=r"^[0-9a-f]{64}$")

    @field_validator("deadline")
    @classmethod
    def aware_deadline(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Timezone-aware deadline required")
        return value

    @model_validator(mode="after")
    def source_contract(self):
        if (self.actor_sources is None) != (self.expected_actor_snapshot is None):
            raise ValueError("Actor sources and expected preview snapshot must be supplied together")
        if (self.source_kind == "graph_approval") != (self.approval_uid is not None):
            raise ValueError("Native approval binding is required only for graph approval policies")
        if self.source_kind in ("proposal", "graph_approval") and (self.policy is None or self.poll is not None):
            raise ValueError("Proposal requires recursive policy and forbids Poll settings")
        if self.source_kind == "poll" and (self.poll is None or self.deadline is None or self.policy is not None):
            raise ValueError("Poll requires explicit settings/deadline and forbids proposal policy")
        return self


class DecisionResponseForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    round_id: UUID
    expected_revision: int = Field(strict=True, ge=0)
    target_revision: str = Field(strict=True, min_length=1, max_length=128)
    policy_revision: str = Field(strict=True, min_length=1, max_length=64)
    choice: Literal["approve", "reject", "abstain", "needs_info", "vote"]
    option_uid: str | None = Field(default=None, strict=True, pattern=r"^[0-9A-Za-z]{11}$")

    @model_validator(mode="after")
    def option_contract(self):
        if (self.choice == "vote") != (self.option_uid is not None):
            raise ValueError("Exactly one option UID is required only for a vote")
        return self
