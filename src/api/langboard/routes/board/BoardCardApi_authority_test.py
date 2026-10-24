"""Registered card reads and mutations enforce current native authority."""

import asyncio
import pytest
from langboard.mcp_tools import CardMcp
from langboard.mcp_tools.RoleChecker import McpRoleChecker
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board import BoardCardApi  # noqa: F401 - register the real HTTP routes
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import ProjectRole, User
from langboard_shared.security import RoleSecurity
from src.api.tests.card_cross_board.test_card_mirrors import mirror_board as mirror_board


@pytest.mark.parametrize(
    "method,path,actions,expected",
    [
        ("POST", "/board/project-a/card", ["card_write"], 200),
        ("POST", "/board/project-a/card", ["read", "card_write"], 200),
        ("POST", "/board/project-a/card", ["card_update"], 403),
        ("POST", "/board/project-a/card", ["read"], 403),
        ("POST", "/board/project-a/card", [], 403),
        ("POST", "/board/project-a/card", ["*"], 200),
        ("PUT", "/board/project-a/card/card-a/details", ["card_update"], 200),
        ("PUT", "/board/project-a/card/card-a/details", ["card_write"], 403),
        ("PUT", "/board/project-a/card/card-a/details", ["read"], 403),
        ("PUT", "/board/project-a/card/card-a/content-blocks", ["card_update"], 200),
        ("PUT", "/board/project-a/card/card-a/content-blocks", ["card_write"], 403),
        ("PUT", "/board/project-a/card/card-a/content-blocks", ["read"], 403),
    ],
)
def test_http_card_authority(monkeypatch, method, path, actions, expected):
    checks = []
    role = ProjectRole(actions=actions)

    def decision(self, user_id, path_params, required, finder):
        checks.append((self._model_class, user_id, path_params))
        return role.is_granted(required)

    monkeypatch.setattr(RoleSecurity, "is_authorized", decision)
    actor = User(
        id=SnowflakeID(1), firstname="External", lastname="Test", email="card@example.invalid", password="test-only"
    )
    status, reached = _request(method, path, actor)
    assert status == expected
    assert reached is (expected == 200)
    assert len(checks) == 1
    assert checks[0][:2] == (ProjectRole, actor.id)
    assert checks[0][2]["project_uid"] == "project-a"


def test_card_creation_without_identity_never_reaches_command():
    assert _request("POST", "/board/project-a/card", None) == (401, False)


@pytest.mark.parametrize("handler", [CardMcp.create_card, CardMcp.create_card_in_leftmost_column])
@pytest.mark.parametrize(
    "actions,expected", [(["card_write"], True), (["card_update"], False), (["read"], False), (["*"], True)]
)
def test_mcp_creation_uses_the_same_authority(monkeypatch, handler, actions, expected):
    role = ProjectRole(actions=actions)
    checks = []

    def decision(self, user_id, arguments, required, finder):
        checks.append((self._model_class, arguments))
        return role.is_granted(required)

    monkeypatch.setattr(RoleSecurity, "is_authorized", decision)
    actor = User(
        id=SnowflakeID(1), firstname="External", lastname="Test", email="card@example.invalid", password="test-only"
    )
    assert McpRoleChecker(None).check_permission(handler, actor, {"project_uid": "project-a"}) is expected
    assert checks == [(ProjectRole, {"project_uid": "project-a"})]


def _request(method, path, actor):
    messages = []
    reached = False

    async def terminal(scope, receive, send):
        nonlocal reached
        reached = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "method": method, "path": path, "root_path": "", "headers": [], "query_string": b""}
    if actor is not None:
        scope["auth"] = actor
    asyncio.run(RoleMiddleware(terminal, AppRouter.api.routes)(scope, receive, send))
    return next(message["status"] for message in messages if message["type"] == "http.response.start"), reached


