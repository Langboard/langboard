"""The API and graph use the same source identity and citation boundary."""

import json
import pytest
from langboard_shared.ai.comment_compaction import CommentCompactionSnapshot


def snapshot():
    return CommentCompactionSnapshot(sources=[{
        "evidence_ref": "comment:original", "created_at": "2026-10-21T00:00:00Z",
        "updated_at": "2026-10-21T00:00:00Z", "text": "Original private evidence",
    }, {
        "evidence_ref": "activity:moved", "created_at": "2026-10-21T00:01:00Z",
        "updated_at": "2026-10-21T00:01:00Z", "text": "User moved Request to Doing",
    }])


@pytest.mark.parametrize("change", ["edit", "timestamp", "delete", "replace", "reorder"])
def test_persistable_manifest_detects_source_changes_without_copying_comment_text(change):
    original = snapshot()
    changed = original.model_copy(deep=True)
    if change == "edit":
        changed.sources[0].text = "Edited private evidence"
    elif change == "timestamp":
        changed.sources[0].updated_at = changed.sources[1].updated_at
    elif change == "delete":
        changed.sources.pop(0)
    elif change == "replace":
        changed.sources[0].evidence_ref = "comment:replacement"
    else:
        changed.sources.reverse()
    assert original.source_revision() != changed.source_revision()
    manifest = original.evidence_manifest()
    assert set(manifest[0]) == {"evidence_ref", "fingerprint"}
    assert "private evidence" not in json.dumps(manifest)
    assert len(manifest[0]["fingerprint"]) == 64
    assert snapshot().source_revision() == original.source_revision()


def test_citations_accept_only_present_evidence_and_do_not_change_sources():
    current = snapshot()
    before = current.model_dump(mode="json")
    claim = {"text": "Work remains unverified", "kind": "unresolved",
             "evidence_refs": ["comment:original", "activity:moved"]}
    assert current.validate_draft_json(json.dumps({"claims": [claim]})).claims[0].kind == "unresolved"
    for refs in (["comment:outside"], []):
        with pytest.raises(ValueError):
            current.validate_draft_json(json.dumps({"claims": [{**claim, "evidence_refs": refs}]}))
    assert current.model_dump(mode="json") == before


def test_api_snapshot_rejects_duplicate_and_unbounded_source_sets():
    source = snapshot().sources[0].model_dump(mode="json")
    with pytest.raises(ValueError, match="Duplicate source"):
        CommentCompactionSnapshot(sources=[source, source])
    with pytest.raises(ValueError, match="too much text"):
        CommentCompactionSnapshot(sources=[{**source, "evidence_ref": f"comment:c{i}", "text": "x" * 16_000} for i in range(32)])
    with pytest.raises(ValueError):
        CommentCompactionSnapshot(sources=[])
