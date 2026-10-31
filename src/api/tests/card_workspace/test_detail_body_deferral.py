"""Native HTTP detail reads defer bodies while preserving authority and projections.

Checkbox eligibility still inspects editor text in SQL and returns one boolean.
Linked project-card payloads defer the original body when content is omitted.
"""

from types import SimpleNamespace
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board import BoardCardApi
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import Card, Checklist, ProjectActivity, ProjectRole, User
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from sqlalchemy import event, inspect, text, update
from src.api.tests.card_cross_board.test_card_mirrors import mirror_board as mirror_board


pytestmark = pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)


@pytest.fixture
def detail_http(mirror_board, monkeypatch, tmp_path):  # noqa: F811 - persisted native fixture
    b = mirror_board
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path / "data"))
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    # Exercise the full native service graph, including work state and metadata.
    User.metadata.create_all(b.engine)
    with b.engine.begin() as db:
        for ddl in (
            "CREATE TABLE card_execution_generation (card_id BIGINT PRIMARY KEY, execution_generation INTEGER NOT NULL)",
            "CREATE TABLE execution_receipt (card_id BIGINT, execution_generation INTEGER, idempotency_key TEXT, payload_json JSON, created_at TIMESTAMP)",
            "CREATE TABLE execution_checklist_projection (card_id BIGINT, execution_generation INTEGER, item_uid TEXT, evidence_kind TEXT, evidence_refs JSON, is_checked BOOLEAN)",
        ):
            db.execute(text(ddl))
    native = DomainService()
    b.service = native.card
    route = next(route for route in AppRouter.api.routes if route.endpoint is BoardCardApi.get_card_details)
    app = FastAPI()
    app.include_router(APIRouter(routes=[route]))
    app.add_middleware(RoleMiddleware, routes=[route])
    for dependency in route.dependant.dependencies:
        if dependency.name == "user_or_bot":
            app.dependency_overrides[dependency.call] = lambda: b.actor
        elif dependency.name == "service":
            app.dependency_overrides[dependency.call] = lambda: native
    statements, body_results, selected = [], [], []

    def capture(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
            if "FROM card " in statement or "FROM card\n" in statement:
                selected.append([column[0] for column in cursor.description or ()])

    body_type = type(Card.__table__.c.description.type)
    original = body_type.process_result_value

    def body_result(self, value, dialect):
        body_results.append(value)
        return original(self, value, dialect)

    monkeypatch.setattr(body_type, "process_result_value", body_result)
    event.listen(b.engine, "after_cursor_execute", capture)
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(
                **vars(b), client=client, statements=statements, selected=selected, body_results=body_results,
            )
    finally:
        event.remove(b.engine, "after_cursor_execute", capture)
        native.close()


def path(b, query="sections=attachments", *, project=None, card=None):
    return f"/board/{(project or b.source_board).get_uid()}/card/{card or b.source.get_uid()}?{query}"


def no_body(b):
    assert b.body_results == []
    assert not any({"description", "ai_description"} & set(columns) for columns in b.selected)
    assert not any("app_management" in sql or "app_definition" in sql for sql in b.statements)


@pytest.mark.parametrize("selection", ["attachments", "checklists", "execution_receipts", "agent_execution"])
def test_actual_detail_defers_direct_body_and_keeps_metadata(detail_http, selection):
    b = detail_http
    response = b.client.get(path(b, f"sections={selection}"))
    assert response.status_code == 200, response.text
    card = response.json()["card"]
    assert card["title"] == b.source.title
    assert card["is_check_card"] is False
    assert "description" not in card and "ai_description" not in card
    assert set(response.json()) == {"card", "whispers_allowed", selection}
    no_body(b)
    print({"dialect": b.engine.dialect.name, "selection": selection,
           "selects": len(b.statements), "card_selects": len(b.selected),
           "body_materializations": len(b.body_results)})
    assert sum("FROM card " in sql or "FROM card\n" in sql for sql in b.statements) <= 5


@pytest.mark.parametrize("query", ["", "sections=description"])
def test_legacy_and_explicit_description_keep_full_body(detail_http, query):
    b = detail_http
    response = b.client.get(path(b, query))
    assert response.status_code == 200, response.text
    card = response.json()["card"]
    assert card["description"]["content"] == b.source.description.content
    assert "ai_description" in card
    print({"dialect": b.engine.dialect.name, "selection": query or "legacy",
           "selects": len(b.statements), "card_selects": len(b.selected),
           "body_materializations": len(b.body_results)})
    assert len(b.body_results) >= 2
    assert any({"description", "ai_description"} <= set(columns) for columns in b.selected)


@pytest.mark.parametrize("query", ["location_only=true", "sections=attachments"])
@pytest.mark.parametrize("state", ["ok", "missing", "denied", "private", "moved", "unrelated"])
def test_location_authority_and_historical_redirect_defer_body(detail_http, state, query):
    b = detail_http
    project, card = b.source_board, None
    with DbSession.atomic() as db:
        if state == "denied":
            db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=[]))
        elif state == "private":
            db.exec(update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id))
        elif state == "missing":
            card = SnowflakeID(123).to_short_code()
        elif state == "moved":
            db.exec(update(Card).where(Card.id == b.source.id).values(
                project_id=b.destination.id, project_column_id=b.column.id,
            ))
            db.insert(ProjectActivity(
                project_id=b.destination.id, card_id=b.source.id, user_id=b.actor.id,
                activity_type=ProjectActivityType.CardMoved,
                activity_history={"source": {"project_uid": b.source_board.get_uid()}},
            ))
        elif state == "unrelated":
            project = b.destination
    response = b.client.get(path(b, query, project=project, card=card))
    assert response.status_code == (200 if state in {"ok", "moved"} else 404), response.text
    if state != "ok" or query == "location_only=true":
        assert response.headers["cache-control"] == "no-store"
    if response.status_code == 200 and (state == "moved" or query == "location_only=true"):
        assert response.json() == {
            "canonical_project_uid": (b.destination if state == "moved" else b.source_board).get_uid(),
            "card_uid": b.source.get_uid(),
        }
    elif response.status_code != 200:
        assert b.source.title not in response.text
        assert b.source.description.content not in response.text
    no_body(b)


