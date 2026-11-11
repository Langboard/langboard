"""MCP REST compatibility adapter tests."""

from types import SimpleNamespace
import pytest
from langboard.mcp_integration import Server
from langboard.routes.mcp.McpApi import serialize_mcp_result
from pydantic import BaseModel


def test_typed_results_are_recursively_dumped_as_json() -> None:
    """Nested typed outputs remain structured JSON in the REST compatibility facade."""

    class Card(BaseModel):
        uid: str

    class Result(BaseModel):
        card: Card

    assert serialize_mcp_result({"page": [Result(card=Card(uid="c1"))]}) == {"page": [{"card": {"uid": "c1"}}]}


@pytest.mark.parametrize(
    "mode", ["rejected", "invalid_submission", "after_write", "after_write_validation", "conflict"]
)
async def test_verification_http_distinguishes_preflight_from_unknown_write(monkeypatch, mode):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.services.CardVerification import (
        VerificationConflict,
        VerificationEvidence,
        VerificationInputRejected,
        VerificationSubmission,
    )

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    name = "record_card_verification_evidence"
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    closed, effects = [], []
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(
            mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
        ),
    )
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    server = FastMCP("verification-preflight-http")

    @server.tool(name=name)
    def record() -> dict:
        if mode == "rejected":
            raise VerificationInputRejected("private checkitem detail")
        if mode == "conflict":
            raise VerificationConflict("private revision detail")
        if mode == "invalid_submission":
            from langboard.mcp_tools.CardMcp import record_card_verification_evidence

            # Item-level evidence alone cannot satisfy the separate card-level
            # evidence requirement. Exercise the real native constructor boundary.
            return record_card_verification_evidence(
                "board",
                "card",
                0,
                "verified",
                [
                    VerificationEvidence(
                        reference="private evidence",
                        source_revision="source",
                        environment="canary",
                        checkitem_uid=f"item{i}",
                    )
                    for i in range(3)
                ],
                SimpleNamespace(id=1),
                SimpleNamespace(card=SimpleNamespace(record_verification_evidence=lambda *_: effects.append("saved"))),
                required_checkitem_uids=[f"item{i}" for i in range(3)],
            )
        effects.append("saved")
        if mode == "after_write_validation":
            VerificationSubmission(expected_change_seq=-1, decision="partial", evidence=[])
        raise ValueError("private checkitem detail")

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/verification")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(name, request)

    request_id = "a" * 32
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post(
            "/verification",
            json={},
            headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group", "X-Request-ID": request_id},
        )
    assert (
        response.status_code
        == {
            "rejected": 400,
            "invalid_submission": 400,
            "after_write": 500,
            "after_write_validation": 500,
            "conflict": 409,
        }[mode]
    )
    payload = response.json()
    after_write = mode in {"after_write", "after_write_validation"}
    assert payload["outcome"] == ("unknown" if after_write else "not_applied")
    assert effects == (["saved"] if after_write else [])
    assert "private" not in response.text
    if mode in {"rejected", "invalid_submission"}:
        assert payload["code"] == "VERIFICATION_INPUT_REJECTED"
        assert payload["retryable"] is False
        assert payload["next_action"] == "read_and_review"
        assert response.headers["X-Request-ID"] == payload["request_id"] == request_id
    assert closed == [True]
    assert route.mcp_auth_context.get() is None


@pytest.mark.parametrize("busy", [False, True])
async def test_preview_http_distinguishes_busy_from_invalid_without_exposing_database_details(monkeypatch, busy):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from fastmcp.exceptions import ValidationError
    from httpx import ASGITransport, AsyncClient
    from langboard.card_workspace.application.work_plan import WorkPlanRejected
    from langboard_shared.core.security import AuthSecurity

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=["preview_card_work_plan"], user_id=None)
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: None),
    )
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    server = FastMCP("preview-rejection-http")
    rejection = WorkPlanRejected(
        "WORK_PLAN_BUSY" if busy else "WORK_PLAN_INVALID",
        "Safe guidance",
        "retry_preview" if busy else "revise_plan",
        retryable=busy,
    )

    @server.tool(name="preview_card_work_plan")
    def preview() -> dict:
        raise ValidationError(str(rejection)) from rejection

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/preview")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool("preview_card_work_plan", request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/preview", json={}, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"})
    assert response.status_code == (503 if busy else 400)
    assert response.json() == dict(
        code=rejection.code,
        message="Safe guidance",
        request_id=rejection.request_id,
        outcome="not_applied",
        retryable=busy,
        next_action=rejection.next_action,
    )
    assert response.headers["X-Request-ID"] == rejection.request_id


