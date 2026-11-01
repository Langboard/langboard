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


def compaction_thresholds(effects: dict) -> dict[str, int]:
    thresholds = {"compact_after_characters": compaction_character_threshold(effects)}
    for key in ("compact_after_comments", "compact_after_turns"):
        value = effects.get(key, 0)
        if type(value) is not int or not 0 <= value <= 100_000:
            raise ValueError("Invalid compaction count threshold")
        thresholds[key] = value
    return thresholds


def compaction_threshold_reached(thresholds: dict[str, int], *, characters: int, comments: int, turns: int) -> bool:
    return any(thresholds[key] > 0 and value >= thresholds[key] for key, value in (
        ("compact_after_characters", characters), ("compact_after_comments", comments), ("compact_after_turns", turns),
    ))


EvidenceRef = Annotated[str, Field(pattern=r"^(comment|activity):[A-Za-z0-9]{1,64}$")]


def _digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def compaction_manifest_revision(manifest: list[dict[str, str]]) -> str:
    return _digest(manifest)


class CompactionEvidenceSource(BaseModel):
    """Full stored evidence, retained only inside the API for identity checks."""
    model_config = ConfigDict(extra="forbid")

    evidence_ref: EvidenceRef
    created_at: AwareDatetime
    updated_at: AwareDatetime
    text: str = Field(min_length=1)


class CompactionSource(CompactionEvidenceSource):
    """Bounded original or sequential portion sent to the provider."""

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


class CompactionEvidenceFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: EvidenceRef
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class PriorCompactionSummary(BaseModel):
    """A derived draft plus API-only provenance, never old original text.

    Graph receives the draft and its cited source references. The API retains
    full fingerprints to revalidate every original before publishing/reading.
    """
    model_config = ConfigDict(extra="forbid")

    draft: CompactionDraft
    evidence_manifest: list[CompactionEvidenceFingerprint] = Field(default_factory=list, exclude=True)

    def cited_refs(self) -> set[str]:
        return {ref for item in [*self.draft.claims, *self.draft.keywords, *self.draft.entities]
                for ref in item.evidence_refs}

    @model_validator(mode="after")
    def validate_provenance(self):
        refs = [entry.evidence_ref for entry in self.evidence_manifest]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate prior evidence")
        if refs and not self.cited_refs().issubset(refs):
            raise ValueError("Prior summary cites unknown evidence")
        return self


def validate_compaction_draft(content: str, allowed_refs: set[str]) -> CompactionDraft:
    if len(content) > 32_000:
        raise ValueError("Invalid compaction response")
    draft = CompactionDraft.model_validate_json(content)
    if any(not set(item.evidence_refs).issubset(allowed_refs)
           for item in [*draft.claims, *draft.keywords, *draft.entities]):
        raise ValueError("Compaction cites unknown evidence")
    return draft


class CommentCompactionEvidence(BaseModel):
    """API-only full originals; never serialize as a provider request."""
    model_config = ConfigDict(extra="forbid")

    sources: list[CompactionEvidenceSource] = Field(min_length=1, max_length=100)
    prior_summaries: list[PriorCompactionSummary] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_snapshot(self):
        refs = [source.evidence_ref for source in self.sources]
        for previous in self.prior_summaries:
            refs.extend(entry.evidence_ref for entry in previous.evidence_manifest)
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate source evidence")
        return self

    def evidence_manifest(self) -> list[dict[str, str]]:
        previous = [entry.model_dump() for summary in self.prior_summaries for entry in summary.evidence_manifest]
        return [*previous, *[{"evidence_ref": source.evidence_ref,
                 "fingerprint": _digest(source.model_dump(mode="json"))} for source in self.sources]]

    def source_revision(self) -> str:
        # Provider credentials and model changes are never part of source identity.
        return compaction_manifest_revision(self.evidence_manifest())

    def validate_draft_json(self, content: str) -> CompactionDraft:
        allowed = {source.evidence_ref for source in self.sources}
        for previous in self.prior_summaries:
            allowed.update(previous.cited_refs())
        return validate_compaction_draft(content, allowed)


class CommentCompactionSnapshot(CommentCompactionEvidence):
    """Bounded provider request; caps apply to every sequential step."""

    sources: list[CompactionSource] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_request_size(self):
        characters = sum(len(source.text) for source in self.sources)
        characters += sum(len(previous.draft.model_dump_json()) for previous in self.prior_summaries)
        if characters > 500_000:
            raise ValueError("Compaction snapshot contains too much text")
        return self
