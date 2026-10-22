"""Persisted native ownership projection and current mutation authority."""
from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import Bot, Card, CardAppOwnership
from sqlalchemy import event, update
from test_card_clones import clone
from test_card_mirrors import link


pytest_plugins = ["test_card_mirrors", "test_card_clones"]


def snapshot(b, monkeypatch, visible):
    service = b.service
    repo = service.repo
    monkeypatch.setattr(repo.card, "get_board_list", lambda *args, **kwargs: [(card, 0) for card in visible])
    monkeypatch.setattr(repo.card, "get_board_creators", lambda *args, **kwargs: {})
    repo.card_assigned_user = SimpleNamespace(get_all_by_project=lambda *a, **kw: [])
    repo.card_relationship = SimpleNamespace(get_all_by_project=lambda *a, **kw: [])
    repo.project_label = SimpleNamespace(get_all_card_labels_by_project=lambda *a, **kw: [])
    repo.checklist = SimpleNamespace(get_all_by_project=lambda *a, **kw: [])
    repo.project_assigned_user = SimpleNamespace(get_by_user_and_project=lambda *a: None)
    repo.user_card_read_state = SimpleNamespace(get_seen_seq_map=lambda *a: {})
    monkeypatch.setattr(service, "get_active_workers", lambda *a, **kw: {})
    monkeypatch.setattr(service, "get_work_states", lambda cards, **kw: {
        card.id: {"checklist_progress": {"total": 0, "completed": 0}} for card in cards
    })
    return service.get_board_snapshot(b.source_board, b.actor)[0]


def test_snapshot_projects_true_owner_not_bot_creator_and_queries_only_visible_ids(mirror_board, monkeypatch):
    b = mirror_board
    Bot.__table__.create(b.engine)
    with DbSession.use(readonly=False) as db:
        bot = Bot(name="Ordinary creator", bot_uname="ordinary", app_api_token="fixture-only",
                  platform="default", platform_running_type="default")
        db.insert(bot)
        b.source.created_by_user_id = None
        b.source.created_by_bot_id = bot.id
        db.update(b.source)
        app = Card(project_id=b.source_board.id, project_column_id=b.source.project_column_id, title="Owned")
        hidden = Card(project_id=b.source_board.id, project_column_id=b.source.project_column_id,
                      title="Hidden", visibility="PRIVATE", owner_user_id=b.outsider.id, created_by_user_id=b.outsider.id)
        db.insert(app)
        db.insert(hidden)
        db.insert(CardAppOwnership(card_id=app.id, app_key="private-app-key"))
        db.insert(CardAppOwnership(card_id=hidden.id, app_key="hidden-key"))
    queries = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "card_app_ownership" in statement.lower():
            queries.append((statement, parameters))
    event.listen(b.engine, "before_cursor_execute", capture)
    try:
        payloads = snapshot(b, monkeypatch, [b.source, app])
    finally:
        event.remove(b.engine, "before_cursor_execute", capture)
    assert [row["can_cross_board"] for row in payloads] == [True, False]
    assert len(queries) == 1
    assert set(queries[0][1]) == {int(b.source.id), int(app.id)}
    assert "private-app-key" not in str(payloads) and "hidden-key" not in str(payloads)
    assert hidden.get_uid() not in str(payloads)
    # The eligible bot-created original remains a native LINK source.
    assert link(b)[2] is True


@pytest.mark.parametrize("operation", ["link", "clone"])
def test_execution_rechecks_ownership_after_eligible_snapshot(clone_board, monkeypatch, operation):
    b = clone_board
    assert snapshot(b, monkeypatch, [b.source])[0]["can_cross_board"] is True
    with DbSession.use(readonly=False) as db:
        db.insert(CardAppOwnership(card_id=b.source.id, app_key="test-app"))
    assert snapshot(b, monkeypatch, [b.source])[0]["can_cross_board"] is False
    assert (link(b) if operation == "link" else clone(b)) is None
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(Card)).all()) == 1
    with b.engine.begin() as db:
        db.execute(update(CardAppOwnership).where(CardAppOwnership.card_id == b.source.id).values(app_key=None))
    assert snapshot(b, monkeypatch, [b.source])[0]["can_cross_board"] is True


