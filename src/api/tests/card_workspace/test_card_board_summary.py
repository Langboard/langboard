import os
import pytest


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard_shared.core.db import EditorContentModel  # noqa: E402
from langboard_shared.domain.models import Card  # noqa: E402


def _card(description: str) -> Card:
    return Card(project_id=1, project_column_id=2, title="Card", description=EditorContentModel(content=description))


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("본문이 있습니다.", True),
        ("   ", False),
        ("", False),
    ],
)
def test_board_api_response_reports_has_description(description: str, expected: bool) -> None:
    """Board summaries expose body presence so the UI never reads full descriptions."""

    response = _card(description).board_api_response(0, [], [], [])
    assert response["has_description"] is expected


def test_board_api_response_reports_completion_flags() -> None:
    """Board summaries carry the check-card state the board UI branches on."""

    response = _card("본문").board_api_response(0, [], [], [], completed=True, is_check_card=False)
    assert response["completed"] is True
    assert response["is_check_card"] is False

    defaults = _card("").board_api_response(0, [], [], [])
    assert defaults["completed"] is False
    assert defaults["is_check_card"] is False


def test_compact_board_projection_keeps_flags_but_leaves_bodies_to_detail() -> None:
    import json

    body = "Large document with Korean 한국어 and Japanese 日本語. " * 4000
    card = _card(body)
    card.ai_description = body
    full = card.board_api_response(3, ["member"], [], [], completed=True)
    summary = card.board_api_response(3, ["member"], [], [], completed=True, include_body=False)
    assert summary == {key: value for key, value in full.items() if key not in ("description", "ai_description")}
    assert summary["has_description"] is True
    assert summary["completed"] is True
    assert full["description"]["content"] == body
    assert card.api_response()["description"]["content"] == body
    assert card.api_response()["ai_description"] == body
    assert len(json.dumps(summary, default=str).encode()) < len(json.dumps(full, default=str).encode()) / 100


def test_compact_board_schema_matches_body_omission_without_changing_detail_schema() -> None:
    full = Card.api_schema()
    compact = Card.api_schema({"has_description": "boolean"}, include_body=False)
    assert "description" in full and "ai_description" in full
    assert "description" not in compact and "ai_description" not in compact
    assert compact["has_description"] == "boolean"


def test_compact_projection_does_not_serialize_discarded_document_body(monkeypatch) -> None:
    card = _card("Large document 한국어 日本語 " * 4000)
    original = EditorContentModel.model_dump
    serialized = []

    def track_dump(self, *args, **kwargs):
        serialized.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(EditorContentModel, "model_dump", track_dump)
    summary = card.board_api_response(0, [], [], [], include_body=False)
    assert summary["has_description"] is True
    assert serialized == []
    full = card.api_response()
    assert full["description"]["content"] == card.description.content
    assert serialized == [card.description]