@pytest.mark.parametrize("mode", ["ineligible", "unavailable", "late_failure"])
async def test_member_preflight_http_does_not_classify_late_failures_as_unapplied(monkeypatch, mode):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from fastmcp.exceptions import ValidationError
    from httpx import ASGITransport, AsyncClient
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.services.factory.ProjectService import (
        ProjectMemberCandidatesUnavailable,
        ProjectMemberNotEligible,
    )

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=["add_project_people"], user_id=None)
    closed = []
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(
            mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
        ),
    )
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    effects = []
    server = FastMCP("member-preflight-http")

    @server.tool(name="add_project_people")
    def add_people() -> dict:
        if mode == "late_failure":
            effects.append("saved")
            raise ValueError("Private directory detail")
        error_type = ProjectMemberCandidatesUnavailable if mode == "unavailable" else ProjectMemberNotEligible
        rejection = error_type("Private directory detail")
        raise ValidationError(str(rejection)) from rejection

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/members")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool("add_project_people", request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/members", json={}, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"})
    payload = response.json()
    assert response.status_code == {"ineligible": 400, "unavailable": 503, "late_failure": 500}[mode]
    assert "Private directory detail" not in response.text
    assert payload["outcome"] == ("unknown" if mode == "late_failure" else "not_applied")
    assert effects == (["saved"] if mode == "late_failure" else [])
    if mode != "late_failure":
        assert payload["retryable"] is False
        assert payload["code"] == (
            "PROJECT_MEMBER_CANDIDATES_UNAVAILABLE" if mode == "unavailable" else "PROJECT_MEMBER_NOT_ELIGIBLE"
        )
        assert payload["next_action"] == ("contact_administrator" if mode == "unavailable" else "search_project_people")
    assert closed == [True]
    assert route.mcp_auth_context.get() is None


