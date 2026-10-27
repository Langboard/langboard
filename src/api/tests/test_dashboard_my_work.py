import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import orjson
import pytest


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard.mcp_tools.UserMcp import list_my_work  # noqa: E402
from langboard.routes.dashboard.DashboardApi import get_assigned_work, get_my_work  # noqa: E402
from langboard_shared.core.routing import ApiException  # noqa: E402
from langboard_shared.core.types import SnowflakeID  # noqa: E402


def test_my_work_limits_query_to_authorized_project() -> None:
    calls = []
    user = SimpleNamespace()
    service = SimpleNamespace(
        notification=SimpleNamespace(get_mentioned_card_ids=lambda _user: [7]),
        card=SimpleNamespace(
            resolve_work_visibility_contexts=Mock(return_value={12: object(), 24: object()}),
            get_my_work_context=lambda *args, **kwargs: calls.append((args, kwargs)) or {"cards": [{"uid": "card-1"}]},
        ),
    )

    response = get_my_work(request=SimpleNamespace(scope={}), project_uid=SnowflakeID(12).to_short_code(), limit=20, user=user, service=service)

    assert orjson.loads(response.body) == {"cards": [{"uid": "card-1"}]}
    args, kwargs = calls[0]
    assert args == (user, [7])
    assert kwargs["project_uid"] == SnowflakeID(12).to_short_code()
    assert kwargs["limit"] == 20
    assert kwargs["contexts"].keys() == {12, 24}


def test_my_work_rejects_project_outside_current_access() -> None:
    user = SimpleNamespace()
    service = SimpleNamespace(
        notification=SimpleNamespace(get_mentioned_card_ids=lambda _user: pytest.fail("notification lookup must not run")),
        card=SimpleNamespace(
            resolve_work_visibility_contexts=Mock(return_value={12: object()}),
            get_my_work_context=lambda *_args: pytest.fail("card query must not run"),
        ),
    )

    with pytest.raises(ApiException.NotFound_404):
        get_my_work(request=SimpleNamespace(scope={}), project_uid="revoked", limit=20, user=user, service=service)


def test_rest_and_mcp_assigned_work_share_query_and_cursor_contract() -> None:
    result = {"items": [{"card_uid": "card-1"}], "next_cursor": "next"}
    query = Mock(return_value=result)
    service = SimpleNamespace(card=SimpleNamespace(list_assigned_work=query))
    user = object()
    response = get_assigned_work(request=SimpleNamespace(scope={}), project_uid="board", cursor="prior", limit=7, user=user, service=service)
    assert orjson.loads(response.body) == list_my_work(user, service, "board", "prior", 7) == result
    assert query.call_args_list[0].kwargs["channel"].value == "api"
    assert query.call_args_list[1].kwargs["channel"].value == "mcp"


def test_assigned_work_invalid_query_is_a_client_error() -> None:
    def invalid(*_, **kwargs):
        raise ValueError("Invalid My Work cursor")
    service = SimpleNamespace(card=SimpleNamespace(list_assigned_work=invalid))
    with pytest.raises(ApiException.BadRequest_400):
        get_assigned_work(request=SimpleNamespace(scope={}), cursor="bad", limit=20, user=object(), service=service)


