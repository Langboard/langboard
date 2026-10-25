"""Private ownership forbids assignment commands before persistence and effects.

SQLite exercises native creation, member/label repositories and visibility. Only
creation readiness/title/checklist orchestration and outbound effects are replaced.
HTTP authenticates real native tokens; MCP uses an authenticated context and the
registered transport, role checker and native dependency lifecycle.
"""

import importlib
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from langboard.mcp_integration import Dependencies
from langboard.mcp_integration.Providers import create_native_tool
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardMcp  # noqa: F401 - register native tool handlers
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard.routes.board import BoardCardApi
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAssignedProjectLabel,
    CardAssignedUser,
    NativeMcpBoardConsent,
    NativeMcpConnection,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectLabel,
    ProjectRole,
    User,
)
from langboard_shared.domain.services.factory.ProjectLabelService import ProjectLabelService
from langboard_shared.domain.services.factory.ProjectService import ProjectService
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.CardAssignedProjectLabelRepository import (
    CardAssignedProjectLabelRepository,
)
from langboard_shared.infrastructure.repositories.factory.CardAssignedUserRepository import CardAssignedUserRepository
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from langboard_shared.infrastructure.repositories.factory.ProjectLabelRepository import ProjectLabelRepository
from sqlalchemy import select


MODULE = "langboard_shared.domain.services.factory.CardService"


@pytest.fixture
def assignment_board(current_card, monkeypatch):
    owner, project, _, card_service = current_card
    engine = DbEngine.get_main_engine()
    for model in (
        ProjectColumn,
        CardAssignedUser,
        ProjectLabel,
        CardAssignedProjectLabel,
        NativeMcpConnection,
        NativeMcpBoardConsent,
    ):
        model.__table__.create(engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    repo = card_service.repo
    repo.card = CardRepository(None, None)
    repo.card_assigned_user = CardAssignedUserRepository(None, None)
    repo.project_label = ProjectLabelRepository(None, None)
    repo.card_assigned_project_label = CardAssignedProjectLabelRepository(None, None)
    with DbSession.atomic() as db:
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=owner.id))
        db.insert(ProjectRole(project_id=project.id, user_id=owner.id, actions=["read", "card_update"]))
        column = ProjectColumn(project_id=project.id, name="Doing")
        db.insert(column)
        label = ProjectLabel(project_id=project.id, name="Keep", color="#123456", description="Fixture")
        db.insert(label)

    @contextmanager
    def creation_unit():
        with DbSession.atomic() as db:
            yield SimpleNamespace(db=db, watch_new=lambda _: None)

    monkeypatch.setattr(importlib.import_module(MODULE), "execution_readiness_uow", creation_unit)
    monkeypatch.setattr(card_service, "_apply_title_labels", lambda actor, project, card, title: title)
    monkeypatch.setattr(card_service, "ensure_completion_checklist", Mock())
    stamp = Mock()
    monkeypatch.setattr(card_service, "mark_card_changed", stamp)
    card, _ = card_service.create(
        owner,
        project.get_uid(),
        column.get_uid(),
        "Solo private creation",
        EditorContentModel(content="Personal work"),
        dispatch_effects=False,
    )
    assert card.visibility == "PRIVATE" and card.owner_user_id == owner.id
    assert repo.card_assigned_user.get_all_by_card(card) == []
    # The second member joins after native solo-owner creation.
    with DbSession.atomic() as db:
        other = User(
            firstname="Later",
            lastname="Member",
            email="later@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(other)
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=other.id))
        db.insert(CardAssignedProjectLabel(card_id=card.id, project_label_id=label.id))
    effects = [stamp]
    for name in (
        "CardPublisher.assigned_users_updated",
        "CardPublisher.labels_updated",
        "CardActivityTask.card_assigned_users_updated",
        "CardActivityTask.card_labels_updated",
    ):
        effect = Mock()
        target, attribute = name.split(".")
        monkeypatch.setattr(getattr(importlib.import_module(MODULE), target), attribute, effect)
        effects.append(effect)
    notification = Mock()
    monkeypatch.setattr(card_service, "_get_service", lambda _: notification)
    effects.append(notification)
    stamp.reset_mock()
    service = SimpleNamespace(
        card=card_service,
        project=ProjectService(lambda _: None, lambda _: None, repo),
        project_label=ProjectLabelService(lambda _: None, lambda _: None, repo),
        close=lambda: None,
    )
    return SimpleNamespace(
        owner=owner,
        other=other,
        project=project,
        card=card,
        label=label,
        service=service,
        effects=effects,
        engine=engine,
        column=column,
    )


