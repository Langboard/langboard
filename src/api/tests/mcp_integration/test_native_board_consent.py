"""Persistent native client consent intersects primary board authority at SQL boundary."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastmcp.exceptions import AuthorizationError
from langboard.mcp_integration import OAuth
from langboard.mcp_integration.NativeConsent import native_call_boundary
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardComment,
    CardRelationship,
    DocumentKeywordIndex,
    EmployeeMembershipPolicy,
    GlobalCardRelationshipType,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    ProjectWiki,
    User,
    UserIdentityLink,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.NativeMcpBoundary import NativeMcpDenied, native_mcp_boundary
from langboard_shared.domain.services.NativeMcpConsent import (
    connect_native_client,
    effective_board_ids,
    list_native_connections,
    positively_internal,
    read_native_board_consent,
    save_native_board_consent,
)
from langboard_shared.Env import Env
from sqlalchemy import create_engine, func, insert, select, text, update
from sqlalchemy.orm import Session, aliased
from sqlalchemy.pool import StaticPool


@pytest.fixture
def native(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (
        User,
        Project,
        ProjectAssignedUser,
        ProjectRole,
        ProjectColumn,
        Card,
        CardComment,
        ProjectWiki,
        GlobalCardRelationshipType,
        CardRelationship,
        EmployeeMembershipPolicy,
        UserIdentityLink,
    ):
        model.__table__.create(engine)
    migration = importlib.import_module("langboard.migrations.versions.20261025010000-efbc567d8901")
    with engine.begin() as db:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
        migration.upgrade()
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: []))
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            id=1,
            firstname="External",
            lastname="User",
            email="external@example.invalid",
            password="test",
            activated_at=SafeDateTime.now(),
        )
        other = User(
            id=2,
            firstname="Other",
            lastname="User",
            email="other@example.invalid",
            password="test",
            activated_at=SafeDateTime.now(),
        )
        boards = [Project(id=i, title=f"Board {i}", owner_id=2) for i in (10, 20)]
        db.add_all([user, other, *boards])
        db.add_all([ProjectAssignedUser(id=i + 30, project_id=i, user_id=1) for i in (10, 20)])
        db.add_all([ProjectRole(id=i + 60, project_id=i, user_id=1, actions=["*"]) for i in (10, 20)])
        db.add_all([ProjectColumn(id=i + 100, project_id=i, name=f"Column {i}") for i in (10, 20)])
        db.add_all(
            [
                Card(id=i + 200, project_id=i, project_column_id=i + 100, title=f"Card {i}", visibility="SHARED")
                for i in (10, 20)
            ]
        )
        db.add_all([CardComment(id=i + 300, card_id=i + 200, user_id=1, content={}) for i in (10, 20)])
        db.add_all([ProjectWiki(id=i + 400, project_id=i, title=f"Public Wiki {i}", is_public=True) for i in (10, 20)])
        db.add(GlobalCardRelationshipType(id=600, parent_name="parent", child_name="child"))
        db.add(CardRelationship(id=610, card_id_parent=210, card_id_child=220, relationship_type_id=600))
        db.commit()
    yield engine, user, other, boards
    engine.dispose()


def test_persistent_own_connections_cas_role_and_membership_revocation(native):
    engine, user, other, boards = native
    a = connect_native_client(user, "native-client-a")
    b = connect_native_client(user, "native-client-b")
    assert connect_native_client(user, "native-client-a").id == a.id
    assert len(list_native_connections(user)) == 2
    assert list_native_connections(other) == []
    assert positively_internal(user) is False  # compatibility project_members is not employee evidence.
    assert read_native_board_consent(user, a.id, 10)["revision"] is None
    consent = save_native_board_consent(user, a.id, 10, ["read"], None)
    assert consent["scopes"] == ["read"] and consent["revision"] == 1
    assert effective_board_ids(user, a.id, "read") == {10}
    assert effective_board_ids(user, a.id, "write") == set()
    assert effective_board_ids(user, b.id, "read") == set()
    with pytest.raises(AppGovernanceConflict):
        save_native_board_consent(user, a.id, 10, ["read", "write"], None)
    with pytest.raises(AppGovernanceDenied):
        read_native_board_consent(other, a.id, 10)
    # Membership on B, including an accepted invitation, never adds consent.
    assert effective_board_ids(user, a.id, "read") == {10}
    revoked = save_native_board_consent(user, a.id, 10, [], 1)
    assert revoked["revision"] == 2 and effective_board_ids(user, a.id, "read") == set()
    with pytest.raises(AppGovernanceConflict):
        save_native_board_consent(user, a.id, 10, ["read"], 1)
    save_native_board_consent(user, a.id, 10, ["read", "write"], 2)
    with engine.begin() as db:
        db.execute(update(ProjectRole).where(ProjectRole.project_id == 10).values(actions=["read"]))
    assert effective_board_ids(user, a.id, "write") == set()
    with engine.begin() as db:
        db.execute(ProjectAssignedUser.__table__.delete().where(ProjectAssignedUser.project_id == 10))
    assert effective_board_ids(user, a.id, "read") == set()
    with pytest.raises(AppGovernanceDenied):
        read_native_board_consent(user, a.id, 10)
    # The production migration retains existing connection consent on downgrade.
    migration = importlib.import_module("langboard.migrations.versions.20261025010000-efbc567d8901")
    with engine.begin() as db:
        migration.op = Operations(MigrationContext.configure(db))
        with pytest.raises(RuntimeError, match="retained"):
            migration.downgrade()


def test_sql_boundary_filters_lists_counts_indirect_public_wiki_aliases_relationships(native):
    engine, user, *_ = native
    token = native_mcp_boundary.set({"read": frozenset({10}), "write": frozenset()})
    try:
        with Session(engine) as db:
            assert [r.id for r in db.scalars(select(Project).order_by(Project.id).limit(1).offset(0))] == [10]
            assert db.scalar(select(func.count()).select_from(Project)) == 1
            assert db.scalar(select(func.count()).select_from(select(Project).subquery())) == 1
            assert list(db.scalars(select(CardComment).where(CardComment.id == 320))) == []
            assert (
                list(db.scalars(select(ProjectWiki).where(ProjectWiki.id == 420, ProjectWiki.is_public.is_(True))))
                == []
            )
            assert list(db.scalars(select(CardRelationship))) == []  # A->B does not reveal B's identifier.
            alias = aliased(ProjectWiki)
            assert [r.id for r in db.scalars(select(alias))] == [410]
            assert list(db.scalars(select(Card).where(Card.id == 220))) == []
            with pytest.raises(NativeMcpDenied):
                db.execute(update(Card).where(Card.id == 210).values(title="denied"))
            with pytest.raises(NativeMcpDenied):
                db.execute(text("SELECT title FROM project"))
    finally:
        native_mcp_boundary.reset(token)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Project)) == 2  # Legacy and ordinary UI unaffected.


def test_write_boundary_denies_indirect_destination_and_preserves_ungranted_board(native):
    engine, user, *_ = native
    token = native_mcp_boundary.set({"read": frozenset({10}), "write": frozenset({10})})
    try:
        with engine.begin() as db:
            assert db.execute(update(Card).where(Card.id == 220).values(title="denied")).rowcount == 0
            assert db.execute(update(Card).where(Card.id == 210).values(title="allowed")).rowcount == 1
            with pytest.raises(NativeMcpDenied):
                db.execute(update(Card).where(Card.id == 210).values(project_id=20, project_column_id=120))
            with pytest.raises(NativeMcpDenied):
                db.execute(
                    insert(CardRelationship).values(
                        id=620,
                        card_id_parent=210,
                        card_id_child=220,
                        relationship_type_id=600,
                        created_at=SafeDateTime.now(),
                        updated_at=SafeDateTime.now(),
                    )
                )
    finally:
        native_mcp_boundary.reset(token)
    with Session(engine) as db:
        assert db.get(Card, 220).title == "Card 20"
        assert db.get(Card, 210).title == "allowed"


async def test_raw_wrapper_and_read_only_write_denial(native):
    engine, user, *_ = native
    connection = connect_native_client(user, "client")
    save_native_board_consent(user, connection.id, 10, ["read"], None)
    auth = {"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id}
    token = mcp_auth_context.set(auth)
    try:
        async with native_call_boundary("get_card", {"project_uid": native[3][0].get_uid()}):
            with Session(engine) as db:
                assert list(db.scalars(select(Card).where(Card.id == 220))) == []
        with pytest.raises(AuthorizationError):
            async with native_call_boundary("update_wiki", {"project_uid": native[3][0].get_uid()}):
                pytest.fail("read grant allowed write")
        with pytest.raises(AuthorizationError):
            async with native_call_boundary("get_card", {"project_uid": native[3][1].get_uid()}):
                pytest.fail("B allowed")
        with pytest.raises(AuthorizationError):
            async with native_call_boundary("get_shared_user_activities", {}):
                pytest.fail("history leak")
    finally:
        mcp_auth_context.reset(token)


async def test_signed_downstream_identity_overrides_upstream_claims_after_full_validation(monkeypatch):
    from fastmcp.server.auth.auth import AccessToken
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    provider = object.__new__(OAuth.LangboardOIDCProxy)
    monkeypatch.setattr(
        OAuth.LangboardOIDCProxy,
        "jwt_issuer",
        property(
            lambda _: SimpleNamespace(
                verify_token=lambda token: {"scope": "mcp:access", "client_id": "verified-native-client"}
            )
        ),
    )
    upstream = AccessToken(
        token="upstream",
        client_id="upstream-app",
        scopes=["mcp:access", "extra"],
        claims={"iss": "https://id.example", "sub": "linked", "langboard_native_client_id": "attacker"},
    )
    validation = AsyncMock(return_value=upstream)
    monkeypatch.setattr(OIDCProxy, "load_access_token", validation)
    validated = await provider.load_access_token("signed-native-token")
    assert validated.client_id == "verified-native-client"
    assert validated.claims["langboard_native_client_id"] == "verified-native-client"
    assert validated.scopes == ["mcp:access"]
    assert upstream.client_id == "upstream-app" and upstream.claims["langboard_native_client_id"] == "attacker"
    validation.return_value = None
    assert await provider.load_access_token("invalid-upstream") is None
    monkeypatch.setattr(
        OAuth.LangboardOIDCProxy,
        "jwt_issuer",
        property(lambda _: SimpleNamespace(verify_token=lambda token: {"scope": "mcp:access"})),
    )
    validation.return_value = upstream
    assert await provider.load_access_token("missing-downstream-client") is None


def test_human_http_consent_routes_enforce_ownership_channel_and_cas(native, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.routes.mcp import NativeMcpConsentApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security.CollaborationChannel import CollaborationChannel

    engine, user, other, boards = native
    connection = connect_native_client(user, "native-desktop")
    app = FastAPI()
    app.include_router(AppRouter.api)
    current = {"user": user, "channel": CollaborationChannel.HumanUI}

    @app.middleware("http")
    async def verified_session(request, call_next):
        request.scope["auth"] = current["user"]
        request.scope["collaboration_channel"] = current["channel"]
        return await call_next(request)

    path = f"/mcp/connections/{connection.get_uid()}/boards/{boards[0].get_uid()}"
    with TestClient(app) as client:
        listing = client.get("/mcp/connections")
        assert listing.status_code == 200
        assert listing.json()["connections"] == [{"uid": connection.get_uid(), "client_id": "native-desktop"}]
        assert listing.json()["server"] == {
            "name": "langboard",
            "enabled": False,
            "transport_url": None,
            "resource": None,
        }
        assert client.get(path).json()["revision"] is None
        saved = client.put(path, json={"scopes": ["read"], "expected_revision": None})
        assert saved.status_code == 200 and saved.json()["revision"] == 1
        assert saved.headers["cache-control"] == "no-store"
        assert client.put(path, json={"scopes": ["read", "write"], "expected_revision": None}).status_code == 409
        assert client.put(path, json={"scopes": ["read", "read"], "expected_revision": 1}).status_code == 400
        current["user"] = other
        assert client.get("/mcp/connections").json()["connections"] == []
        assert client.get(path).status_code == 403
        assert client.put(path, json={"scopes": [], "expected_revision": 1}).status_code == 403
        current["user"], current["channel"] = user, CollaborationChannel.Mcp
        assert client.get(path).status_code == 403
        assert client.put(path, json={"scopes": ["read", "write"], "expected_revision": 1}).status_code == 403
        assert client.get("/mcp/connections").status_code == 403


async def test_fastmcp_direct_raw_and_public_resource_use_same_boundary(native, monkeypatch):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError
    from langboard.mcp_integration.Providers import create_native_agent_provider
    from langboard.mcp_integration.Server import McpServer
    from langboard.mcp_integration.Tool import McpTool
    from langboard_shared.domain.services import DomainService

    engine, user, _, boards = native
    connection = connect_native_client(user, "native-client")
    save_native_board_consent(user, connection.id, 10, ["read"], None)

    from langboard_shared.core.types import SnowflakeID

    def get_card(project_uid: str, card_uid: str, service: DomainService) -> dict:
        with Session(engine) as db:
            card = db.scalar(select(Card).where(Card.id == SnowflakeID.from_short_code(card_uid)))
            if card is None:
                raise ValueError("Card unavailable")
            return {"title": card.title}

    def read_wiki_content(project_uid: str, wiki_uid: str) -> dict:
        with Session(engine) as db:
            from langboard_shared.core.types import SnowflakeID

            wiki = db.scalar(select(ProjectWiki).where(ProjectWiki.id == SnowflakeID.from_short_code(wiki_uid)))
            if wiki is None:
                raise ValueError("Wiki unavailable")
            return {"title": wiki.title}

    def metadata(handler):
        return {
            "handler": handler,
            "description": "Fixture read",
            "accessible_type": "user",
            "exclude": ["service"] if handler is get_card else [],
        }

    registry = {"get_card": metadata(get_card), "read_wiki_content": metadata(read_wiki_content)}
    monkeypatch.setattr(McpTool, "get_tools", lambda: registry)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: registry.get(name))
    from fastmcp import FastMCP

    server = FastMCP("Native grant regression")
    server.add_provider(create_native_agent_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id})
    try:
        async with Client(server) as client:
            assert "Card 10" in str(
                await client.call_tool(
                    "get_card", {"project_uid": boards[0].get_uid(), "card_uid": SnowflakeID(210).to_short_code()}
                )
            )
            # Supplying A's project does not authorize an indirect B card/wiki.
            with pytest.raises(ToolError):
                await client.call_tool(
                    "get_card", {"project_uid": boards[0].get_uid(), "card_uid": SnowflakeID(220).to_short_code()}
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool",
                    {
                        "name": "get_card",
                        "arguments": {"project_uid": boards[0].get_uid(), "card_uid": SnowflakeID(220).to_short_code()},
                    },
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool",
                    {
                        "name": "read_wiki_content",
                        "arguments": {"project_uid": boards[0].get_uid(), "wiki_uid": SnowflakeID(420).to_short_code()},
                    },
                )
    finally:
        mcp_auth_context.reset(token)


def test_writer_reservation_preserves_board_scope_and_denies_other_expressions(native):
    engine, *_ = native
    token = native_mcp_boundary.set({"read": frozenset({10}), "write": frozenset({10})})
    try:
        with engine.begin() as db:
            assert db.execute(
                update(Project).values(owner_id=Project.owner_id, updated_at=Project.updated_at)
            ).rowcount == 1
            assert db.execute(update(Project).where(Project.id == 20).values(owner_id=Project.owner_id)).rowcount == 0
            assert db.execute(update(Card).values(order=Card.order - 1)).rowcount == 1
            for values in (
                {"project_id": Card.project_column_id},
                {"project_id": Card.project_id + 10},
                {"title": func.upper(Card.title)},
                {"order": Card.order - 2},
                {"order": Card.project_column_id - 1},
            ):
                with pytest.raises(NativeMcpDenied):
                    db.execute(update(Card).where(Card.id == 210).values(**values))
        native_mcp_boundary.set({"read": frozenset({10}), "write": frozenset()})
        with engine.begin() as db, pytest.raises(NativeMcpDenied):
            db.execute(update(Project).where(Project.id == 10).values(owner_id=Project.owner_id))
    finally:
        native_mcp_boundary.reset(token)
    with Session(engine) as db:
        assert db.get(Card, 210).order == -1
        assert db.get(Card, 220).order == 0


def test_keyword_counter_increment_preserves_board_scope(native):
    engine, *_ = native
    DocumentKeywordIndex.__table__.create(engine)
    with Session(engine) as db:
        for identifier, board_id, card_id in ((710, 10, 210), (720, 20, 220)):
            db.add(DocumentKeywordIndex(
                id=identifier, attachment_id=identifier, project_id=board_id, card_id=card_id,
                destination_fingerprint="fixture", destination={}, source_generation="fixture",
            ))
        db.commit()
    token = native_mcp_boundary.set({"read": frozenset({10}), "write": frozenset({10})})
    try:
        with engine.begin() as db:
            assert db.execute(update(DocumentKeywordIndex).values(version=DocumentKeywordIndex.version + 1)).rowcount == 1
            for expression in (DocumentKeywordIndex.version + 2, DocumentKeywordIndex.card_id + 1):
                with pytest.raises(NativeMcpDenied):
                    db.execute(update(DocumentKeywordIndex).values(version=expression))
            with pytest.raises(NativeMcpDenied):
                db.execute(update(DocumentKeywordIndex).values(project_id=DocumentKeywordIndex.project_id + 1))
    finally:
        native_mcp_boundary.reset(token)
    with Session(engine) as db:
        assert db.get(DocumentKeywordIndex, 710).version == 2
        assert db.get(DocumentKeywordIndex, 720).version == 1


def test_boundary_denies_multirow_select_and_missing_insert_scope(native):
    engine, *_ = native
    token = native_mcp_boundary.set({"read": frozenset({10}), "write": frozenset({10})})
    try:
        with engine.begin() as db:
            with pytest.raises(NativeMcpDenied):
                db.execute(insert(Card).values([{"id": 700, "project_id": 20}, {"id": 701, "project_id": 10}]))
            with pytest.raises(NativeMcpDenied):
                db.execute(insert(CardComment).from_select(["id", "card_id"], select(Card.id, Card.id)))
            with pytest.raises(NativeMcpDenied):
                db.execute(insert(CardComment).values(id=710))
            with pytest.raises(NativeMcpDenied):
                db.execute(update(User).where(User.id == 1).values(is_admin=True))
    finally:
        native_mcp_boundary.reset(token)


async def test_primary_statement_boundary_rereads_midcall_revoke_and_does_not_expand(native):
    engine, user, _, boards = native
    connection = connect_native_client(user, "native-client")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id})
    try:
        async with native_call_boundary("update_card", {"project_uid": boards[0].get_uid()}):
            with Session(engine) as db:
                assert db.scalar(select(Card.id).where(Card.id == 210)) == 210
            # Simulate a human changing policy while the same native call runs.
            bypass = native_mcp_boundary.set(None)
            try:
                save_native_board_consent(user, connection.id, 10, [], 1)
                save_native_board_consent(user, connection.id, 20, ["read", "write"], None)
            finally:
                native_mcp_boundary.reset(bypass)
            with Session(engine) as db:
                assert list(db.scalars(select(Card))) == []
                with pytest.raises(NativeMcpDenied):
                    db.execute(update(Card).where(Card.id == 210).values(title="revoked"))
    finally:
        mcp_auth_context.reset(token)


def test_every_exposed_tool_has_reviewed_external_policy():
    import ast
    from pathlib import Path
    from langboard.mcp_integration.NativeConsent import EXTERNAL_SUPPORTED_TOOLS, EXTERNAL_UNSUPPORTED_TOOLS

    names = set()
    for path in (Path(__file__).parents[2] / "langboard" / "mcp_tools").glob("*.py"):
        for function in ast.parse(path.read_text()).body:
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "McpTool.add"
                for decorator in function.decorator_list
            ):
                names.add(function.name)
    assert names
    assert not names - EXTERNAL_SUPPORTED_TOOLS - EXTERNAL_UNSUPPORTED_TOOLS
    assert not EXTERNAL_SUPPORTED_TOOLS.intersection(EXTERNAL_UNSUPPORTED_TOOLS)


async def test_actual_wiki_create_handler_persists_with_defaults_and_denies_read_only(native, monkeypatch):
    from langboard.mcp_integration.Server import McpServer
    from langboard.mcp_tools.WikiMcp import create_project_wiki
    from langboard_shared.publishers import ProjectWikiPublisher
    from langboard_shared.tasks.activities import ProjectWikiActivityTask
    from langboard_shared.tasks.bots import ProjectWikiBotTask

    engine, user, _, boards = native
    effects = []
    monkeypatch.setattr(ProjectWikiPublisher, "created", lambda *args: effects.append("publish"))
    monkeypatch.setattr(ProjectWikiActivityTask, "project_wiki_created", lambda *args: effects.append("activity"))
    monkeypatch.setattr(ProjectWikiBotTask, "project_wiki_created", lambda *args: effects.append("bot-event"))
    connection = connect_native_client(user, "native-editor")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id})
    try:
        wrapped = McpServer._wrap_tool("create_project_wiki", create_project_wiki)
        result = await wrapped(
            project_uid=boards[0].get_uid(), title="Native wiki", content="# Created with human consent"
        )
        assert result["title"] == "Native wiki"
        assert effects == ["publish", "activity", "bot-event"]
        with Session(engine) as db:
            wiki = db.scalar(select(ProjectWiki).where(ProjectWiki.title == "Native wiki"))
            assert wiki.project_id == 10 and wiki.content.content == "# Created with human consent"
        save_native_board_consent(user, connection.id, 10, ["read"], 1)
        with pytest.raises(AuthorizationError):
            await wrapped(project_uid=boards[0].get_uid(), title="Denied", content="No write grant")
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(ProjectWiki).where(ProjectWiki.title == "Denied")) == 0
    finally:
        mcp_auth_context.reset(token)