@pytest.fixture
def my_work_db(monkeypatch):
    from langboard_shared.core.db import EditorContentModel
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import (
        Card,
        CardAssignedUser,
        Project,
        ProjectAssignedUser,
        ProjectColumn,
        User,
        WorkflowStageDefinition,
    )
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
    from langboard_shared.domain.services.factory.CardService import CardService
    from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    url = os.getenv("LANGBOARD_SUMMARY_TEST_DATABASE_URL", "sqlite://")
    schema = None
    if url.startswith("postgresql"):
        admin = create_engine(url)
        schema = "my_work_summary_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={
            "options": f"-csearch_path={schema} -ctimezone=America/New_York -cstatement_timeout=10000"
        })
    else:
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        if schema:
            User.metadata.create_all(engine)
        else:
            for model in (Card, CardAssignedUser, Project, ProjectColumn, WorkflowStageDefinition):
                model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        now = SafeDateTime.fromisoformat("2026-10-23T03:00:00+00:00")
        monkeypatch.setattr(SafeDateTime, "now", classmethod(lambda cls, *_, **__: now))
        with Session(engine, expire_on_commit=False) as db:
            if schema:
                db.add_all([User(id=uid, firstname="Fixture", lastname="User", email=f"user{uid}@example.invalid",
                                 password="fixture", activated_at=now) for uid in (1, 2)])
                db.commit()
            for pid in (10, 11, 12):
                db.add(Project(id=pid, owner_id=2, title=f"Board {pid}"))
            for cid, stage in ((40, "active"), (41, "review"), (42, "closed"), (43, "waiting"), (44, "reference")):
                db.add(ProjectColumn(id=cid, project_id=10, name=f"Column {cid}", workflow_stage=stage))
                db.add(WorkflowStageDefinition(id=cid+100, key=stage, name=stage,
                    counts_as_completed=stage == "closed", active_queue_policy="exclude" if stage == "reference" else "include",
                    overdue_policy="suppress" if stage == "waiting" else "normal"))
            db.add(ProjectColumn(id=45, project_id=11, name="Other", workflow_stage="active"))
            db.add(ProjectColumn(id=46, project_id=12, name="Revoked", workflow_stage="active"))
            db.commit()
            if schema:
                db.add_all([ProjectAssignedUser(id=200+pid, project_id=pid, user_id=1) for pid in (10, 11, 12)])
                db.commit()
        user = User(id=1, firstname="Reader", lastname="Fixture", email="reader@example.invalid", password="fixture")
        context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, False, actor_user_id=1)
        contexts = {10: context, 11: context}
        service = CardService(lambda _: None, lambda _: None, SimpleNamespace(card=CardRepository(None, None)))
        monkeypatch.setattr(service, "resolve_work_visibility_contexts", lambda *_: contexts.copy())
        sequence = 1000
        def add(title, *, assigned=True, **values):
            nonlocal sequence
            sequence += 1
            defaults = dict(project_id=10, project_column_id=40, visibility="SHARED", updated_at=now,
                            description=EditorContentModel(content="BODY MUST NOT BE LOADED"))
            defaults.update(values)
            with Session(engine, expire_on_commit=False) as db:
                card = Card(id=sequence, title=title, **defaults)
                db.add(card)
                if assigned:
                    db.add(CardAssignedUser(id=sequence+10000, card_id=card.id, user_id=1,
                                            project_assigned_id=200+card.project_id if schema else 0))
                db.commit()
            return card
        statements = []
        event.listen(engine, "before_cursor_execute", lambda _, __, statement, *___: statements.append(statement))
        yield service, user, add, contexts, statements
    finally:
        engine.dispose()
        if schema:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def context_page(fixture, **kwargs):
    service, user, *_ = fixture
    return service.get_my_work_context(user, [], timezone_name="Asia/Seoul", **kwargs)