@pytest.mark.parametrize("body", ["", " \t\n\x1c\x85\xa0\u1680\u2003\u2028\u202f\u205f\u3000", "  text  ", "\x00", "\\n", ' {"content":""} '])
@pytest.mark.parametrize("deadline,user_checklist", [(False, False), (True, False), (False, True)])
def test_metadata_checkbox_matches_full_body_strip_semantics(detail_http, body, deadline, user_checklist):
    b = detail_http
    with DbSession.atomic() as db:
        db.exec(update(Card).where(Card.id == b.source.id).values(
            description=EditorContentModel(content=body),
            deadline_at=SafeDateTime.now() if deadline else None,
        ))
        if user_checklist:
            db.insert(Checklist(card_id=b.source.id, title="User", is_system=False))
    response = b.client.get(path(b))
    assert response.status_code == 200, response.text
    assert response.json()["card"]["is_check_card"] is ((deadline or not body.strip()) and not user_checklist)
    no_body(b)


def test_metadata_resolver_is_opt_in_and_deferred_fields_raise(detail_http):
    b = detail_http
    resolved = b.service.resolve_canonical_card(b.source_board, b.source, b.actor, metadata_only=True)
    assert resolved is not None
    assert {"description", "ai_description"} <= inspect(resolved[1]).unloaded
    with pytest.raises(Exception, match="(raiseload|not bound to a Session)"):
        _ = resolved[1].description
    no_body(b)
    default = b.service.resolve_canonical_card(b.source_board, b.source, b.actor)
    assert default[1].description.content == b.source.description.content
    assert b.body_results


@pytest.mark.parametrize("include_description", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
def test_linked_original_defers_unrequested_body_and_keeps_privacy(detail_http, revoked, include_description):
    b = detail_http
    with DbSession.atomic() as db:
        mirror = Card(
            project_id=b.destination.id, project_column_id=b.column.id, title="", visibility="SHARED",
            source_type=Card.LINKED_RESOURCE_PROJECT_CARD, source_uid=b.source.get_uid(),
        )
        db.insert(mirror)
        if revoked:
            db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=[]))
    response = b.client.get(path(
        b, "sections=description" if include_description else "sections=attachments",
        project=b.destination, card=mirror.get_uid(),
    ))
    assert response.status_code == 200, response.text
    resource = response.json()["card"]["linked_resource"]
    assert resource["status"] == ("forbidden" if revoked else "available")
    assert ("description" in response.json()["card"]) is include_description
    assert ("content" in resource) is (include_description and not revoked)
    assert ("preview" in resource) is (include_description and not revoked)
    assert len(b.body_results) == (3 if include_description else 0)
    if not include_description:
        no_body(b)
    if include_description and not revoked:
        assert resource["content"]["content"] == b.source.description.content
        assert resource["preview"] == b.source.description.content
    if revoked:
        assert b.source.title not in response.text and b.source.description.content not in response.text
    else:
        assert resource["title"] == b.source.title
