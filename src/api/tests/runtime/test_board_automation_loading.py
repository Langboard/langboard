"""Native automation opt out, read middleware, and isolated on-demand producer."""

import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from langboard.middlewares import ApiAuthMiddleware, RoleMiddleware
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import Project, ProjectRole, User
from langboard_shared.helpers import MiddlewareHelper
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request


board = import_module("langboard.routes.board.BoardApi")


def service_for(project):
    return SimpleNamespace(
        project=SimpleNamespace(
            get_by_id_like=Mock(return_value=project),
            get_details=Mock(return_value=(project, {"uid": "board"})),
            get_api_bot_scope_list=Mock(return_value=[{"uid": "project-scope"}]),
            get_api_bot_schedule_list=Mock(return_value=[{"uid": "project-schedule"}]),
            set_last_view=Mock(),
        ),
        project_column=SimpleNamespace(
            get_api_bot_scopes_by_project=Mock(return_value=[{"uid": "column-scope"}]),
            get_api_bot_schedule_list_by_project=Mock(return_value=[{"uid": "column-schedule"}]),
            get_api_list_by_project=Mock(return_value=[]),
        ),
        card=SimpleNamespace(
            resolve_visibility_context=Mock(return_value=(project, object())),
            get_board_snapshot=Mock(return_value=([], [])),
        ),
        app_setting=SimpleNamespace(get_api_global_relationship_list=Mock(return_value=[])),
    )


@pytest.mark.parametrize("include", [True, False])
def test_project_default_and_opt_out(include):
    actor = User(id=1, firstname="Test", lastname="Reader", email="automation@example.invalid", password="test")
    project = object()
    service = service_for(project)
    kwargs = {} if include else {"include_automation": False}
    response = json.loads(board.get_project("board", user_or_bot=actor, service=service, **kwargs).body)
    assert response == {
        "project": {"uid": "board"},
        "project_bot_scopes": [{"uid": "project-scope"}] if include else [],
        "project_bot_schedules": [{"uid": "project-schedule"}] if include else [],
    }
    assert service.project.get_api_bot_scope_list.call_count == int(include)
    assert service.project.get_api_bot_schedule_list.call_count == int(include)
    service.project.set_last_view.assert_not_called()


@pytest.mark.parametrize("include", [True, False])
def test_cards_default_and_opt_out(include):
    project = SimpleNamespace(archive_visible_days=3)
    service = service_for(project)
    kwargs = {} if include else {"include_automation": False}
    response = json.loads(
        board.get_project_cards(
            "board", Request({"type": "http"}), user_or_bot=object(), service=service, **kwargs
        ).body
    )
    assert response == {
        "cards": [],
        "columns": [],
        "checklists": [],
        "global_relationships": [],
        "column_bot_scopes": [{"uid": "column-scope"}] if include else [],
        "column_bot_schedules": [{"uid": "column-schedule"}] if include else [],
    }
    assert service.project_column.get_api_bot_scopes_by_project.call_count == int(include)
    assert service.project_column.get_api_bot_schedule_list_by_project.call_count == int(include)


def test_missing_automation_board_stops_before_scope_queries():
    from langboard_shared.core.routing.ApiException import NotFoundException

    service = service_for(None)
    with pytest.raises(NotFoundException):
        board.get_project_automation("missing", service=service)
    service.project.get_api_bot_scope_list.assert_not_called()
    service.project.get_api_bot_schedule_list.assert_not_called()
    service.project_column.get_api_bot_scopes_by_project.assert_not_called()
    service.project_column.get_api_bot_schedule_list_by_project.assert_not_called()
    service.project.set_last_view.assert_not_called()


@pytest.mark.parametrize("visit", [False, True])
def test_automation_http_real_auth_role_middleware_and_revocation(monkeypatch, visit):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (User, Project, ProjectRole):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    with DbSession.use(readonly=False) as db:
        actor = User(firstname="Native", lastname="Reader", email="native@example.invalid", password="test")
        db.insert(actor)
        outsider = User(firstname="Other", lastname="Actor", email="other@example.invalid", password="test")
        db.insert(outsider)
        project = Project(title="Board", owner_id=actor.id)
        other = Project(title="Other", owner_id=actor.id)
        db.insert(project)
        db.insert(other)
        role = ProjectRole(user_id=actor.id, project_id=project.id, actions=["read"])
        db.insert(role)
    service = service_for(project)

    def validate(scope, **kwargs):
        if not dict(scope["headers"]).get(b"authorization"):
            return 401
        authenticated = outsider if dict(scope["headers"])[b"authorization"] == b"Bearer outsider" else actor
        scope["auth"] = authenticated
        return authenticated

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    app = FastAPI()
    endpoint = board.record_project_visit if visit else board.get_project_automation
    route = next(route for route in AppRouter.api.routes if isinstance(route, APIRoute) and route.endpoint is endpoint)
    app.include_router(AppRouter.api)
    dep = next(dep.call for dep in route.dependant.dependencies if dep.name == "service")
    app.dependency_overrides[dep] = lambda: service
    app.add_middleware(RoleMiddleware, routes=[route])
    app.add_middleware(ApiAuthMiddleware, routes=[route])
    client = TestClient(app)
    request = client.post if visit else client.get
    suffix = "visit" if visit else "automation"
    path = f"/board/{project.get_uid()}/{suffix}"
    assert request(path).status_code == 401
    assert request(path, headers={"Authorization": "Bearer outsider"}).status_code == 403
    headers = {"Authorization": "Bearer fixture"}
    assert request(f"/board/{other.get_uid()}/{suffix}", headers=headers).status_code == 403
    service.project.get_by_id_like.assert_not_called()
    response = request(path, headers=headers)
    assert response.status_code == 200
    if not visit:
        assert response.json() == {
            "project_bot_scopes": [{"uid": "project-scope"}],
            "project_bot_schedules": [{"uid": "project-schedule"}],
            "column_bot_scopes": [{"uid": "column-scope"}],
            "column_bot_schedules": [{"uid": "column-schedule"}],
        }
    service.project.get_by_id_like.assert_called_once_with(project.get_uid())
    service.project.get_details.assert_not_called()
    if visit:
        service.project.set_last_view.assert_called_once_with(actor, project)
    else:
        service.project.set_last_view.assert_not_called()
        service.project_column.get_api_bot_schedule_list_by_project.assert_called_once_with(project, None)
    with DbSession.use(readonly=False) as db:
        db.delete(role)
    assert request(path, headers=headers).status_code == 403
    assert service.project.get_by_id_like.call_count == 1
    engine.dispose()