@pytest.mark.parametrize("order", ["updated", "priority"])
def test_context_counts_pages_acl_and_no_card_bodies(my_work_db, order):
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Card
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    service, user, add, contexts, statements = my_work_db
    for index in range(61):
        add(f"Assigned {index}")
    add("Review", project_column_id=41)
    add("Other board", project_id=11, project_column_id=45)
    add("Hidden private", visibility="PRIVATE", owner_user_id=2, created_by_user_id=2)
    add("Internal", visibility="INTERNAL")
    add("Revoked", project_id=12, project_column_id=46)
    add("Closed", project_column_id=42)
    add("Reference", project_column_id=44)
    add("Archived", archived_at=SafeDateTime.now())
    add("Deleted", deleted_at=SafeDateTime.now())
    add("Foreign column", project_column_id=45)
    add("Mirror", source_type="project_card", source_uid="source")
    statements.clear()
    loaded_cards = []
    def record_loaded_card(_session, instance):
        if isinstance(instance, Card):
            loaded_cards.append(instance.id)
    event.listen(Session, "loaded_as_persistent", record_loaded_card)
    try:
        page = context_page(my_work_db, order=order, limit=50)
    finally:
        event.remove(Session, "loaded_as_persistent", record_loaded_card)
    assert len(loaded_cards) == 51  # One bounded page plus its overflow probe.
    assert len(page["cards"]) == 50
    assert page["counts"]["assigned"] == 62
    assert page["counts"]["review"] == 1
    assert page["total"] == 63
    second = context_page(my_work_db, order=order, limit=50, cursor=page["next_cursor"])
    assert len(second["cards"]) == 13 and second["next_cursor"] is None
    assert len({card["uid"] for card in page["cards"] + second["cards"]}) == 63
    assert second["counts"] == page["counts"]
    review = context_page(my_work_db, order=order, group="review")
    assert [card["title"] for card in review["cards"]] == ["Review"]
    assert review["cards"][0]["group"] == "review"
    selected = context_page(my_work_db, order=order, project_uid=SnowflakeID(10).to_short_code())
    assert selected["total"] == 62
    selects = [sql.lower() for sql in statements if sql.lstrip().startswith(("SELECT", "WITH"))]
    assert selects and not any("card.description" in sql or "card.ai_description" in sql for sql in selects)
    contexts.pop(10)
    assert context_page(my_work_db, order=order)["total"] == 1
    with pytest.raises(ValueError, match="readable"):
        context_page(my_work_db, order=order, project_uid=SnowflakeID(10).to_short_code())


def test_context_local_day_boundaries_and_shared_group_predicates(my_work_db):
    from langboard_shared.core.types import SafeDateTime
    _, _, add, _, _ = my_work_db
    for title, deadline in (("Overdue", "2026-10-22T14:59:59Z"), ("Today start", "2026-10-22T15:00:00Z"),
                            ("Today elapsed", "2026-10-23T00:00:00Z"), ("Today end", "2026-10-23T14:59:59Z"),
                            ("Upcoming start", "2026-10-23T15:00:00Z"), ("Upcoming end", "2026-10-30T14:59:59Z"),
                            ("Beyond upcoming", "2026-10-30T15:00:00Z")):
        add(title, deadline_at=SafeDateTime.fromisoformat(deadline))
    add("Suppressed", project_column_id=43, deadline_at=SafeDateTime.fromisoformat("2026-10-21T00:00:00Z"))
    add("Created review", assigned=False, created_by_user_id=1, project_column_id=41)
    add("Created", assigned=False, created_by_user_id=1)
    mentioned = add("Mentioned", assigned=False)
    service, user, *_ = my_work_db
    page = service.get_my_work_context(user, [mentioned.id], timezone_name="Asia/Seoul")
    assert page["counts"] == {"overdue": 1, "today": 3, "upcoming": 2, "review": 1, "assigned": 2, "mentioned": 1, "created": 1}
    for group, total in page["counts"].items():
        filtered = service.get_my_work_context(user, [mentioned.id], timezone_name="Asia/Seoul", group=group)
        assert len(filtered["cards"]) == total
        assert all(card["group"] == group for card in filtered["cards"])
        assert filtered["counts"] == page["counts"]


@pytest.mark.parametrize("zone,as_of,hours", [("America/New_York", "2026-03-08T12:00:00Z", 23), ("America/New_York", "2026-11-01T12:00:00Z", 25)])
def test_context_dst_day_uses_calendar_boundaries(my_work_db, monkeypatch, zone, as_of, hours):
    from langboard_shared.core.types import SafeDateTime
    monkeypatch.setattr(SafeDateTime, "now", classmethod(lambda cls, *_, **__: SafeDateTime.fromisoformat(as_of)))
    service, user, *_ = my_work_db
    page = service.get_my_work_context(user, [], timezone_name=zone)
    assert (SafeDateTime.fromisoformat(page["day_end"]) - SafeDateTime.fromisoformat(page["day_start"])).total_seconds() == hours * 3600


