"""Native board consent cannot retain activation after its grant is withdrawn."""

import langboard.routes.settings.AppBoardConsentApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import AppDefinition, BoardAppBinding
from langboard_shared.Env import Env


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("capabilities", [["cards.create"], [], ["workflow.transition", "cards.create"]])
def test_consent_withdrawal_clears_only_unsupported_activation(board, monkeypatch, tmp_path, capabilities):
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    _, actor, project, _, role, *_ = board
    from langboard_shared.publishers import AppSettingPublisher

    events = []
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: events.append("changed"))
    with DbSession.atomic() as db:
        role.actions = ["read", "update"]
        db.update(role)
        definition = AppDefinition(
            key="consent-probe",
            approved_by=actor.id,
            declaration={"capabilities": ["workflow.transition", "cards.create"]},
        )
        db.insert(definition)
        binding = BoardAppBinding(
            project_id=project.id,
            app_key=definition.key,
            state="enabled",
            granted_capabilities=["workflow.transition", "cards.create"],
            stage_transitions_enabled=True,
        )
        db.insert(binding)
        payload = {
            "capabilities": capabilities,
            "expected_revision": binding.edit_revision(),
            "expected_app_revision": definition.edit_revision(),
        }
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    path = f"/board/{project.get_uid()}/apps/consent-probe/consent"
    headers = {"Authorization": f"Bearer {access}"}
    with TestClient(app) as client:
        assert client.put(path, json=payload).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        saved = client.put(path, json=payload, headers=headers)
        assert saved.status_code == 200, saved.text
        expected = "workflow.transition" in capabilities
        assert saved.json()["stage_transitions_enabled"] is expected
        assert saved.json()["state"] == ("enabled" if capabilities else "disabled")
        assert saved.json()["granted_capabilities"] == sorted(capabilities)
        assert events == ["changed"]
        stale = client.put(path, json={**payload, "capabilities": []}, headers=headers)
        assert stale.status_code == 409
        assert events == ["changed"]
        with DbSession.atomic() as db:
            stored = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
            assert stored.stage_transitions_enabled is expected
            assert stored.granted_capabilities == sorted(capabilities)