def test_projection_batches_visible_cards_and_empty_input_issues_no_query(mirror_board):
    b = mirror_board
    cards = [Card(id=int(b.source.id) + offset, project_id=b.source_board.id,
                  project_column_id=b.source.project_column_id, title="Visible") for offset in range(401)]
    queries = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "card_app_ownership" in statement.lower():
            queries.append(parameters)
    event.listen(b.engine, "before_cursor_execute", capture)
    try:
        assert b.service.cross_board_eligibility([]) == {}
        assert queries == []
        assert all(b.service.cross_board_eligibility(cards).values())
    finally:
        event.remove(b.engine, "before_cursor_execute", capture)
    assert [len(params) for params in queries] == [200, 200, 1]


def test_detail_projection_tracks_current_ownership(mirror_board, monkeypatch):
    b = mirror_board
    snapshot(b, monkeypatch, [b.source])
    b.service.repo.card_comment = SimpleNamespace(count_by_card=lambda card: 0)
    monkeypatch.setattr(b.service, "is_check_card", lambda card: False)
    monkeypatch.setattr(b.service, "_get_completion_checklist", lambda card: None)
    empty_service = SimpleNamespace(is_employee=lambda *a: False, get_api_assigned_user_list=lambda *a: [], get_api_list_by_card=lambda *a, **kw: [])
    monkeypatch.setattr(b.service, "_get_service", lambda kind: empty_service)
    monkeypatch.setattr(b.service, "get_api_assigned_user_list", lambda *a, **kw: [])
    assert b.service.get_details(b.source_board, b.source, b.actor, include_description=False)["can_cross_board"] is True
    with DbSession.use(readonly=False) as db:
        db.insert(CardAppOwnership(card_id=b.source.id, app_key="secret-app-key"))
    details = b.service.get_details(b.source_board, b.source, b.actor, include_description=False)
    assert details["can_cross_board"] is False and "secret-app-key" not in str(details)


@pytest.mark.parametrize("visibility", ["SHARED", "INTERNAL", "PRIVATE"])
def test_committed_ownership_publishes_only_eligibility_with_native_visibility_routing(mirror_board, monkeypatch, visibility):
    from unittest.mock import Mock
    from langboard_shared.core.routing import SocketTopic
    from langboard_shared.domain.models import AppDefinition, CardAppOwnershipAudit
    from langboard_shared.domain.services import CardAppGovernance as governance
    from langboard_shared.publishers import CardPublisher
    b = mirror_board
    for model in (AppDefinition, CardAppOwnershipAudit):
        model.__table__.create(b.engine)
    with b.engine.begin() as db:
        db.execute(update(Card).where(Card.id == b.source.id).values(visibility=visibility, owner_user_id=b.outsider.id if visibility == "PRIVATE" else None))
    monkeypatch.setattr(governance, "require_current_project_policy", lambda *a: None)
    monkeypatch.setattr(governance, "APP_MANIFESTS", {"test-app": {}})
    published = Mock()
    monkeypatch.setattr(CardPublisher, "put_dispather", published)
    args = b.outsider, b.source_board.id, b.source.id
    assert governance.set_card_app_ownership(*args, "test-app", None)["changed"] is True
    data, route = published.call_args.args
    assert data == {"can_cross_board": False}
    assert route.card_uids == [b.source.get_uid()]
    assert route.topic == (SocketTopic.Board if visibility != "PRIVATE" else SocketTopic.UserPrivate)
    assert route.topic_id == (b.source_board.get_uid() if visibility != "PRIVATE" else b.outsider.get_uid())
    assert governance.set_card_app_ownership(*args, None, 1)["changed"] is True
    assert published.call_args.args[0] == {"can_cross_board": True}
    published.reset_mock()
    assert governance.set_card_app_ownership(*args, None, 2)["changed"] is False
    published.assert_not_called()