@pytest.mark.parametrize(
    "selection", [None, "checklists", "description", "execution_receipts", "attachments,linked_wikis", "agent_execution"]
)
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("actions", [["read"], ["read", "update"], ["*"]])
def test_details_selects_only_requested_sections_after_current_authority(monkeypatch, selection, linked, actions):
    import json
    from types import SimpleNamespace
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext
    from starlette.requests import Request

    reads = []
    actor = User(
        id=SnowflakeID(1), firstname="External", lastname="Test", email="card@example.invalid", password="test-only"
    )
    card = SimpleNamespace(id=SnowflakeID(3), visibility="SHARED", is_linked_resource=linked)
    project = SimpleNamespace(id=SnowflakeID(2), get_uid=lambda: "project-a")
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, True, actor_user_id=1)

    def read(name, result=None):
        def get(*args, **kwargs):
            reads.append((name, kwargs))
            return [] if result is None else result

        return get

    service = SimpleNamespace(
        card=SimpleNamespace(
            resolve_canonical_card=read("resolve", (project, card, context)),
            get_details=read("details", {"uid": "card-a"}),
            can_delete=read("can_delete", False),
            get_api_bot_scope_list=read("bot_scopes"),
        ),
        project=SimpleNamespace(get_user_role_actions_by_project=read("authority", actions)),
        checklist=SimpleNamespace(get_api_list_by_card=read("checklists")),
        card_attachment=SimpleNamespace(get_api_list_by_card=read("attachments")),
        app_setting=SimpleNamespace(get_api_global_relationship_list=read("global_relationships")),
        project_column=SimpleNamespace(get_api_list_by_project=read("project_columns")),
        project_label=SimpleNamespace(get_api_list_by_project=read("project_labels")),
    )
    monkeypatch.setattr(BoardCardApi, "visible_linked_wikis", read("linked_wikis"))
    monkeypatch.setattr(BoardCardApi, "receipt_history", read("execution_receipts"))
    monkeypatch.setattr(BoardCardApi, "card_agent_execution", read("agent_execution"))
    request = Request(
        {
            "type": "http",
            "query_string": b"" if selection is None else f"sections={selection}".encode(),
            "collaboration_channel": CollaborationChannel.HumanUI,
        }
    )
    result = json.loads(BoardCardApi.get_card_details("project-a", "card-a", request, actor, service).body)
    expected = {
        "checklists",
        "attachments",
        "linked_wikis",
        "global_relationships",
        "project_columns",
        "project_labels",
        "bot_scopes",
        "execution_receipts",
    }
    if selection is not None:
        expected = (expected | {"agent_execution"}) & set(selection.split(","))
    assert set(result) == expected | {"card", "whispers_allowed"}
    assert reads[0] == ("resolve", {"metadata_only": selection is not None and "description" not in selection.split(",")})
    assert dict(reads)["details"]["include_description"] is (selection is None or "description" in selection.split(","))
    for name in {
        "checklists",
        "attachments",
        "linked_wikis",
        "global_relationships",
        "project_columns",
        "project_labels",
        "bot_scopes",
        "execution_receipts",
        "agent_execution",
    }:
        should_read = name in expected and (not linked or name in {"project_columns", "execution_receipts"})
        if name == "bot_scopes":
            should_read = should_read and ("*" in actions or "update" in actions)
        assert any(read[0] == name for read in reads) is should_read
    assert result["card"]["current_auth_role_actions"] == actions


@pytest.mark.parametrize("selection", ["", "unknown", "description,unknown", "description,", "description" * 30])
def test_details_rejects_unbounded_or_unknown_sections(monkeypatch, selection):
    from types import SimpleNamespace
    from starlette.requests import Request

    service = SimpleNamespace(
        card=SimpleNamespace(
            resolve_canonical_card=lambda *args, **kwargs: (
                SimpleNamespace(get_uid=lambda: "project-a"),
                None,
                None,
            )
        )
    )
    with pytest.raises(Exception) as error:
        BoardCardApi.get_card_details(
            "project-a",
            "card-a",
            Request({"type": "http", "query_string": f"sections={selection}".encode()}),
            None,
            service,
        )
    assert error.value.status_code == 400


