"""Dashboard counts use the same audience boundaries as cross-board work reads."""

import json
import os
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAssignedUser,
    Checkitem,
    Checklist,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    UserNotification,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.domain.services.factory.ProjectColumnService import ProjectColumnService
from langboard_shared.domain.services.factory.ProjectService import ProjectService
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.ProjectColumnRepository import ProjectColumnRepository
from langboard_shared.infrastructure.repositories.factory.ProjectRepository import ProjectRepository
from sqlalchemy import create_engine, event, text
from starlette.requests import Request


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def counts_db(request, monkeypatch):
    url = request.param
    if url == "postgresql-test":
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = create_engine(url)
    schema = None
    try:
        if engine.dialect.name == "postgresql":
            schema = "dashboard_counts_" + uuid4().hex
            with engine.begin() as db:
                db.execute(text(f'CREATE SCHEMA "{schema}"'))

            @event.listens_for(engine, "begin")
            def use_schema(connection):
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')

            with engine.begin() as db:
                for table in ("organization", "bot"):
                    db.execute(text(f'CREATE TABLE "{table}" (id BIGINT PRIMARY KEY)'))
        for model in (
            User,
            UserNotification,
            Project,
            ProjectColumn,
            ProjectRole,
            Card,
            Checklist,
            Checkitem,
            WorkflowStageDefinition,
            ProjectAssignedUser,
            CardAssignedUser,
            ProjectActivity,
        ):
            model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        yield engine
    finally:
        engine.dispose()
        if schema:
            cleanup = create_engine(url)
            try:
                with cleanup.begin() as db:
                    db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            finally:
                cleanup.dispose()


@pytest.mark.parametrize(
    "channel,revoked,expected",
    [
        (CollaborationChannel.HumanUI, False, [(2, 2, 2), (3, 3, 3), (0, 0, 0)]),
        (CollaborationChannel.Mcp, False, [(2, 2, 2), (3, 3, 3), (0, 0, 0)]),
        (CollaborationChannel.Api, False, [(1, 1, 1), (2, 2, 2), (0, 0, 0)]),
        (CollaborationChannel.HumanUI, True, [(0, 0, 0)] * 3),
    ],
)
def test_dashboard_counts_hide_internal_and_other_users_private_cards_in_one_batch(
    counts_db, channel, revoked, expected
):
    engine = counts_db
    with DbSession.use(readonly=False) as db:
        user = User(firstname="Count", lastname="Viewer", email="counts@example.invalid", password="test-only")
        db.insert(user)
        other = User(firstname="Other", lastname="Owner", email="other@example.invalid", password="test-only")
        db.insert(other)
        projects = []
        for title in ("External", "Internal", "Revoked"):
            project = Project(owner_id=user.id, title=title)
            db.insert(project)
            column = ProjectColumn(project_id=project.id, name=title)
            db.insert(column)
            projects.append(project)
            for visibility, owner in (
                ("SHARED", None),
                ("INTERNAL", None),
                ("PRIVATE", user.id),
                ("PRIVATE", other.id),
            ):
                db.insert(
                    Card(
                        project_id=project.id,
                        project_column_id=column.id,
                        title=visibility,
                        visibility=visibility,
                        owner_user_id=owner,
                        created_by_user_id=owner or user.id,
                    )
                )
    contexts = {
        int(projects[0].id): CardVisibilityContext(channel, True, True, False, actor_user_id=int(user.id)),
        int(projects[1].id): CardVisibilityContext(channel, True, True, True, actor_user_id=int(user.id)),
    }
    if revoked:
        contexts = {}
    repository = ProjectColumnRepository(lambda _: None, lambda _: None)
    column_service = ProjectColumnService(
        lambda _: None,
        lambda _: None,
        SimpleNamespace(project_column=repository, workflow_stage=SimpleNamespace(get_by_keys=Mock(return_value={}))),
    )
    service = SimpleNamespace(
        project=SimpleNamespace(
            get_api_list=Mock(return_value=([{"uid": p.get_uid()} for p in projects], [p.id for p in projects]))
        ),
        project_column=column_service,
        card=SimpleNamespace(resolve_work_visibility_contexts=Mock(return_value=contexts)),
    )
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda *args: statements.append(args[2]) if args[2].lstrip().upper().startswith("SELECT") else None,
    )
    route = import_module("langboard.routes.dashboard.DashboardApi")
    result = json.loads(
        route.get_projects(Request({"type": "http", "collaboration_channel": channel}), user=user, service=service).body
    )
    assert {c["name"]: (c["count"], c["open_count"], c["incomplete_count"]) for c in result["columns"]} == dict(
        zip(("External", "Internal", "Revoked"), expected)
    )
    assert len(statements) == 2  # physical and unfinished aggregates, not per-board reads
    service.card.resolve_work_visibility_contexts.assert_called_once_with(user, channel)