def assert_unchanged(b):
    with b.engine.connect() as db:
        assert db.execute(select(CardAssignedUser.__table__)).all() == []
        assert [row.project_label_id for row in db.execute(select(CardAssignedProjectLabel.__table__))] == [b.label.id]
        row = db.execute(select(Card.__table__).where(Card.id == b.card.id)).one()
        assert row.owner_user_id == b.owner.id and row.visibility == "PRIVATE"
        assert row.last_change_seq == b.card.last_change_seq
    for effect in b.effects:
        assert effect.mock_calls == []


def native_oauth_context(b):
    connection = connect_native_client(b.owner, "private-assignment-regression")
    save_native_board_consent(b.owner, connection.id, b.project.id, ["read", "write"], None)
    return {"transport": "oauth", "user_or_bot": b.owner, "native_connection_id": connection.id}


@pytest.mark.parametrize("command", ["replace_other", "replace_self", "replace_empty", "add_other", "add_self", "self"])
def test_private_service_denies_all_assignment_controls(assignment_board, command):
    b = assignment_board
    args = (b.owner, b.project.get_uid(), b.card.get_uid())
    with pytest.raises(PermissionError, match="Private cards do not support assignees"):
        if command.startswith("replace"):
            users = (
                []
                if command == "replace_empty"
                else [getattr(b, "other" if command == "replace_other" else "owner").get_uid()]
            )
            b.service.card.update_assigned_users(*args, users)
        elif command.startswith("add"):
            b.service.card.assign_member(*args, getattr(b, "other" if command == "add_other" else "owner").get_uid())
        else:
            b.service.card.assign_self(*args)
    assert_unchanged(b)


@pytest.mark.parametrize("current_card", ["sqlite-http"], indirect=True)
@pytest.mark.parametrize("command", ["replace_other", "replace_self", "replace_empty", "add_other", "add_self"])
def test_private_http_assignment_returns_forbidden(assignment_board, monkeypatch, command):
    b = assignment_board
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: b.service
    )
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) in (
            BoardCardApi.update_card_assigned_users,
            BoardCardApi.add_card_assignee,
        ):
            for dep in route.dependant.dependencies:
                if dep.name == "service":
                    app.dependency_overrides[dep.call] = lambda: b.service
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(b.owner.id)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
    path = f"/board/{b.project.get_uid()}/card/{b.card.get_uid()}/assigned-users"
    target = b.other if command.endswith("other") else b.owner
    with TestClient(app) as client:
        if command.startswith("replace"):
            response = client.put(
                path, json={"assigned_users": [] if command == "replace_empty" else [target.get_uid()]}, headers=headers
            )
        else:
            response = client.put(f"{path}/{target.get_uid()}", headers=headers)
    assert response.status_code == 403, response.text
    assert_unchanged(b)


@pytest.mark.parametrize("current_card", ["sqlite-http"], indirect=True)
@pytest.mark.parametrize("command", ["replace_other", "replace_self", "replace_empty", "self"])
async def test_private_registered_mcp_assignment_rejects_without_partial_labels(assignment_board, monkeypatch, command):
    b = assignment_board

    def factory(_type):
        @asynccontextmanager
        async def resource():
            yield b.service

        return resource

    monkeypatch.setattr(Dependencies, "_factory_dependency", factory)
    monkeypatch.setattr(importlib.import_module("langboard.mcp_integration.Server"), "DomainService", lambda: b.service)
    name = "assign_card_to_me" if command == "self" else "set_card_people_and_labels"
    server = FastMCP("Private assignment regression", strict_input_validation=True)
    server.add_tool(create_native_tool(name, McpTool.get_tool(name), McpServer._wrap_tool))
    arguments = {"project_uid": b.project.get_uid(), "card_uid": b.card.get_uid()}
    if command != "self":
        arguments.update(
            assign_user_uids=[]
            if command == "replace_empty"
            else [getattr(b, "other" if command == "replace_other" else "owner").get_uid()],
            label_uids=[],
        )
    token = mcp_auth_context.set(native_oauth_context(b))
    try:
        async with Client(server) as client:
            result = await client.call_tool(name, arguments, raise_on_error=False)
            assert result.is_error, result
            assert "Private cards do not support assignees" in str(result)
    finally:
        mcp_auth_context.reset(token)
    assert_unchanged(b)