def test_details_hidden_card_stops_before_any_section_read():
    from types import SimpleNamespace
    from starlette.requests import Request

    service = SimpleNamespace(card=SimpleNamespace(resolve_canonical_card=lambda *args, **kwargs: None))
    with pytest.raises(Exception) as error:
        BoardCardApi.get_card_details(
            "project-a", "hidden", Request({"type": "http", "query_string": b"sections=description"}), None, service
        )
    assert error.value.status_code == 404


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("include_description", [None, False, True])
@pytest.mark.parametrize("app_owned", [False, True])
def test_real_details_service_skips_inactive_body_and_preserves_default(
    monkeypatch,
    mirror_board,  # noqa: F811 - native persisted fixture
    linked,
    include_description,
    app_owned,
):
    from types import SimpleNamespace
    from langboard_shared.core.db import DbSession, EditorContentModel
    from langboard_shared.domain.models import Card, CardAppOwnership
    from langboard_shared.domain.services.factory.CardService import CardService
    from langboard_shared.helpers import InfraHelper

    reads = []
    card = Card(
        id=mirror_board.source.id,
        project_id=mirror_board.source_board.id,
        project_column_id=SnowflakeID(4),
        title="Body card",
        visibility="SHARED",
        description=EditorContentModel(content="Legacy body"),
        ai_description="Legacy AI body",
        source_type="project_wiki" if linked else None,
        source_uid="wiki-a" if linked else None,
    )
    if app_owned:
        with DbSession.atomic() as db:
            db.insert(CardAppOwnership(card_id=card.id, app_key="owned-app"))
    service = object.__new__(CardService)
    monkeypatch.setattr(CardService, "resolve_readable_card", lambda *args, **kwargs: (SimpleNamespace(id=2), card, None))
    monkeypatch.setattr(
        CardService,
        "get_work_states",
        lambda *args, **kwargs: {card.id: {"checklist_progress": {"total": 0, "completed": 0}}},
    )
    monkeypatch.setattr(InfraHelper, "get_by_id_like", lambda *args: SimpleNamespace(name="Doing"))
    monkeypatch.setattr(
        CardService,
        "_get_linked_resource_payloads",
        lambda *args, **kwargs: reads.append(("wiki", kwargs["include_content"]))
        or {card.get_uid(): {"status": "available"}},
    )
    monkeypatch.setattr(CardService, "is_check_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(CardService, "_get_completion_checklist", lambda *args: None)
    monkeypatch.setattr(CardService, "get_api_assigned_user_list", lambda *args, **kwargs: [])
    monkeypatch.setattr(CardService, "get_active_workers", lambda *args: {})
    helper = SimpleNamespace(
        get_api_assigned_user_list=lambda *args: [],
        get_api_list_by_card=lambda *args, **kwargs: [],
        api_blocks_by_card=lambda *args: reads.append(("blocks", True)) or [{"block_uid": "body"}],
    )
    monkeypatch.setattr(CardService, "_get_service", lambda *args: helper)
    # Counting comments is core metadata; body lookup is separately observable.
    service.repo = SimpleNamespace(card_comment=SimpleNamespace(count_by_card=lambda *args: 0))
    kwargs = {} if include_description is None else {"include_description": include_description}
    result = service.get_details(None, None, None, **kwargs)
    expected = include_description is not False
    assert result["can_cross_board"] is (not linked and not app_owned)
    assert ("description" in result) is expected
    assert ("ai_description" in result) is expected
    assert reads == ([("wiki", expected)] if linked else [("blocks", True)] if expected else [])
    assert ("content_blocks" in result) is (expected and not linked)


@pytest.mark.parametrize("linked", [False, True])
def test_details_preserves_bot_scope_gate(linked):
    import json
    from types import SimpleNamespace
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
    from langboard_shared.domain.models import Bot
    from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext
    from starlette.requests import Request

    reads = []
    actor = Bot(
        id=SnowflakeID(1),
        name="Scope bot",
        bot_uname="bot-test",
        app_api_token="test-only",
        platform="default",
        platform_running_type="default",
    )
    card = SimpleNamespace(id=3, is_linked_resource=linked, visibility="SHARED")
    context = CardVisibilityContext(CollaborationChannel.Api, True, True, True)
    service = SimpleNamespace(
        card=SimpleNamespace(
            resolve_canonical_card=lambda *args, **kwargs: (SimpleNamespace(get_uid=lambda: "project-a"), card, context),
            get_details=lambda *args, **kwargs: {"uid": "card-a"},
            can_delete=lambda *args: False,
            get_api_bot_scope_list=lambda *args: reads.append("scopes") or [],
        )
    )
    response = json.loads(
        BoardCardApi.get_card_details(
            "project-a", "card-a", Request({"type": "http", "query_string": b"sections=bot_scopes"}), actor, service
        ).body
    )
    assert reads == ([] if linked else ["scopes"])
    assert response["card"]["can_change_visibility"] is False
    assert response["bot_scopes"] == []


@pytest.fixture
def canonical_http(mirror_board):  # noqa: F811 - native persisted fixture
    from types import SimpleNamespace
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    b = mirror_board
    route = next(route for route in AppRouter.api.routes if route.endpoint is BoardCardApi.get_card_details)
    app = FastAPI()
    app.include_router(APIRouter(routes=[route]))
    app.add_middleware(RoleMiddleware, routes=[route])
    # Supply an authenticated native actor and service; authorization stays in
    # the actual canonical resolver and its current primary-database policy.
    for dependency in route.dependant.dependencies:
        if dependency.name == "user_or_bot":
            app.dependency_overrides[dependency.call] = lambda: b.actor
        elif dependency.name == "service":
            app.dependency_overrides[dependency.call] = lambda: SimpleNamespace(card=b.service)
    with TestClient(app) as client:
        yield b, client


def canonical_path(b, project=None):
    return f"/board/{(project or b.source_board).get_uid()}/card/{b.source.get_uid()}?location_only=true"


@pytest.mark.parametrize("actions,expected", [(["read"], 200), (["card_write"], 404), ([], 404), (["*"], 200)])
def test_registered_get_requires_current_native_read_grant(canonical_http, actions, expected):
    from langboard_shared.core.db import DbSession
    from sqlalchemy import update

    b, client = canonical_http
    with DbSession.atomic() as db:
        db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=actions))
    response = client.get(canonical_path(b))
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    if expected == 200:
        assert response.json() == {"canonical_project_uid": b.source_board.get_uid(), "card_uid": b.source.get_uid()}
    else:
        assert b.source_board.get_uid() not in response.text
        assert b.source.title not in response.text
        assert b.source.description.content not in response.text


