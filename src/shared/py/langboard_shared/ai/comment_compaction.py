"""Shared evidence contract for authorized API snapshots and model drafts.

The manifest fingerprints sources without duplicating their text. Matching a
revision only proves source identity; callers must still check current access.
"""

import hashlib
import json
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


DEFAULT_COMPACTION_CHARACTERS = 12_000


def compaction_character_threshold(effects: dict) -> int:
    value = effects.get("compact_after_characters", DEFAULT_COMPACTION_CHARACTERS)
    if type(value) is not int or not 1_000 <= value <= 500_000:
        raise ValueError("Invalid compaction character threshold")
    return value


EvidenceRef = Annotated[str, Field(pattern=r"^(comment|activity):[A-Za-z0-9]{1,64}$")]


def _digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class CompactionSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: EvidenceRef
    created_at: AwareDatetime
    updated_at: AwareDatetime
    text: str = Field(min_length=1, max_length=100_000)


class CompactionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["fact", "reported_success", "reported_failure", "unresolved", "conflict"]
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_meaningful_claim(self):
        if not self.text.strip():
            raise ValueError("Empty compaction claim")
        return self


class CompactionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[CompactionClaim] = Field(min_length=1, max_length=12)
    keywords: list["CompactionKeyword"] = Field(default_factory=list, max_length=20)
    entities: list["CompactionEntity"] = Field(default_factory=list, max_length=20)


class CompactionKeyword(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(min_length=1, max_length=80)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=10)


class CompactionEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["person", "organization", "project", "product", "technology", "location", "document", "other"]
    aliases: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=5)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=10)


class CommentCompactionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[CompactionSource] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_snapshot(self):
        refs = [source.evidence_ref for source in self.sources]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate source evidence")
        if sum(len(source.text) for source in self.sources) > 500_000:
            raise ValueError("Compaction snapshot contains too much text")
        return self

    def evidence_manifest(self) -> list[dict[str, str]]:
        return [{"evidence_ref": source.evidence_ref,
                 "fingerprint": _digest(source.model_dump(mode="json"))} for source in self.sources]

    def source_revision(self) -> str:
        # Provider credentials and model changes are never part of source identity.
        return _digest(self.evidence_manifest())

    def validate_draft_json(self, content: str) -> CompactionDraft:
        if len(content) > 32_000:
            raise ValueError("Invalid compaction response")
        draft = CompactionDraft.model_validate_json(content)
        allowed = {source.evidence_ref for source in self.sources}
        if any(not set(item.evidence_refs).issubset(allowed) for item in [*draft.claims, *draft.keywords, *draft.entities]):
            raise ValueError("Compaction cites unknown evidence")
        return draft