@pytest.mark.parametrize("kwargs", [{"group": "invalid"}, {"order": "invalid"}, {"limit": 0}, {"limit": 51}, {"limit": True},
                                  {"cursor": "broken"}, {"cursor": "x" * 1025}, {"timezone_name": "Not/AZone"}])
def test_context_rejects_invalid_inputs(my_work_db, kwargs):
    service, user, *_ = my_work_db
    with pytest.raises(ValueError):
        service.get_my_work_context(user, [], **kwargs)


def test_context_cursor_bound_to_actor_scope_group_and_timezone(my_work_db):
    service, user, add, *_ = my_work_db
    add("First")
    add("Second")
    cursor = context_page(my_work_db, limit=1)["next_cursor"]
    for changes in ({"group": "assigned"}, {"project_uid": SnowflakeID(10).to_short_code()}, {"timezone_name": "UTC"}):
        with pytest.raises(ValueError, match="cursor"):
            service.get_my_work_context(user, [], cursor=cursor, **{"timezone_name": "Asia/Seoul", **changes})
    with pytest.raises(ValueError, match="cursor"):
        service.get_my_work_context(SimpleNamespace(id=SnowflakeID(2)), [], cursor=cursor, timezone_name="Asia/Seoul")


def test_context_native_http_contract_validates_before_query(my_work_db):
    import inspect
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    service, actor, add, *_ = my_work_db
    add("HTTP assigned")
    app = FastAPI()
    signature = inspect.signature(get_my_work)
    app.dependency_overrides[signature.parameters["user"].default.dependency] = lambda: actor
    app.dependency_overrides[signature.parameters["service"].default.dependency] = lambda: SimpleNamespace(
        card=service, notification=SimpleNamespace(get_mentioned_card_ids=lambda _: [])
    )
    app.get("/work")(get_my_work)
    with TestClient(app) as client:
        for params in ({"order": "invalid"}, {"group": "invalid"}, {"limit": 51}, {"limit": 0}, {"cursor": "x" * 1025}, {"timezone_name": "x" * 65}):
            assert client.get("/work", params=params).status_code == 422
        result = client.get("/work", params={"group": "assigned", "limit": 1, "timezone_name": "Asia/Seoul"})
        assert result.status_code == 200
        body = result.json()
        assert body["order"] == "updated"
        priority = client.get("/work", params={"order": "priority"})
        assert priority.status_code == 200 and priority.json()["order"] == "priority"
        assert body["counts"]["assigned"] == 1
        assert body["cards"][0]["title"] == "HTTP assigned"
        assert body["cards"][0]["group"] == "assigned"


def test_context_empty_group_and_exhausted_cursor_keep_population_counts(my_work_db):
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import Card
    from sqlalchemy import delete

    _, _, add, _, statements = my_work_db
    older = add("Older")
    add("Newer")
    empty_group = context_page(my_work_db, group="review")
    assert empty_group["cards"] == [] and empty_group["counts"]["assigned"] == 2
    first = context_page(my_work_db, limit=1)
    with DbEngine.get_main_engine().begin() as db:
        db.execute(delete(Card).where(Card.id == older.id))
    statements.clear()
    last = context_page(my_work_db, limit=1, cursor=first["next_cursor"])
    assert last["cards"] == [] and last["next_cursor"] is None
    assert last["total"] == last["counts"]["assigned"] == 1
    reads = [sql for sql in statements if sql.lstrip().startswith(("SELECT", "WITH"))]
    assert len(reads) == 1
    assert "LIMIT" in reads[0]
    assert "card.description" not in reads[0] and "card.ai_description" not in reads[0]