def test_accessible_type_rejects_wrong_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared native wrapper keeps user-only and bot-only tools isolated."""

    class FakeUser:
        pass

    class FakeBot:
        pass

    monkeypatch.setattr(Server, "User", FakeUser)
    monkeypatch.setattr(Server, "Bot", FakeBot)
    monkeypatch.setattr(
        Server.McpTool,
        "get_tool",
        lambda name: {"accessible_type": "bot"} if name == "bot_tool" else None,
    )

    assert Server.McpServer._validate_auth(FakeBot(), "bot_tool") is True
    assert Server.McpServer._validate_auth(FakeUser(), "bot_tool") is False
    assert Server.McpServer._validate_auth(SimpleNamespace(), "bot_tool") is False


@pytest.mark.parametrize("conflict", [True, False])
@pytest.mark.parametrize("name", ["apply_card_work_plan", "preview_card_work_plan"])
async def test_native_http_dispatch_distinguishes_review_conflict_from_unknown_write(monkeypatch, conflict, name):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from langboard.card_workspace.domain import WorkPlanConflict
    from langboard_shared.core.security import AuthSecurity

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    closed = []
    service = SimpleNamespace(
        mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
    )
    monkeypatch.setattr(route, "DomainService", lambda: service)
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    effects = []
    server = FastMCP("work-plan-conflict-http")

    @server.tool(name=name)
    def apply() -> dict:
        if conflict:
            raise WorkPlanConflict("Work plan changed after review; preview again")
        effects.append("saved")
        # Identical text must not turn an untyped failure after persistence into a safe conflict.
        raise ValueError("Work plan changed after review; preview again")

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/mcp/tools/{tool_name}")
    async def dispatch(tool_name: str, request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(tool_name, request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/mcp/tools/{name}", json={}, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"}
        )
    payload = response.json()
    assert response.status_code == (409 if conflict else 500)
    assert payload["outcome"] == ("not_applied" if conflict else "unknown")
    if conflict:
        assert payload["code"] == "REVISION_CONFLICT"
        assert payload["retryable"] is False
        assert payload["next_action"] == "read_and_review"
    assert effects == ([] if conflict else ["saved"])
    assert closed == [True]
    assert route.mcp_auth_context.get() is None


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"request_uid": "00000000-0000-4000-8000-000000000001"},
        {
            "request_uid": "00000000-0000-4000-8000-000000000001",
            "duplicate_action": "add",
            "confirmation_token": "current",
        },
        {
            "request_uid": "00000000-0000-4000-8000-000000000001",
            "duplicate_action": "replace",
            "confirmation_token": "current",
            "replacement_uid": "file",
        },
        {"request_uid": "00000000-0000-4000-8000-000000000001", "duplicate_action": "cancel"},
        {"request_uid": "invalid"},
    ],
)
async def test_http_versioned_fields_use_modern_validation_without_changing_legacy(monkeypatch, extra):
    import importlib
    from typing import Annotated, Literal
    from uuid import UUID
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from fastmcp.tools import Tool
    from httpx import ASGITransport, AsyncClient
    from langboard.mcp_integration.Providers import without_modern_parameters
    from langboard.mcp_tools.CardMcp import _as_upload_request_uid
    from langboard_shared.core.security import AuthSecurity
    from pydantic import BeforeValidator

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    modern_fields = ("request_uid", "duplicate_action", "confirmation_token", "replacement_uid")
    effects, closed, modern_calls = [], [], []

    async def upload(
        filename: str,
        file_data_base64: str,
        request_uid: Annotated[UUID, BeforeValidator(_as_upload_request_uid)] | None = None,
        duplicate_action: Literal["add", "replace", "cancel"] | None = None,
        confirmation_token: str | None = None,
        replacement_uid: str | None = None,
    ) -> dict:
        payload = {"filename": filename, "file_data_base64": file_data_base64}
        payload.update(
            {
                key: value
                for key, value in {
                    "request_uid": str(request_uid) if request_uid is not None else None,
                    "duplicate_action": duplicate_action,
                    "confirmation_token": confirmation_token,
                    "replacement_uid": replacement_uid,
                }.items()
                if value is not None
            }
        )
        effects.append(payload)
        return {"confirmation_required": True, "confirmation_token": "next", "request_uid": str(request_uid)}

    legacy, modern = FastMCP("legacy", strict_input_validation=True), FastMCP("modern", strict_input_validation=True)
    legacy.add_tool(Tool.from_function(without_modern_parameters(upload, modern_fields), name="upload_card_attachment"))
    modern.add_tool(Tool.from_function(upload, name="upload_card_attachment"))
    group = SimpleNamespace(activated_at=True, tools=["upload_card_attachment"], user_id=None)
    service = SimpleNamespace(
        mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
    )
    monkeypatch.setattr(route, "DomainService", lambda: service)
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user", "modern_only": modern_fields})
    monkeypatch.setattr(route.McpServer, "mcp", legacy)
    monkeypatch.setattr(route.McpServer, "get_modern_command_server", lambda: modern_calls.append(True) or modern)
    app = FastAPI()

    @app.post("/mcp/tools/{tool_name}")
    async def dispatch(tool_name: str, request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(tool_name, request)

    payload = {"filename": "proof.txt", "file_data_base64": "eA==", **extra}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp/tools/upload_card_attachment", json=payload, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"}
        )
    invalid = extra.get("request_uid") == "invalid"
    assert response.status_code == (400 if invalid else 200)
    assert effects == ([] if invalid else [payload])
    if not invalid:
        assert response.json()["result"]["confirmation_required"] is True
        assert response.json()["result"]["confirmation_token"] == "next"
    assert modern_calls == ([True] if extra else [])
    assert closed == [True]
    assert route.mcp_auth_context.get() is None


@pytest.mark.parametrize("name", ["list_project_cards", "search_project_cards"])
@pytest.mark.parametrize("typed", [True, False])
async def test_card_cursor_http_rejection_is_typed_and_non_retryable(monkeypatch, name, typed):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from langboard.card_workspace.domain import CardQueryCursorRejected
    from langboard_shared.core.security import AuthSecurity

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    closed = []
    monkeypatch.setattr(route, "DomainService", lambda: SimpleNamespace(
        mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
    ))
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    server = FastMCP("card-cursor-http")

    @server.tool(name=name)
    def query() -> dict:
        rejection = CardQueryCursorRejected if typed else ValueError
        raise rejection("private cursor and filter values")

    monkeypatch.setattr(route.McpServer, "mcp", server)
    monkeypatch.setattr(route.McpServer, "agent_mcp", server)
    app = FastAPI()

    @app.post("/cursor")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(name, request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/cursor", json={}, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"})
    payload = response.json()
    assert response.status_code == (400 if typed else 500)
    assert payload["outcome"] == ("not_applied" if typed else "unknown")
    assert "private cursor" not in response.text
    if typed:
        assert payload["code"] == "CARD_QUERY_CURSOR_INVALID"
        assert payload["retryable"] is False
        assert payload["next_action"] == "restart_without_cursor"
    assert closed == [True]
    assert route.mcp_auth_context.get() is None


@pytest.mark.parametrize(
    "structured",
    [
        None,
        {"included": [{"attachment_uid": "image"}], "omitted": [], "total_bytes": 4},
    ],
)
def test_image_blocks_survive_http_serialization_with_the_manifest(structured):
    from fastmcp.tools import ToolResult
    from mcp.types import ImageContent, TextContent

    image = ImageContent(type="image", data="ZGF0YQ==", mimeType="image/png")
    note = TextContent(type="text", text='{"total_bytes":4}')
    result = serialize_mcp_result(ToolResult(content=[note, image], structured_content=structured))
    assert result["content"] == [
        note.model_dump(mode="json"),
        image.model_dump(mode="json"),
    ]
    assert result["structuredContent"] == structured


def test_text_only_http_results_keep_the_existing_json_shape():
    from fastmcp.tools import ToolResult
    from mcp.types import TextContent

    manifest = {"included": [], "omitted": [], "total_bytes": 0}
    note = TextContent(type="text", text='{"plain":true}')
    assert serialize_mcp_result(ToolResult(content=[note], structured_content=manifest)) == manifest
    assert serialize_mcp_result(ToolResult(content=[note])) == {"plain": True}


@pytest.mark.parametrize("with_image", [False, True])
async def test_native_image_tool_http_dispatch_preserves_multimodal_result(monkeypatch, with_image):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from fastmcp.tools import ToolResult
    from httpx import ASGITransport, AsyncClient
    from langboard_shared.core.security import AuthSecurity
    from mcp.types import ImageContent, TextContent

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    name = "read_card_images"
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    closed = []
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(
            mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group),
            close=lambda: closed.append(True),
        ),
    )
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    server = FastMCP("image-http")
    manifest = {
        "included": ["image"] if with_image else [],
        "omitted": [],
        "total_bytes": 4 if with_image else 0,
    }
    image = ImageContent(type="image", data="ZGF0YQ==", mimeType="image/png")

    @server.tool(name=name)
    def read() -> ToolResult:
        return ToolResult(
            content=[
                TextContent(type="text", text="manifest"),
                *([image] if with_image else []),
            ],
            structured_content=manifest,
        )

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/images")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(name, request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/images",
            json={},
            headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"},
        )
    assert response.status_code == 200
    result = response.json()["result"]
    if with_image:
        assert result["content"][1] == image.model_dump(mode="json")
        assert result["structuredContent"] == manifest
    else:
        assert result == manifest
    assert closed == [True]
    assert route.mcp_auth_context.get() is None
