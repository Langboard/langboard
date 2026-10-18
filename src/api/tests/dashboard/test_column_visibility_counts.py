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
from langboard_shared.domain.models import (
    Card,
    Checkitem,
    Checklist,
    Project,
    ProjectColumn,
    User,
    WorkflowStageDefinition,
)
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext
from langboard_shared.domain.services.factory.ProjectColumnService import ProjectColumnService
from langboard_shared.infrastructure.repositories.factory.ProjectColumnRepository import ProjectColumnRepository
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
        for model in (User, Project, ProjectColumn, Card, Checklist, Checkitem, WorkflowStageDefinition):
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
