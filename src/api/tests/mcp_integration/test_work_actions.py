"""Registered canonical work preserves native persistence, authority and receipts."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from langboard.mcp_integration.Providers import create_native_agent_provider
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_integration.ToolGroupMiddleware import ToolGroupMiddleware
from langboard.mcp_tools import CardMcp
from langboard.mcp_tools.WorkActionsMcp import work
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardExecutionGeneration,
    CardVerificationRecord,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    ProjectRole,
)
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from langboard_shared.publishers import CardPublisher, CheckitemPublisher
from langboard_shared.tasks.activities import CardCheckitemActivityTask
from langboard_shared.tasks.bots import CardCheckitemBotTask
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session
from test_native_board_consent import native, native_engine  # noqa: F401


def uid(value):
    return SnowflakeID(value).to_short_code()


@pytest.fixture
def registered_work(native, monkeypatch):  # noqa: F811 - imported pytest fixture
    engine, user, other, boards = native
    # Retain real owner services and repositories, including source cursor writes.
    Card.metadata.create_all(engine)
    CardExecutionGeneration.__table__.create(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as db:
            db.execute(text("CREATE SEQUENCE content_change_seq"))
    for cls in (CardPublisher, CheckitemPublisher, CardCheckitemActivityTask, CardCheckitemBotTask):
        for name in dir(cls):
            if not name.startswith("_") and callable(getattr(cls, name)):
                monkeypatch.setattr(cls, name, Mock())
    with Session(engine) as db:
        db.add(Checklist(id=700, card_id=210, title="Work"))
        db.flush()
        db.add(Checkitem(id=710, checklist_id=700, title="Task", user_id=1))
        db.commit()
    connection = connect_native_client(user, "canonical-work-tests")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    metadata = McpTool.get_tool("work")
    assert metadata["handler"] is work and metadata["accessible_type"] == "user"
    registry = {
        name: McpTool.get_tool(name)
        for name in ("work", "change_card_checkitem_work", "record_card_verification_evidence")
    }
    monkeypatch.setattr(McpTool, "get_tools", lambda: registry)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: registry.get(name))
    # Calling either decorated primitive is a regression. Helpers must own dispatch.
    monkeypatch.setattr(CardMcp, "change_card_checkitem_work", Mock(side_effect=AssertionError("primitive redispatch")))
    monkeypatch.setattr(
        CardMcp, "record_card_verification_evidence", Mock(side_effect=AssertionError("primitive redispatch"))
    )
    server = FastMCP("Canonical native work", strict_input_validation=True)
    server.add_provider(create_native_agent_provider(McpServer._wrap_tool))
    auth = {"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id}
    return engine, user, other, boards, connection, server, auth


async def test_registered_work_timers_verification_and_fences(registered_work):
    engine, user, _, boards, _, server, auth = registered_work
    token = mcp_auth_context.set(auth)
    arguments = {"project_uid": boards[0].get_uid(), "card_uid": uid(210)}
    try:
        async with Client(server) as client:
            catalog = {tool.name: tool for tool in await client.list_tools()}
            assert {"work", "change_card_checkitem_work", "record_card_verification_evidence"} <= set(catalog)
            assert "type" in catalog["work"].output_schema and catalog["work"].output_schema["type"] == "object"
            for action in ("start", "pause", "resume", "stop", "start", "complete"):
                result = await client.call_tool(
                    "work", {**arguments, "change": {"action": action, "checkitem_uid": uid(710)}}
                )
                assert set(result.structured_content) == {"checkitem_uid", "status", "is_checked", "user_uid"}
                assert result.structured_content["user_uid"] == user.get_uid()
            with Session(engine) as db:
                assert db.get(Checkitem, 710).is_checked
                assert len(db.scalars(select(CheckitemTimerRecord)).all()) == 6
                seq = db.get(Card, 210).last_change_seq
            change = {
                "action": "record_verification",
                "expected_change_seq": seq,
                "decision": "verified",
                "required_checkitem_uids": [uid(710)],
                "evidence": [
                    {"reference": "run:card", "source_revision": "source", "environment": "isolated"},
                    {
                        "reference": "run:item",
                        "source_revision": "source",
                        "environment": "isolated",
                        "checkitem_uid": uid(710),
                    },
                ],
            }
            receipt = (await client.call_tool("work", {**arguments, "change": change})).structured_content
            assert set(receipt) == {"verification"}
            record = receipt["verification"]
            assert record["recorded_by_user_uid"] == user.get_uid() and record["source_change_seq"] == seq
            with pytest.raises(ToolError, match="Verification changed"):
                await client.call_tool("work", {**arguments, "change": change})
            change.update(expected_record_uid=record["uid"], decision="partial")
            next_record = (await client.call_tool("work", {**arguments, "change": change})).structured_content[
                "verification"
            ]
            assert next_record["uid"] != record["uid"]
            change.update(expected_record_uid=next_record["uid"], expected_change_seq=seq + 1)
            with pytest.raises(ToolError, match="Card changed"):
                await client.call_tool("work", {**arguments, "change": change})
            with Session(engine) as db:
                assert len(db.scalars(select(CardVerificationRecord)).all()) == 2
                assert db.get(Card, 210).last_change_seq == seq
                assert db.get(Card, 210).project_column_id == 110
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "change",
    [
        {"action": "start", "checkitem_uid": "item", "decision": "verified"},
        {"action": "start", "checkitem_uid": "item", "replace_active": 1},
        {"action": "start", "checkitem_uid": " "},
        {"action": "record_verification", "expected_change_seq": "0", "decision": "partial", "evidence": []},
        {
            "action": "record_verification",
            "expected_change_seq": 0,
            "decision": "partial",
            "replace_active": True,
            "evidence": [{"reference": "r", "source_revision": "s", "environment": "e"}],
        },
        {
            "action": "record_verification",
            "expected_change_seq": 0,
            "decision": "verified",
            "required_checkitem_uids": ["missing"],
            "evidence": [{"reference": "r", "source_revision": "s", "environment": "e"}],
        },
    ],
)
async def test_malformed_work_has_no_writes(registered_work, change):
    engine, _, _, boards, _, server, auth = registered_work
    token = mcp_auth_context.set(auth)
    try:
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "work", {"project_uid": boards[0].get_uid(), "card_uid": uid(210), "change": change}
                )
        with Session(engine) as db:
            assert not db.scalars(select(CardVerificationRecord)).all()
            assert not db.scalars(select(CheckitemTimerRecord)).all()
            assert db.get(Card, 210).last_change_seq == 0
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "denial",
    ["read-only", "role", "bot", "unconsented-board", "hidden", "wrong-owner", "foreign-item", "primitive-grant"],
)
@pytest.mark.parametrize("verification", [False, True])
async def test_work_authority_rejections(registered_work, denial, monkeypatch, verification):
    engine, user, other, boards, connection, server, auth = registered_work
    arguments = {
        "project_uid": boards[0].get_uid(),
        "card_uid": uid(210),
        "change": {"action": "start", "checkitem_uid": uid(710)},
    }
    if verification:
        arguments["change"] = {
            "action": "record_verification",
            "expected_change_seq": 0,
            "decision": "partial",
            "evidence": [{"reference": "r", "source_revision": "s", "environment": "e"}],
        }
    if verification and denial in {"wrong-owner", "foreign-item"}:
        pytest.skip("Verification has reviewer authority, independent of timer ownership")
    if denial == "read-only":
        save_native_board_consent(user, connection.id, 10, ["read"], 1)
    elif denial == "role":
        with Session(engine) as db:
            db.execute(update(ProjectRole).where(ProjectRole.project_id == 10).values(actions=["read"]))
            db.commit()
    elif denial == "bot":
        auth = {
            "transport": "api_key",
            "user_or_bot": Bot(
                id=90,
                name="Bot",
                bot_uname="bot",
                platform="default",
                platform_running_type="default",
                app_api_token="fixture",
            ),
        }
    elif denial == "unconsented-board":
        arguments.update(project_uid=boards[1].get_uid(), card_uid=uid(220))
    elif denial == "hidden":
        with Session(engine) as db:
            db.execute(
                update(Card)
                .where(Card.id == 210)
                .values(visibility="PRIVATE", owner_user_id=other.id, created_by_user_id=other.id)
            )
            db.commit()
    elif denial == "wrong-owner":
        with Session(engine) as db:
            db.execute(update(Checkitem).where(Checkitem.id == 710).values(user_id=other.id))
            db.commit()
    elif denial == "foreign-item":
        arguments["card_uid"] = uid(220)
    else:
        server.add_middleware(ToolGroupMiddleware())
        auth = {
            **auth,
            "transport": "api_key",
            "tool_group": SimpleNamespace(
                activated_at=True, tools=["change_card_checkitem_work", "record_card_verification_evidence"]
            ),
        }
    token = mcp_auth_context.set(auth)
    try:
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("work", arguments)
        with Session(engine) as db:
            assert not db.scalars(select(CheckitemTimerRecord)).all()
            assert not db.scalars(select(CardVerificationRecord)).all()
    finally:
        mcp_auth_context.reset(token)


async def test_canonical_grant_does_not_grant_primitives(registered_work):
    _, _, _, boards, _, server, auth = registered_work
    server.add_middleware(ToolGroupMiddleware())
    auth = {**auth, "transport": "api_key", "tool_group": SimpleNamespace(activated_at=True, tools=["work"])}
    token = mcp_auth_context.set(auth)
    arguments = {"project_uid": boards[0].get_uid(), "card_uid": uid(210)}
    try:
        async with Client(server) as client:
            assert "work" in {tool.name for tool in await client.list_tools()}
            await client.call_tool("work", {**arguments, "change": {"action": "start", "checkitem_uid": uid(710)}})
            for primitive, fields in [
                ("change_card_checkitem_work", {"action": "pause", "checkitem_uid": uid(710)}),
                (
                    "record_card_verification_evidence",
                    {"expected_change_seq": 0, "decision": "partial", "evidence": []},
                ),
            ]:
                with pytest.raises(ToolError, match="not allowed"):
                    await client.call_tool(primitive, {**arguments, **fields})
    finally:
        mcp_auth_context.reset(token)


async def test_canonical_uses_one_injected_actor_and_service(registered_work, monkeypatch):
    from langboard.mcp_integration import Dependencies

    _, user, _, boards, _, server, auth = registered_work
    resources, closed, owners = [], [], []
    factory_dependency = Dependencies._factory_dependency

    def observed_factory(factory):
        def create():
            value = factory()
            resources.append(value)
            close = value.close

            def observed_close():
                closed.append(value)
                close()

            value.close = observed_close
            return value

        return factory_dependency(create)

    helper = CardMcp._change_card_checkitem_work

    def observed_owner(project_uid, card_uid, checkitem_uid, action, actor, service, replace_active=False):
        owners.append((actor, service))
        return helper(project_uid, card_uid, checkitem_uid, action, actor, service, replace_active)

    monkeypatch.setattr(Dependencies, "_factory_dependency", observed_factory)
    monkeypatch.setattr(CardMcp, "_change_card_checkitem_work", observed_owner)
    server = FastMCP("Observed canonical dependency lifecycle", strict_input_validation=True)
    server.add_provider(create_native_agent_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set(auth)
    try:
        async with Client(server) as client:
            await client.call_tool(
                "work",
                {
                    "project_uid": boards[0].get_uid(),
                    "card_uid": uid(210),
                    "change": {"action": "start", "checkitem_uid": uid(710)},
                },
            )
        assert len(resources) == 1 and closed == resources
        assert owners == [(user, resources[0])]
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("entrypoint", ["work", "record_card_verification_evidence"])
async def test_both_verification_entrypoints_reject_hidden_private_card(registered_work, entrypoint):
    engine, _, other, boards, _, server, auth = registered_work
    with Session(engine) as db:
        db.execute(
            update(Card)
            .where(Card.id == 210)
            .values(visibility="PRIVATE", owner_user_id=other.id, created_by_user_id=other.id)
        )
        db.commit()
    fields = {
        "expected_change_seq": 0,
        "decision": "partial",
        "evidence": [{"reference": "r", "source_revision": "s", "environment": "e"}],
    }
    arguments = {"project_uid": boards[0].get_uid(), "card_uid": uid(210)}
    arguments.update({"change": {"action": "record_verification", **fields}} if entrypoint == "work" else fields)
    token = mcp_auth_context.set(auth)
    try:
        async with Client(server) as client:
            with pytest.raises(ToolError, match="CARD_UNAVAILABLE"):
                await client.call_tool(entrypoint, arguments)
        with Session(engine) as db:
            assert not db.scalars(select(CardVerificationRecord)).all()
            assert db.get(Card, 210).last_change_seq == 0
    finally:
        mcp_auth_context.reset(token)
