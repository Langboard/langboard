"""Exercise permission-scoped reminder rules through the authenticated REST entrypoints."""

import importlib
from types import SimpleNamespace
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt import encode
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board.BoardCardReminderApi import create_card_reminder, list_card_reminders, update_card_reminder
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    CardReminderRule,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.Env import Env


def _headers(user: User) -> dict:
    token = encode(
        {
            "sub": str(user.id),
            "internal": "bot",
            "api_permission_level": "edit",
            "exp": int(SafeDateTime.now().timestamp()) + 300,
            "iss": Env.PROJECT_NAME,
        },
        Env.JWT_SECRET_KEY,
        algorithm=Env.JWT_ALGORITHM,
    )
    return {"X-Api-Token": token}


def test_reminders_auth_scope_replay_and_revision(current_card, monkeypatch):
    owner, project, card, card_service = current_card
    for model in (ProjectColumn, WorkflowStageDefinition, CardReminderRule):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.atomic() as db:
        reader = User(
            firstname="Other",
            lastname="Reader",
            email="reminder-reader@example.invalid",
            password=owner.password,
            activated_at=SafeDateTime.now(),
        )
        db.insert(reader)
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=reader.id))
        db.insert(ProjectRole(project_id=project.id, user_id=owner.id, actions=["*"]))
        reader_role = ProjectRole(project_id=project.id, user_id=reader.id, actions=[ProjectRoleAction.Read.value])
        db.insert(reader_role)
        current_column = ProjectColumn(project_id=project.id, name="Ready")
        db.insert(current_column)
        card.project_column_id = current_column.id
        card.visibility = "SHARED"
        card.owner_user_id = None
        db.update(card)

    app = FastAPI()
    app.include_router(AppRouter.api)
    service = SimpleNamespace(card=card_service, close=lambda: None)
    monkeypatch.setattr(
        importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service
    )
    for route in app.routes:
        if getattr(route, "endpoint", None) in (create_card_reminder, list_card_reminders, update_card_reminder):
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)

    endpoint = f"/board/{project.get_uid()}/card/{card.get_uid()}/reminders"
    body = {"request_uid": str(uuid4()), "schedule": {"anchor": "2090-01-05T09:00:00Z", "timezone": "UTC"}}
    with TestClient(app) as client:
        assert client.get(endpoint).status_code in (401, 422)
        response = client.post(endpoint, headers=_headers(owner), json=body)
        assert response.status_code == 201, response.text
        personal = response.json()["item"]
        assert personal["next_due_at"] == "2090-01-05T09:00:00+00:00"
        assert client.post(endpoint, headers=_headers(owner), json=body).json()["item"] == personal
        assert client.post(endpoint, headers=_headers(owner), json={**body, "shared": True}).status_code == 409
        assert client.get(endpoint, headers=_headers(reader)).json() == {"items": [], "next_cursor": None}

        update = {"schedule": body["schedule"], "expected_revision": personal["revision"], "enabled": False}
        target = f"{endpoint}/{personal['uid']}"
        assert client.put(target, headers=_headers(reader), json=update).status_code == 404
        paused = client.put(target, headers=_headers(owner), json=update).json()["item"]
        assert not paused["enabled"] and paused["next_due_at"] is None
        assert client.put(target, headers=_headers(owner), json=update).status_code == 409
        assert client.post(endpoint, headers=_headers(owner), json=body).json()["item"] == personal
        invalid = {
            "request_uid": str(uuid4()),
            "schedule": {"deadline_offset": "PT1H", "timezone": "Invalid/TimeZone"},
        }
        assert client.post(endpoint, headers=_headers(owner), json=invalid).status_code == 400

        public_body = {**body, "request_uid": str(uuid4()), "shared": True}
        assert client.post(endpoint, headers=_headers(reader), json=public_body).status_code == 403
        shared = client.post(endpoint, headers=_headers(owner), json=public_body).json()["item"]
        listed = client.get(endpoint, headers=_headers(reader), params={"limit": 1}).json()
        assert listed == {"items": [shared], "next_cursor": None}
        shared_target = f"{endpoint}/{shared['uid']}"
        assert client.put(shared_target, headers=_headers(reader), json=update).status_code == 403

        with DbSession.atomic() as db:
            reader_role.actions = [ProjectRoleAction.Read.value, ProjectRoleAction.CardUpdate.value]
            db.update(reader_role)
        assert client.put(shared_target, headers=_headers(reader), json=update).status_code == 200
        reader_body = {**body, "request_uid": str(uuid4())}
        reader_rule = client.post(endpoint, headers=_headers(reader), json=reader_body)
        assert reader_rule.status_code == 201
        owner_list = client.get(endpoint, headers=_headers(owner)).json()["items"]
        assert reader_rule.json()["item"]["uid"] not in {item["uid"] for item in owner_list}
        with DbSession.atomic() as db:
            card.visibility = "PRIVATE"
            card.owner_user_id = owner.id
            db.update(card)
        assert client.get(endpoint, headers=_headers(reader)).status_code == 404