@pytest.mark.parametrize(
    "channel,day,unread",
    [
        (CollaborationChannel.HumanUI, 3, True),
        (CollaborationChannel.Mcp, 3, True),
        (CollaborationChannel.Api, 1, False),
    ],
)
def test_dashboard_project_summary_does_not_advertise_hidden_card_activity(counts_db, channel, day, unread):
    with DbSession.use(readonly=False) as db:
        actor = User(
            firstname="Summary", lastname="Viewer", email="summary@example.invalid", password="test-only", is_admin=True
        )
        other = User(firstname="Other", lastname="Owner", email="summary-other@example.invalid", password="test-only")
        db.insert(actor)
        db.insert(other)
        project = Project(owner_id=actor.id, title="Shared summary")
        db.insert(project)
        member = ProjectAssignedUser(project_id=project.id, user_id=actor.id, starred=True, board_seen_seq=20)
        db.insert(member)
        column = ProjectColumn(project_id=project.id, name="Work")
        db.insert(column)
        for visibility, owner, event_day in (
            ("SHARED", None, 1),
            ("INTERNAL", None, 9),
            ("PRIVATE", actor.id, 3),
            ("PRIVATE", other.id, 10),
        ):
            card = Card(
                project_id=project.id,
                project_column_id=column.id,
                title=visibility,
                visibility=visibility,
                owner_user_id=owner,
                created_by_user_id=owner or actor.id,
                last_change_seq=event_day * 10,
            )
            db.insert(card)
            db.insert(CardAssignedUser(project_assigned_id=member.id, card_id=card.id, user_id=actor.id))
            db.insert(
                ProjectActivity(
                    project_id=project.id,
                    card_id=card.id,
                    activity_type=ProjectActivityType.CardCreated,
                    created_at=SafeDateTime(2026, 1, event_day),
                )
            )
    contexts = {int(project.id): CardVisibilityContext(channel, True, True, False, actor_user_id=int(actor.id))}
    resolver = SimpleNamespace(resolve_work_visibility_contexts=Mock(return_value=contexts))
    repository = ProjectRepository(None, None)
    service = ProjectService(lambda _: resolver, lambda _: resolver, SimpleNamespace(project=repository))
    projects, _ = service.get_api_list(actor, channel=channel)
    assert projects[0]["last_activity_at"].day == day
    assert projects[0]["related_activity_at"].day == day
    assert projects[0]["related_to_current_user"] is True
    assert projects[0]["board_has_unread_change"] is unread
    # Once only inaccessible cards are newer, the unread dot must remain clear.
    with DbSession.use(readonly=False) as db:
        member.board_seen_seq = 30
        db.update(member)
    projects, _ = service.get_api_list(actor, channel=channel)
    assert projects[0]["board_has_unread_change"] is False
    starred = service.get_api_starred_project_list(actor, channel=channel)
    assert starred[0]["last_activity_at"].day == day
    contexts.clear()
    projects, _ = service.get_api_list(actor, channel=channel)
    assert projects[0]["last_activity_at"] is None
    assert projects[0]["related_activity_at"] is None
    assert projects[0]["related_to_current_user"] is False
    assert projects[0]["board_has_unread_change"] is False


