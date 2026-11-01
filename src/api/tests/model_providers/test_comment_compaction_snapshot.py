"""The API and graph use the same source identity and citation boundary."""

import json
import pytest
from langboard_shared.ai.comment_compaction import CommentCompactionEvidence, CommentCompactionSnapshot


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


def test_search_attributes_are_bounded_and_require_existing_evidence():
    current = snapshot()
    result = {"claims": [{"text": "Reported issue", "kind": "unresolved", "evidence_refs": ["comment:original"]}],
              "keywords": [{"value": "  concurrency  ", "evidence_refs": ["comment:original"]}],
              "entities": [{"name": "Phoenix", "kind": "technology", "aliases": ["Elixir Phoenix"],
                            "evidence_refs": ["comment:original"]}]}
    assert current.validate_draft_json(json.dumps(result)).keywords[0].value == "concurrency"
    for field in ("keywords", "entities"):
        changed = json.loads(json.dumps(result))
        changed[field][0]["evidence_refs"] = ["comment:outside"]
        with pytest.raises(ValueError, match="unknown evidence"):
            current.validate_draft_json(json.dumps(changed))
        changed[field] = result[field] * 21
        with pytest.raises(ValueError):
            current.validate_draft_json(json.dumps(changed))
    result["entities"][0]["kind"] = "permission"
    with pytest.raises(ValueError):
        current.validate_draft_json(json.dumps(result))


def test_cumulative_snapshot_keeps_prior_fingerprints_out_of_model_payload():
    original = snapshot()
    previous = {"draft": {"claims": [{"text": "An unresolved earlier report", "kind": "unresolved",
        "evidence_refs": ["comment:original"]}]}, "evidence_manifest": original.evidence_manifest()}
    current = CommentCompactionSnapshot(sources=[{
        "evidence_ref": "comment:new", "created_at": "2026-10-22T00:00:00Z",
        "updated_at": "2026-10-22T00:00:00Z", "text": "A newer report, not independently verified",
    }], prior_summaries=[previous])
    manifest = current.evidence_manifest()
    assert manifest[:2] == original.evidence_manifest()
    assert len(manifest) == 3
    payload = current.model_dump_json()
    assert "Original private evidence" not in payload and "fingerprint" not in payload
    assert "unresolved earlier report" in payload
    result = {"claims": [{"text": "Earlier report remains unresolved", "kind": "unresolved",
                         "evidence_refs": ["comment:original", "comment:new"]}]}
    assert current.validate_draft_json(json.dumps(result)).claims[0].kind == "unresolved"
    result["claims"][0]["evidence_refs"] = ["comment:invented"]
    with pytest.raises(ValueError, match="unknown evidence"):
        current.validate_draft_json(json.dumps(result))
    changed = current.model_copy(deep=True)
    changed.prior_summaries[0].evidence_manifest[0].fingerprint = "f" * 64
    assert changed.source_revision() != current.source_revision()


def test_cumulative_snapshot_rejects_overlap_and_unbound_prior_citations():
    original = snapshot()
    previous = {"draft": {"claims": [{"text": "Unresolved", "kind": "unresolved",
        "evidence_refs": ["comment:original"]}]}, "evidence_manifest": original.evidence_manifest()}
    with pytest.raises(ValueError, match="Duplicate source"):
        CommentCompactionSnapshot(sources=original.sources, prior_summaries=[previous])
    previous["draft"]["claims"][0]["evidence_refs"] = ["comment:invented"]
    with pytest.raises(ValueError, match="unknown evidence"):
        CommentCompactionSnapshot(sources=original.sources, prior_summaries=[previous])


@pytest.mark.parametrize("size", [100_001, 500_001])
def test_full_internal_evidence_identity_does_not_relax_provider_caps(size):
    original = snapshot().sources[0].model_dump(mode="json")
    original["text"] = "x" * size
    evidence = CommentCompactionEvidence(sources=[original])
    assert evidence.sources[0].text == original["text"]
    assert len(evidence.evidence_manifest()) == 1
    with pytest.raises(ValueError):
        CommentCompactionSnapshot(sources=[original])