@pytest.mark.parametrize("revocation", ["read", "membership", "account", "card_deleted", "project_deleted", "private"])
def test_registered_get_rechecks_current_audience_after_a_successful_read(canonical_http, revocation):
    from langboard_shared.core.db import DbSession
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Card, Project, ProjectAssignedUser
    from sqlalchemy import delete, update

    b, client = canonical_http
    assert client.get(canonical_path(b)).status_code == 200
    with DbSession.atomic() as db:
        if revocation == "read":
            db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=[]))
        elif revocation == "membership":
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == b.source_board.id))
        elif revocation == "account":
            db.exec(update(User).where(User.id == b.actor.id).values(activated_at=None))
        elif revocation == "card_deleted":
            db.exec(update(Card).where(Card.id == b.source.id).values(deleted_at=SafeDateTime.now()))
        elif revocation == "project_deleted":
            db.exec(update(Project).where(Project.id == b.source_board.id).values(deleted_at=SafeDateTime.now()))
        else:
            db.exec(
                update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id)
            )
    denied = client.get(canonical_path(b))
    assert denied.status_code == 404
    assert denied.headers["cache-control"] == "no-store"
    assert b.source_board.get_uid() not in denied.text
    assert b.source.title not in denied.text
    assert b.source.description.content not in denied.text


def test_registered_get_does_not_reveal_current_location_for_unrelated_project(canonical_http):
    b, client = canonical_http
    assert client.get(canonical_path(b)).status_code == 200
    denied = client.get(canonical_path(b, b.destination))
    assert denied.status_code == 404
    assert denied.headers["cache-control"] == "no-store"
    assert b.source_board.get_uid() not in denied.text


def test_native_mirror_retains_destination_read_but_hides_revoked_source(canonical_http):
    from langboard_shared.core.db import DbSession
    from sqlalchemy import update

    b, client = canonical_http
    mirror, _, _ = b.service.link_cross_board_card(b.actor, b.source_board, b.source, b.destination, b.column)
    payload = b.service._get_linked_resource_payloads(b.actor, b.destination, [mirror], include_content=True)[
        mirror.get_uid()
    ]
    assert payload["status"] == "available" and payload["title"] == b.source.title
    with DbSession.atomic() as db:
        db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=[]))
    assert client.get(canonical_path(b)).status_code == 404
    mirror_response = client.get(f"/board/{b.destination.get_uid()}/card/{mirror.get_uid()}?location_only=true")
    assert mirror_response.status_code == 200
    hidden = b.service._get_linked_resource_payloads(b.actor, b.destination, [mirror], include_content=True)[
        mirror.get_uid()
    ]
    assert hidden == {"type": "project_card", "uid": b.source.get_uid(), "status": "forbidden"}
    assert b.source.title not in str(hidden) and b.source.description.content not in str(hidden)


def test_registered_app_owned_card_read_does_not_grant_cross_board_link(canonical_http):
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.domain.models import Card, CardAppOwnership

    b, client = canonical_http
    with DbSession.atomic() as db:
        db.insert(CardAppOwnership(card_id=b.source.id, app_key="owned-app"))
    assert client.get(canonical_path(b)).status_code == 200
    assert b.service.cross_board_eligibility([b.source]) == {b.source.id: False}
    assert b.service.link_cross_board_card(b.actor, b.source_board, b.source, b.destination, b.column) is None
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(Card)).all()) == 1