@pytest.mark.parametrize("project_count", [1, 20])
@pytest.mark.parametrize("channel", [CollaborationChannel.HumanUI, CollaborationChannel.Mcp, CollaborationChannel.Api])
def test_assigned_work_does_not_load_dashboard_summaries_or_repeat_authority(
    counts_db, monkeypatch, project_count, channel
):
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    with DbSession.use(readonly=False) as db:
        actor = User(
            firstname="Assigned",
            lastname="Viewer",
            email="assigned@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
            is_admin=True,
        )
        db.insert(actor)
        for number in range(project_count):
            project = Project(owner_id=actor.id, title=f"Assigned {number}")
            db.insert(project)
            db.insert(ProjectAssignedUser(project_id=project.id, user_id=actor.id))
    statements = []
    event.listen(
        counts_db,
        "before_cursor_execute",
        lambda *args: statements.append(args[2]) if args[2].lstrip().upper().startswith("SELECT") else None,
    )
    with DomainService.use() as service:
        assert service.card.list_assigned_work(actor, limit=1, channel=channel) == {"items": [], "next_cursor": None}
    assert len(statements) == 3  # current account, current membership/read grants, one assigned page
    assert not any("project_activity" in query or "max(card.last_change_seq)" in query for query in statements)
    with DbSession.use(readonly=False) as db:
        actor.activated_at = None
        db.update(actor)
    statements.clear()
    with DomainService.use() as service:
        assert service.card.list_assigned_work(actor, limit=1, channel=channel) == {"items": [], "next_cursor": None}
    assert len(statements) == 1  # current inactive account stops before membership or card reads


@pytest.mark.parametrize("project_count", [1, 20])
@pytest.mark.parametrize("surface,expected_queries", [("rest", 4), ("mcp_assigned", 3), ("mcp_mentioned", 4)])
def test_multipurpose_work_skips_dashboard_queries_and_reuses_current_scope(
    counts_db, monkeypatch, project_count, surface, expected_queries
):
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    with DbSession.use(readonly=False) as db:
        actor = User(
            firstname="Multipurpose",
            lastname="Viewer",
            email="multipurpose@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
            is_admin=True,
        )
        db.insert(actor)
        for number in range(project_count):
            project = Project(owner_id=actor.id, title=f"Multipurpose {number}")
            db.insert(project)
            db.insert(ProjectAssignedUser(project_id=project.id, user_id=actor.id))
    statements = []
    event.listen(
        counts_db,
        "before_cursor_execute",
        lambda *args: statements.append(args[2]) if args[2].lstrip().upper().startswith(("SELECT", "WITH")) else None,
    )
    with DomainService.use() as service:
        if surface == "rest":
            response = import_module("langboard.routes.dashboard.DashboardApi").get_my_work(
                Request({"type": "http", "collaboration_channel": CollaborationChannel.HumanUI}),
                user=actor,
                service=service,
                limit=1,
            )
            payload = json.loads(response.body)
            assert payload["cards"] == []
            assert payload["counts"] == dict.fromkeys(
                ["overdue", "today", "upcoming", "review", "assigned", "mentioned", "created"], 0
            )
            assert payload["total"] == 0 and payload["next_cursor"] is None
        else:
            result = import_module("langboard.mcp_tools.UserMcp").get_my_work_cards(
                actor, service, purposes=["assigned" if surface == "mcp_assigned" else "mentioned"], limit=1
            )
            assert result == {"cards": [], "returned_count": 0}
    assert len(statements) == expected_queries
    assert not any("project_activity" in query or "max(card.last_change_seq)" in query for query in statements)
    with DbSession.use(readonly=False) as db:
        actor.activated_at = None
        db.update(actor)
    statements.clear()
    with DomainService.use() as service:
        if surface == "rest":
            response = import_module("langboard.routes.dashboard.DashboardApi").get_my_work(
                Request({"type": "http", "collaboration_channel": CollaborationChannel.HumanUI}),
                user=actor,
                service=service,
                limit=1,
            )
            payload = json.loads(response.body)
            assert payload["cards"] == []
            assert payload["counts"] == dict.fromkeys(
                ["overdue", "today", "upcoming", "review", "assigned", "mentioned", "created"], 0
            )
            assert payload["total"] == 0 and payload["next_cursor"] is None
        else:
            result = import_module("langboard.mcp_tools.UserMcp").get_my_work_cards(
                actor, service, purposes=["mentioned"], limit=1
            )
            assert result == {"cards": [], "returned_count": 0}
    assert len(statements) == 1  # no stale-account notification or card lookup
