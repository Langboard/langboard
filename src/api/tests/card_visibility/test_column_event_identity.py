"""Column event evidence retains identity even when display names collide."""

from types import SimpleNamespace

from langboard.card_workspace.application.projections import public_card_timeline_entry
from langboard_shared.tasks.activities.utils.ActivityHistoryHelper import ActivityHistoryHelper


def test_column_snapshots_distinguish_equal_names_and_survive_rename():
    source = SimpleNamespace(name="Review", get_uid=lambda: "column-a")
    target = SimpleNamespace(name="Review", get_uid=lambda: "column-b")
    before = ActivityHistoryHelper.create_project_column_history(source)
    after = ActivityHistoryHelper.create_project_column_history(target)
    source.name = "Renamed"
    target.name = "Also renamed"
    result = public_card_timeline_entry({
        "kind": "activity", "uid": "move-1", "evidence_ref": "activity:move-1",
        "created_at": "2026-11-04T00:00:00Z",
        "data": {"from_column": before, "column": after, "recorder": None},
    })
    assert result["from_column"] == {"uid": "column-a", "name": "Review"}
    assert result["to_column"] == {"uid": "column-b", "name": "Review"}
    assert result["evidence_ref"] == "activity:move-1"


def test_old_column_evidence_does_not_invent_identity_or_leak_extra_fields():
    result = public_card_timeline_entry({
        "kind": "activity", "uid": "old-move", "evidence_ref": "activity:old-move",
        "data": {"from_column": None, "column": {"name": "Deleted column", "secret": "excluded"}},
    })
    assert result["from_column"] == {}
    assert result["to_column"] == {"name": "Deleted column"}
