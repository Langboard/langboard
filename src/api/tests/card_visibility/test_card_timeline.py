"""Comments and committed moves share one bounded, card-scoped creation order."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp.exceptions import ValidationError
from langboard.mcp_tools import CardMcp
from langboard.routes.activities import ActivityApi
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import Bot, Card, CardComment, ProjectActivity
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services.factory.ActivityService import ActivityService
from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
from langboard_shared.infrastructure.repositories.factory.ActivityRepository import ActivityRepository
from langboard_shared.infrastructure.repositories.factory.CardCommentRepository import CardCommentRepository
from sqlalchemy import select, update


@pytest.fixture
def timeline(current_card):
    user, project, card, _ = current_card
    for model in (CardComment, ProjectActivity):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.use(readonly=False) as db:
        other = Card(project_id=project.id, title="Different card", visibility="PRIVATE", owner_user_id=user.id, created_by_user_id=user.id)
        db.insert(other)
        timestamp = SafeDateTime.now()
        rows = []
        for index in range(47):
            if index % 2:
                record = CardComment(card_id=card.id, user_id=user.id, created_at=timestamp)
                kind = "comment"
            else:
                record = ProjectActivity(project_id=project.id, card_id=card.id, user_id=user.id, created_at=timestamp,
                                         activity_type=ProjectActivityType.CardMoved,
                                         activity_history={"from_column": {"name": "Review"}, "column": {"name": "Doing"},
                                                           "recorder": {"type": "user", "firstname": "Original actor"}})
                kind = "activity"
            db.insert(record)
            rows.append((kind, record))
        # Distinct source tables can share an ID; kind must break a page-boundary tie.
        tied_move = ProjectActivity(project_id=project.id, card_id=card.id, user_id=user.id,
                                    created_at=timestamp, activity_type=ProjectActivityType.CardMoved)
        db.insert(tied_move)
        db.exec(update(ProjectActivity).where(ProjectActivity.id == tied_move.id).values(id=rows[27][1].id))
        tied_move.id = rows[27][1].id
        rows.append(("activity", tied_move))
        foreign = CardComment(card_id=other.id, user_id=user.id)
        created = ProjectActivity(project_id=project.id, card_id=card.id, user_id=user.id,
                                  activity_type=ProjectActivityType.CardCreated)
        db.insert(foreign)
        db.insert(created)
    with DbSession.use(readonly=True) as db:
        assert db.exec(select(ProjectActivity.id).where(ProjectActivity.id == tied_move.id)).first() is not None
    return project, card, rows, foreign, created


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_timeline_pages_share_one_order_and_reject_foreign_cursors(timeline):
    project, card, rows, foreign, created = timeline
    repo = ActivityRepository(None, None)
    actual = []
    cursor = None
    page_sizes = []
    tied = repo.get_card_timeline_page(project, card, before=f"comment:{rows[27][1].get_uid()}")
    assert tied[0][:2] == ("activity", rows[27][1].id), tied[:2]
    while True:
        page = repo.get_card_timeline_page(project, card, before=cursor)
        assert len(page) <= 21
        visible = page[:20]
        page_sizes.append(len(visible))
        actual.extend((kind, record_id) for kind, record_id, _ in visible)
        if len(page) <= 20:
            break
        last = visible[-1]
        cursor = f"{last[0]}:{SnowflakeID(last[1]).to_short_code()}"
    assert len(actual) == len(rows), (set((kind, record.id) for kind, record in rows) - set(actual), page_sizes)
    assert page_sizes == [20, 20, 8]
    assert actual == [(kind, record.id) for kind, record in sorted(rows, key=lambda pair: (pair[1].created_at, pair[1].id, pair[0]), reverse=True)]
    for cursor in (f"comment:{foreign.get_uid()}", f"activity:{created.get_uid()}", "bad", "comment:!", "other:abc"):
        assert repo.get_card_timeline_page(project, card, before=cursor) == []


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_deleted_cursor_retains_position_and_comment_edits_do_not_reorder(timeline):
    project, card, rows, _, _ = timeline
    repo = ActivityRepository(None, None)
    old_comment = rows[1][1]
    with DbSession.use(readonly=False) as db:
        old_comment.updated_at = SafeDateTime.now()
        db.update(old_comment)
    assert repo.get_card_timeline_page(project, card)[0][1] == max(record.id for _, record in rows)
    cursor_comment = rows[25][1]
    before = f"comment:{cursor_comment.get_uid()}"
    expected = repo.get_card_timeline_page(project, card, before=before)
    with DbSession.use(readonly=False) as db:
        cursor_comment.deleted_at = SafeDateTime.now()
        db.update(cursor_comment)
    assert repo.get_card_timeline_page(project, card, before=before) == expected
    page = repo.get_card_timeline_page(project, card, before=f"activity:{rows[26][1].get_uid()}")
    assert cursor_comment.id not in [record_id for _, record_id, _ in page]


def test_timeline_projects_original_snapshots_and_batches_only_page_comments(timeline):
    project, card, rows, _, _ = timeline
    comments = {record.id: record for kind, record in rows if kind == "comment"}
    def convert(_, *, comment_ids):
        return [comments[uid].api_response() for uid in comment_ids]
    comment_service = SimpleNamespace(get_api_list_by_card=Mock(side_effect=convert))
    service = ActivityService(lambda _: comment_service, lambda _: None, SimpleNamespace(activity=ActivityRepository(None, None)))
    result = service.get_card_timeline(project, card)
    assert len(result["records"]) == 20 and result["next_cursor"]
    for event in result["records"]:
        assert event["evidence_ref"] == f'{event["kind"]}:{event["uid"]}'
        assert event["created_at"] == event["data"]["created_at"]
        if event["kind"] == "activity":
            assert event["data"]["from_column"] == {"name": "Review"}
            assert event["data"]["column"] == {"name": "Doing"}
            assert event["data"]["recorder"]["firstname"] == "Original actor"
    assert len(comment_service.get_api_list_by_card.call_args.kwargs["comment_ids"]) == 10


def test_timeline_route_forwards_the_single_cursor_after_visibility(monkeypatch):
    monkeypatch.setattr(ActivityApi, "require_visible_card", Mock())
    timeline = Mock(return_value={"records": [], "next_cursor": None})
    service = SimpleNamespace(activity=SimpleNamespace(get_card_timeline=timeline))
    ActivityApi.get_card_timeline("project", "card", SimpleNamespace(scope={}), "comment:cursor", object(), service)
    timeline.assert_called_once_with("project", "card", before="comment:cursor")


def test_comment_payload_batch_never_loads_other_cards_or_the_unrequested_page(timeline):
    _, card, rows, foreign, _ = timeline
    Bot.__table__.create(DbEngine.get_main_engine())
    reactions = SimpleNamespace(get_api_map=Mock(return_value={}))
    service = CardCommentService(lambda _: reactions, lambda _: None, SimpleNamespace(card_comment=CardCommentRepository(None, None)))
    target = rows[1][1]
    result = service.get_api_list_by_card(card, comment_ids=[target.id, foreign.id])
    assert [comment["uid"] for comment in result] == [target.get_uid()]
    assert result[0]["user"]["firstname"] == "Fixture"
    assert service.get_api_list_by_card(card, comment_ids=[]) == []


def test_mcp_timeline_uses_current_visibility_and_public_snapshots(timeline, current_card):
    project, card, _, _, _ = timeline
    user, _, _, card_service = current_card
    page = {"records": [
        {"kind": "activity", "uid": "source", "evidence_ref": "activity:source", "created_at": "2026-10-21T00:00:00Z",
         "data": {"recorder": {"uid": "actor", "firstname": "Original", "email": "private@example.invalid", "token": "secret"},
                  "from_column": {"name": "Before", "private": "secret"}, "column": {"name": "After"}}},
        {"kind": "comment", "uid": "comment", "evidence_ref": "comment:comment", "created_at": "2026-10-21T00:00:00Z",
         "data": {"uid": "comment", "content": {"content": "bounded comment"},
                  "user": {"uid": "actor", "firstname": "Original", "email": "private@example.invalid"}}},
    ], "next_cursor": "activity:next"}
    activity = SimpleNamespace(get_view=Mock(return_value=page))
    service = SimpleNamespace(card=card_service, card_comment_compaction=activity)
    result = CardMcp.get_card_timeline(project.get_uid(), card.get_uid(), user, service, "activity:before")
    assert result["next_cursor"] == "activity:next"
    move = result["records"][0]
    assert move["actor"] == {"uid": "actor", "firstname": "Original"}
    assert move["from_column"] == {"name": "Before"} and move["to_column"] == {"name": "After"}
    assert move["evidence_ref"] == "activity:source"
    assert "secret" not in str(result) and "private@example.invalid" not in str(result)
    activity.get_view.assert_called_once()
    with DbSession.use(readonly=False) as db:
        card.owner_user_id = user.id + 1
        card.created_by_user_id = card.owner_user_id
        db.update(card)
    with pytest.raises(ValidationError, match="CARD_UNAVAILABLE"):
        CardMcp.get_card_timeline(project.get_uid(), card.get_uid(), user, service)
    assert activity.get_view.call_count == 1
