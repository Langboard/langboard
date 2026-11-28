"""Production schemas may mix legacy naive UTC and offset-aware creation dates."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from langboard.apps import BoardCloud, BoardCloudLinks


def test_cloud_creation_order_accepts_legacy_and_offset_aware_timestamps(monkeypatch):
    naive = datetime(2026, 11, 4, 18, 30)
    aware = naive.replace(tzinfo=timezone.utc)
    offset = aware.astimezone(timezone(timedelta(hours=9)))
    assert BoardCloud._utc_datetime(naive) == aware == BoardCloud._utc_datetime(offset)
    assert naive.tzinfo is None  # Normalizing sort keys must not mutate source dates.

    attachment = SimpleNamespace(created_at=naive, id=1)
    monkeypatch.setattr(BoardCloud, "_candidates", lambda *args, **kwargs: [(attachment, None, "project")])
    monkeypatch.setattr(
        BoardCloudLinks, "_candidates",
        lambda *args, **kwargs: [("card", 2, offset, aware, "Card", 0)],
    )
    rows = BoardCloudLinks._mixed_candidates(None, None, None, None, False, 10)
    assert [row[0][1] for row in rows] == ["project_attachment", "card_link"]
    assert all(row[0][0] == aware for row in rows)
    assert BoardCloudLinks._mixed_candidates(None, None, None, None, False, 1) == rows[:1]
