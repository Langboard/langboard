"""Bounded evidence and result contract for explicitly reviewed avatar drafts."""

import hashlib
import json
import re
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


MODEL_KEYS = {"agent_llm", "model_name", "model", "model_id", "api_key", "base_url", "temperature", "max_tokens",
              "azure_endpoint", "api_version", "deployment_name", "azure_deployment", "region_name"}


def model_settings(value: str) -> dict[str, Any]:
    settings = json.loads(value)
    if not isinstance(settings, dict) or not settings.get("agent_llm") or not any(
        isinstance(settings.get(key), str) and settings[key].strip() for key in ("model_name", "model", "model_id")
    ):
        raise ValueError("Avatar summary model is unconfigured")
    return {key: item for key, item in settings.items() if key in MODEL_KEYS}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def clean_text(value, limit):
    text = str(value or "")
    text = re.sub(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.|mailto:)\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[removed]", text, flags=re.I)
    text = re.sub(r"(?i)\b(?:api[_ -]?key|password|passwd|secret|token|authorization)\s*[:=]\s*\S+|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+|\bBearer\s+\S+", "[removed]", text)
    text = re.sub(r"-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?-----END [^-]+-----", "[removed]", text, flags=re.S)
    text = re.sub(r"\b(?:AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "[removed]", text)
    return " ".join(text.split())[:limit]


class AvatarSummarySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern=r"^(project_title|project_description|project_type|workflow_stage|card_title|label)$")
    text: str = Field(min_length=1, max_length=1000)


class AvatarSummarySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[AvatarSummarySource] = Field(min_length=1, max_length=45)

    @model_validator(mode="after")
    def bounded(self):
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)) or sum(len(source.text) for source in self.sources) > 5000:
            raise ValueError("Invalid avatar sources")
        return self


class AvatarSummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=2000)
    source_refs: list[str] = Field(min_length=1, max_length=45)


def validate_draft(content: str, sources: list[AvatarSummarySource]) -> AvatarSummaryDraft:
    if len(content) > 10000:
        raise ValueError("Invalid avatar draft")
    draft = AvatarSummaryDraft.model_validate_json(content)
    refs = [source.id for source in sources]
    if len(draft.source_refs) != len(set(draft.source_refs)) or set(draft.source_refs) != set(refs):
        raise ValueError("Avatar draft source mismatch")
    return draft