@pytest.mark.parametrize("visibility", ["SHARED", "INTERNAL"])
def test_nonprivate_assignment_commands_preserve_existing_behavior(assignment_board, visibility):
    b = assignment_board
    with DbSession.atomic() as db:
        b.card.visibility, b.card.owner_user_id = visibility, None
        db.update(b.card)
    args = (b.owner, b.project.get_uid(), b.card.get_uid())
    assert b.service.card.assign_member(*args, b.other.get_uid())["changed"]
    assert b.service.card.assign_self(*args)["changed"]
    assert [user.id for user in b.service.card.update_assigned_users(*args, [b.other.get_uid()])] == [b.other.id]
    assert b.service.card.update_assigned_users(*args, []) == []


@pytest.mark.parametrize("current_card", ["sqlite-http"], indirect=True)
@pytest.mark.parametrize("transport", ["service", "http", "create_card", "create_card_in_leftmost_column"])
async def test_private_initial_assignee_denial_is_structured_and_writes_nothing(
    assignment_board, monkeypatch, transport
):
    b = assignment_board
    # Restore the native solo-owner condition. Do not override visibility selection.
    with DbSession.atomic() as db:
        db.delete(b.service.card.repo.project_assigned_user.find_by_user_and_project(b.other, b.project))
        role = db.exec(select(ProjectRole).where(ProjectRole.user_id == b.owner.id)).first()[0]
        role.actions = ["read", "card_update", "card_write"]
        db.update(role)
    assert b.service.card.default_creation_visibility(b.project, b.owner).value == "PRIVATE"
    with b.engine.connect() as db:
        before = db.execute(select(Card.__table__)).all()
    created = Mock()
    monkeypatch.setattr(b.service.card, "dispatch_created", created)
    b.effects.append(created)
    if transport == "service":
        with pytest.raises(PermissionError, match="Private cards do not support assignees"):
            b.service.card.create(
                b.owner, b.project.get_uid(), b.column.get_uid(), "Denied", assign_user_uids=[b.owner.get_uid()]
            )
    elif transport == "http":
        monkeypatch.setattr(
            importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: b.service
        )
        app = FastAPI()
        app.include_router(AppRouter.api)
        for route in app.routes:
            if getattr(route, "endpoint", None) is BoardCardApi.create_card:
                for dep in route.dependant.dependencies:
                    if dep.name == "service":
                        app.dependency_overrides[dep.call] = lambda: b.service
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        access, refresh = AuthSecurity.authenticate(b.owner.id)
        headers = {"Authorization": f"Bearer {access}", "Cookie": f"{Env.REFRESH_TOKEN_NAME}={refresh}"}
        with TestClient(app) as client:
            result = client.post(
                f"/board/{b.project.get_uid()}/card",
                headers=headers,
                json={
                    "title": "Denied",
                    "project_column_uid": b.column.get_uid(),
                    "assign_users": [b.owner.get_uid()],
                },
            )
        assert result.status_code == 403, result.text
    else:
        # Column-list workflow projections are outside assignment scope. Supply
        # the persisted column projection; keep project/member validation native.
        b.service.project_column = SimpleNamespace(get_api_list_by_project=lambda _: [b.column.api_response()])

        def factory(_type):
            @asynccontextmanager
            async def resource():
                yield b.service

            return resource

        monkeypatch.setattr(Dependencies, "_factory_dependency", factory)
        monkeypatch.setattr(
            importlib.import_module("langboard.mcp_integration.Server"), "DomainService", lambda: b.service
        )
        server = FastMCP("Native private creation denial", strict_input_validation=True)
        server.add_tool(create_native_tool(transport, McpTool.get_tool(transport), McpServer._wrap_tool))
        arguments = {
            "project_uid": b.project.get_uid(),
            "title": "Denied",
            "description": None,
            "assign_user_uids": [b.owner.get_uid()],
        }
        if transport == "create_card":
            arguments["column_uid"] = b.column.get_uid()
        token = mcp_auth_context.set(native_oauth_context(b))
        try:
            async with Client(server) as client:
                result = await client.call_tool(transport, arguments, raise_on_error=False)
                assert result.is_error
                assert "Private cards do not support assignees" in str(result)
        finally:
            mcp_auth_context.reset(token)
    with b.engine.connect() as db:
        assert db.execute(select(Card.__table__)).all() == before
    assert_unchanged(b)
