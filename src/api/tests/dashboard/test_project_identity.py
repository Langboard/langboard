"""Board identity persists through the existing authorized settings update."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board import BoardSettingApi  # noqa: F401
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectRole
from langboard_shared.Env import Env
from langboard_shared.publishers import ProjectPublisher
from langboard_shared.tasks.activities import ProjectActivityTask
from langboard_shared.tasks.bots import ProjectBotTask
from sqlalchemy.orm import Session


def test_board_identity_http_and_projection(board, monkeypatch):
    _, reader, project, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "board-identity-test-key-at-least-32-bytes"))
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    events = []
    monkeypatch.setattr(ProjectPublisher, "updated", lambda _, payload: events.append(payload))
    monkeypatch.setattr(ProjectActivityTask, "project_updated", lambda *_: None)
    monkeypatch.setattr(ProjectBotTask, "project_updated", lambda *_: None)

    def reload_project():
        with Session(engine) as db:
            return db.get(Project, project.id).api_response()

    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    path = f"/board/{project.get_uid()}/settings/details"
    details = {"title": project.title, "project_type": project.project_type, "archive_visible_days": 3}
    identity = {"style": "abbreviation", "color": "plum", "icon": None, "abbreviation": "랭보드"}

    with Session(engine) as db:
        db.add(ProjectAssignedUser(id=21, project_id=project.id, user_id=project.owner_id))
        db.add(ProjectRole(id=31, project_id=project.id, user_id=project.owner_id, actions=["read", "update"]))
        db.commit()
    assert reload_project().get("identity") is None
    with TestClient(app) as client:
        for actor, status in ((reader.id, 403), (project.owner_id, 200)):
            access, refresh = AuthSecurity.authenticate(actor)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            response = client.put(path, headers=headers, json={**details, "identity": identity})
            assert response.status_code == status, response.text
        assert response.json() == {"identity": identity}
        assert reload_project()["identity"] == identity
        assert events == [{"identity": identity}]

        response = client.put(path, headers=headers, json={**details, "identity": {**identity, "color": "#ffffff"}})
        assert response.status_code >= 400
        assert reload_project()["identity"] == identity

        assert client.put(path, headers=headers, json={**details, "title": "Renamed board"}).status_code == 200
        assert reload_project()["identity"] == identity
        assert client.put(path, headers=headers, json={**details, "identity": None}).json() == {"identity": None}
        assert reload_project().get("identity") is None
        assert events[-1]["identity"] is None