@pytest.mark.parametrize("order", ["updated", "priority"])
@pytest.mark.parametrize("mutation", ["insert", "delete"])
@pytest.mark.parametrize("group", [None, "created"])
def test_context_postgres_count_and_page_share_one_snapshot(my_work_db, mutation, group, order):
    """Commit another connection after the first read starts, before any later read."""
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Card
    from sqlalchemy import delete, event, text
    from sqlalchemy.orm import Session

    engine = DbEngine.get_main_engine()
    if engine.dialect.name != "postgresql":
        pytest.skip("Controlled concurrent snapshot proof requires PostgreSQL")
    _, user, add, _, statements = my_work_db
    original = add("Before concurrent commit", assigned=False, created_by_user_id=user.id)
    mutation_receipt = {}

    def commit_between_reads(reader, cursor, statement, parameters, context, many):
        if mutation_receipt or not statement.lstrip().startswith(("SELECT", "WITH")) or "FROM card" not in statement:
            return
        mutation_receipt["reader_pid"] = reader.connection.driver_connection.info.backend_pid
        with Session(engine) as writer:
            mutation_receipt["writer_pid"] = writer.scalar(text("SELECT pg_backend_pid()"))
            if mutation == "insert":
                writer.add(Card(
                    id=9000, title="After concurrent commit", project_id=10, project_column_id=40,
                    visibility="SHARED", created_by_user_id=user.id, updated_at=SafeDateTime.now(),
                ))
            else:
                writer.execute(delete(Card).where(Card.id == original.id))
            writer.commit()
        mutation_receipt["committed"] = True

    statements.clear()
    event.listen(engine, "after_cursor_execute", commit_between_reads)
    try:
        page = context_page(my_work_db, group=group, order=order)
    finally:
        event.remove(engine, "after_cursor_execute", commit_between_reads)
    observed = {"mutation": mutation, "group": group, "count": page["total"],
                "titles": [card["title"] for card in page["cards"]], **mutation_receipt}
    print(observed)
    assert mutation_receipt["committed"]
    assert mutation_receipt["reader_pid"] != mutation_receipt["writer_pid"]
    assert observed["titles"] == ["Before concurrent commit"], observed
    assert page["total"] == page["counts"]["created"] == 1
    refreshed = context_page(my_work_db, group=group, order=order)
    expected = 2 if mutation == "insert" else 0
    assert refreshed["total"] == len(refreshed["cards"]) == expected