def test_native_automation_services_are_board_scoped_selects_without_writes(monkeypatch):
    from langboard_shared.domain.models import (
        BotSchedule,
        ProjectBotSchedule,
        ProjectBotScope,
        ProjectColumn,
        ProjectColumnBotSchedule,
        ProjectColumnBotScope,
    )
    from langboard_shared.domain.services import DomainService
    from sqlalchemy import event

    engine = create_engine("sqlite://")
    for model in (
        User,
        Project,
        ProjectColumn,
        ProjectBotScope,
        ProjectBotSchedule,
        ProjectColumnBotScope,
        ProjectColumnBotSchedule,
        BotSchedule,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    with DbSession.use(readonly=False) as db:
        owner = User(firstname="Service", lastname="Reader", email="service@example.invalid", password="test")
        db.insert(owner)
        projects = [Project(title=title, owner_id=owner.id) for title in ("A", "B")]
        for project in projects:
            db.insert(project)
            column = ProjectColumn(project_id=project.id, name="Column", order=0)
            db.insert(column)
            db.insert(ProjectBotScope(project_id=project.id, bot_id=owner.id, conditions=[]))
            db.insert(ProjectColumnBotScope(project_column_id=column.id, bot_id=owner.id, conditions=[]))
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, many: statements.append(statement),
    )
    service = DomainService()
    try:
        for project in projects:
            response = json.loads(board.get_project_automation(project.get_uid(), service=service).body)
            assert len(response["project_bot_scopes"]) == len(response["column_bot_scopes"]) == 1
            assert response["project_bot_scopes"][0]["project_uid"] == project.get_uid()
            assert response["project_bot_schedules"] == response["column_bot_schedules"] == []
        assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    finally:
        service.close()
        engine.dispose()


def test_project_read_survives_unavailable_visit_storage():
    actor = User(id=1, firstname="Test", lastname="Reader", email="visit@example.invalid", password="test")
    service = service_for(object())
    service.project.set_last_view.side_effect = RuntimeError("visit storage unavailable")
    response = board.get_project("board", user_or_bot=actor, service=service)
    assert response.status_code == 200
    service.project.set_last_view.assert_not_called()


def test_explicit_visit_missing_bot_and_storage_failure():
    from langboard_shared.core.routing.ApiException import NotFoundException

    actor = User(id=1, firstname="Test", lastname="Reader", email="visit@example.invalid", password="test")
    service = service_for(None)
    with pytest.raises(NotFoundException):
        board.record_project_visit("missing", user_or_bot=actor, service=service)
    service.project.set_last_view.assert_not_called()
    project = object()
    service.project.get_by_id_like.return_value = project
    assert board.record_project_visit("board", user_or_bot=object(), service=service).status_code == 200
    service.project.set_last_view.assert_not_called()
    service.project.set_last_view.side_effect = RuntimeError("visit storage unavailable")
    with pytest.raises(RuntimeError, match="visit storage unavailable"):
        board.record_project_visit("board", user_or_bot=actor, service=service)
    service.project.set_last_view.assert_called_once_with(actor, project)


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_explicit_visit_updates_only_native_recency_and_frequency(board):
    from datetime import timedelta
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import ProjectAssignedUser
    from langboard_shared.infrastructure.repositories.factory.ProjectAssignedUserRepository import (
        ProjectAssignedUserRepository,
    )

    _, user, project, member, *_ = board
    previous = SafeDateTime.now() - timedelta(days=2)
    with DbSession.use(readonly=False) as db:
        db.exec(
            SqlBuilder.update.table(ProjectAssignedUser)
            .where(ProjectAssignedUser.id == member.id)
            .values(
                last_viewed_at=previous,
                view_count=7,
                board_seen_seq=42,
            )
        )
    with DbSession.use(readonly=False) as db:
        previous = (
            db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.id == member.id))
            .first()
            .last_viewed_at
        )
    service = service_for(project)
    service.project.set_last_view = ProjectAssignedUserRepository(None, None).set_last_view
    endpoint = import_module("langboard.routes.board.BoardApi").record_project_visit
    assert endpoint(project.get_uid(), user_or_bot=user, service=service).status_code == 200
    with DbSession.use(readonly=False) as db:
        saved = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.id == member.id)).first()
    assert saved.last_viewed_at > previous
    assert saved.view_count == 8
    assert saved.board_seen_seq == 42
