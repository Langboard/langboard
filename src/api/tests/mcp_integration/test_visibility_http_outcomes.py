"""Only known pre-save visibility failures are definitive."""

# ruff: noqa: F811
import importlib
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, Request
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient
from langboard.mcp_tools.CardManagementMcp import change_card_visibility
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, ProjectRole
from tests.card_visibility.conftest import current_card  # noqa: F401
from tests.mcp_integration.test_card_visibility_actions import invoke, persisted, visibility_card  # noqa: F401


@pytest.mark.parametrize("current_card", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("failure", ["conflict", "denied", "late_value", "late_permission"])
async def test_visibility_http_only_classifies_known_preflight(visibility_card, monkeypatch, failure):
    b = visibility_card
    stale = SafeDateTime.fromisoformat(b.card.updated_at.isoformat()).isoformat()
    invoke(b)
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == b.card.id)).first()
        version = SafeDateTime.fromisoformat(current.updated_at.isoformat()).isoformat()
        if failure == "denied":
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.user_id == b.user.id)).first()
            role.actions = []
            db.update(role)
    before = persisted(b)
    audit_count = len(before[1])
    effects = []
    if failure.startswith("late_"):

        def fail_after_effect(*args, **kwargs):
            effects.append("saved")
            raise (ValueError if failure == "late_value" else PermissionError)("private internal details")

        monkeypatch.setattr(b.owner, "change_visibility", fail_after_effect)
    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=["change_card_visibility"], user_id=None)
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(
            mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group),
            close=lambda: None,
        ),
    )
    server = FastMCP("visibility-http-outcomes")

    @server.tool(name="change_card_visibility")
    def execute():
        return change_card_visibility(
            b.project.get_uid(),
            b.card.get_uid(),
            "PRIVATE",
            "INTERNAL",
            b.user,
            b.service,
            confirmed=True,
            expected_updated_at=stale if failure == "conflict" else version,
        )

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/mcp/tools/change_card_visibility")
    async def dispatch(request: Request):
        request.scope["auth"] = b.user
        return await route.execute_mcp_tool("change_card_visibility", request)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post(
            "/mcp/tools/change_card_visibility", json={}, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"}
        )
    assert "private internal details" not in response.text
    if failure == "conflict":
        assert response.status_code == 409, response.text
        assert response.json()["outcome"] == "not_applied"
        assert response.json()["revision_conflict"] is True
        assert response.json()["retryable"] is False
        assert effects == []
    elif failure == "denied":
        assert response.status_code == 403, response.text
        assert effects == []
    else:
        assert response.status_code == 500, response.text
        assert response.json()["outcome"] == "unknown"
        assert effects == ["saved"]
    after = persisted(b)
    assert after[0] == before[0] == "INTERNAL"
    assert len(after[1]) == audit_count
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == b.card.id)).first()
        assert SafeDateTime.fromisoformat(current.updated_at.isoformat()).isoformat() == version