def test_priority_pages_rank_deadlines_ties_nulls_and_current_authority(my_work_db):
    from datetime import datetime, timezone
    from langboard_shared.core.types import SafeDateTime

    service, user, add, contexts, statements = my_work_db
    rows = []
    rank = {key: index for index, key in enumerate(("overdue", "today", "review", "upcoming", "assigned", "mentioned", "created"))}
    def put(title, group, **kwargs):
        card = add(title, **kwargs)
        rows.append((card, group))
        return card

    # All equal rank/deadline rows exceed a page, including both project and card ties.
    for index in range(57):
        put(f"Null assigned {index}", "assigned", project_id=11 if index % 2 else 10,
            project_column_id=45 if index % 2 else 40)
    put("Older update", "assigned", updated_at=SafeDateTime.fromisoformat("2026-10-22T00:00:00Z"))
    put("Assigned future", "assigned", deadline_at=SafeDateTime.fromisoformat("2026-11-01T00:00:00Z"))
    put("Assigned earlier future", "assigned", deadline_at=SafeDateTime.fromisoformat("2026-10-31T00:00:00Z"))
    put("Review null", "review", project_column_id=41)
    put("Review future", "review", project_column_id=41, deadline_at=SafeDateTime.fromisoformat("2026-11-01T00:00:00Z"))
    put("Upcoming", "upcoming", deadline_at=SafeDateTime.fromisoformat("2026-10-24T00:00:00Z"))
    put("Today", "today", deadline_at=SafeDateTime.fromisoformat("2026-10-23T00:00:00Z"))
    for index in range(9):
        put(f"Overdue tied {index}", "overdue", deadline_at=SafeDateTime.fromisoformat("2026-10-22T00:00:00Z"))
    put("Overdue later", "overdue", deadline_at=SafeDateTime.fromisoformat("2026-10-22T00:00:00Z"))
    put("Overdue earlier", "overdue", deadline_at=SafeDateTime.fromisoformat("2026-10-21T00:00:00Z"))
    mentioned = put("Mentioned", "mentioned", assigned=False)
    put("Created", "created", assigned=False, created_by_user_id=user.id)
    add("Hidden", visibility="INTERNAL")
    add("Revoked", project_id=12, project_column_id=46)

    def key(row):
        card, group = row
        deadline = card.deadline_at or datetime.max.replace(tzinfo=timezone.utc)
        return rank[group], deadline, -card.updated_at.timestamp(), -int(card.project_id), -int(card.id)

    expected = [card.get_uid() for card, _ in sorted(rows, key=key)]
    actual = []
    cursor = None
    pages = []
    while True:
        statements.clear()
        page = service.get_my_work_context(user, [mentioned.id], order="priority", limit=7,
                                           timezone_name="Asia/Seoul", cursor=cursor)
        pages.append(page)
        assert page["order"] == "priority" and page["total"] == len(rows)
        reads = [sql for sql in statements if sql.lstrip().startswith(("SELECT", "WITH"))]
        assert len(reads) == 1 and "LIMIT" in reads[0]
        assert "card.description" not in reads[0] and "card.ai_description" not in reads[0]
        actual.extend(card["uid"] for card in page["cards"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(pages) > 2 and actual == expected and len(set(actual)) == len(rows)
    for group in rank:
        filtered = service.get_my_work_context(user, [mentioned.id], order="priority", group=group,
                                              timezone_name="Asia/Seoul")
        expected_group = [card.get_uid() for card, card_group in sorted(rows, key=key) if card_group == group]
        assert [card["uid"] for card in filtered["cards"]] == expected_group[:50]
        assert filtered["counts"] == pages[0]["counts"]
    contexts.pop(11)
    revoked_page = service.get_my_work_context(user, [mentioned.id], order="priority", limit=50,
                                              timezone_name="Asia/Seoul", cursor=pages[0]["next_cursor"])
    assert revoked_page["total"] == sum(int(card.project_id) == 10 for card, _ in rows)
    assert all(card["project_uid"] == SnowflakeID(10).to_short_code() for card in revoked_page["cards"])


def test_priority_cursor_mode_complete_tuple_validation_and_legacy_updated(my_work_db):
    import base64
    import json

    _, _, add, *_ = my_work_db
    for index in range(3):
        add(str(index))
    updated = context_page(my_work_db, limit=1)
    assert updated == context_page(my_work_db, order="updated", limit=1)
    assert updated["order"] == "updated"
    assert [card["title"] for card in context_page(my_work_db, cursor=updated["next_cursor"])["cards"]] == ["1", "0"]
    priority = context_page(my_work_db, order="priority", limit=1)
    for cursor, order in ((updated["next_cursor"], "priority"), (priority["next_cursor"], "updated")):
        with pytest.raises(ValueError, match="cursor"):
            context_page(my_work_db, cursor=cursor, order=order)
    payload = json.loads(base64.urlsafe_b64decode(priority["next_cursor"] + "=" * (-len(priority["next_cursor"]) % 4)))
    assert payload["context"][-1] == "priority"
    assert payload["before"][:2] == [4, None]
    for before in ([True, *payload["before"][1:]], [7, *payload["before"][1:]],
                   [4, "2026-10-23T00:00:00", *payload["before"][2:]], payload["before"][2:]):
        invalid = base64.urlsafe_b64encode(json.dumps({**payload, "before": before}).encode()).decode()
        with pytest.raises(ValueError, match="cursor"):
            context_page(my_work_db, cursor=invalid, order="priority")
