"""Native checklist schema dispatch cannot bypass command authorization."""

from datetime import timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import AuthorizationError, ToolError, ValidationError
from langboard.mcp_integration.Providers import create_agent_core_provider
from langboard.mcp_integration.RoleFilter import McpRoleFilter
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.ChecklistActionsMcp import (
    CHECKLIST_ACTION_COMMANDS,
    ChecklistChange,
    PromoteItem,
    change_card_checklist,
)
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, Checkitem, CheckitemTimerRecord, Checklist, Project, ProjectColumn
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.domain.services.factory.ChecklistService import ChecklistService
from langboard_shared.helpers import InfraHelper
from pydantic import TypeAdapter
from sqlalchemy import func, select
from src.api.tests.card_workspace.test_card_change_counts import counts, native_delivery, receipt_count  # noqa: F401


@pytest.fixture
def bot_actor():
    # Dispatch/role unit tests use the native bot transport. External OAuth
    # consent is exercised with persisted users in test_native_board_consent.
    return Bot(
        id=90,
        name="Scoped bot",
        bot_uname="scoped",
        platform="default",
        platform_running_type="default",
        app_api_token="fixture-token",
    )


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"action": "create_list", "title": " Tasks "}, {"title": "Tasks"}),
        ({"action": "add_item", "checklist_uid": "list", "title": "Task"}, {"checklist_uid": "list", "title": "Task"}),
        (
            {"action": "update_list", "checklist_uid": "list", "is_checked": False},
            {"checklist_uid": "list", "is_checked": False},
        ),
        ({"action": "delete_list", "checklist_uid": "list"}, {"checklist_uid": "list"}),
        (
            {"action": "set_completed", "checkitem_uid": "item", "is_checked": False},
            {"checkitem_uid": "item", "is_checked": False},
        ),
        (
            {"action": "update_item", "checkitem_uid": "item", "deadline_at": ""},
            {"checkitem_uid": "item", "deadline_at": ""},
        ),
        ({"action": "delete_item", "checkitem_uid": "item"}, {"checkitem_uid": "item"}),
        (
            {"action": "promote", "checkitem_uid": "item", "project_column_uid": "column"},
            {"checkitem_uid": "item", "project_column_uid": "column"},
        ),
    ],
)
async def test_native_checklist_catalog_and_dispatch_without_tool_group(monkeypatch, bot_actor, change, expected):
    calls = []
    original = McpTool.get_tool("change_card_checklist")
    command = CHECKLIST_ACTION_COMMANDS[change["action"]]

    checklist = {"uid": "list", "checkitems": [], "checkitems_total_count": 0, "checkitems_next_cursor": None}
    payload = {
        "create_list": {"checklist": checklist},
        "add_item": {"checkitem": {"uid": "item"}},
        "update_list": {"checklists": {"items": [checklist], "total_count": 1, "next_cursor": None, "limit": 25}},
        "set_completed": {"checklists": {"items": [checklist], "total_count": 1, "next_cursor": None, "limit": 25}},
        "update_item": {"checklists": {"items": [checklist], "total_count": 1, "next_cursor": None, "limit": 25}},
        "delete_list": {"deleted": True},
        "delete_item": {"deleted": True},
        "promote": {"card": {"uid": "new-card"}, "source_checkitem_uid": "item"},
    }[change["action"]]

    def handler(*args, **kwargs):
        positional_fields = {
            "create_list": ["title"], "add_item": ["checklist_uid", "title"],
            "update_list": ["checklist_uid"], "delete_list": ["checklist_uid"],
            "set_completed": ["checkitem_uid"], "update_item": ["checkitem_uid"],
            "delete_item": ["checkitem_uid"], "promote": ["checkitem_uid", "project_column_uid"],
        }[change["action"]]
        fields = dict(zip(positional_fields, args[3:], strict=True))
        fields.update({key: value for key, value in kwargs.items() if value is not None})
        calls.append({"project_uid": "project", "card_uid": "card", "user_or_bot": bot_actor,
                      "service": service, **fields})
        if change["action"] == "create_list":
            return SimpleNamespace(api_response=lambda: checklist)
        if change["action"] == "add_item":
            return SimpleNamespace(api_response=lambda: {"uid": "item"})
        return True

    from langboard.mcp_integration import Dependencies
    from langboard.mcp_tools import CardMcp

    lifecycle = []
    service = SimpleNamespace(
        close=lambda: lifecycle.append("closed"),
        checklist=SimpleNamespace(create=handler, update_details=handler, delete=handler,
                                  get_api_list_by_card=lambda *a, **kw: [checklist]),
        checkitem=SimpleNamespace(create=handler, update_details=handler, delete=handler, cardify=handler,
                                 get_by_id_like=lambda _: SimpleNamespace(cardified_id=2)),
        card=SimpleNamespace(get_by_id_like=lambda _: SimpleNamespace(
            board_api_response=lambda *a: {"uid": "new-card"})),
    )
    monkeypatch.setattr(CardMcp, "_require_task_card", lambda *a: (object(), "card"))
    factory = Dependencies._factory_dependency
    monkeypatch.setattr(
        Dependencies, "_factory_dependency", lambda _: factory(lambda: lifecycle.append("opened") or service)
    )
    monkeypatch.setattr(CardMcp, command, lambda **kw: pytest.fail("Primitive MCP handler invoked"))
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"change_card_checklist": original})
    monkeypatch.setattr(
        McpTool,
        "get_tool",
        lambda name: original if name == "change_card_checklist" else None,
    )
    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: actor is bot_actor)
    checked = []
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, fn, **kwargs: checked.append(fn) or True)
    server = FastMCP("Native checklist actions")
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "api_key", "user_or_bot": bot_actor})
    try:
        async with Client(server) as client:
            catalog = await client.list_tools()
            assert [tool.name for tool in catalog] == ["change_card_checklist"]
            assert len(catalog[0].input_schema["properties"]["change"]["oneOf"]) == 8
            result = await client.call_tool(
                "change_card_checklist", {"project_uid": "project", "card_uid": "card", "change": change}
            )
            assert result.structured_content == payload
            for invalid in (
                {"action": "delete_item", "checkitem_uid": "item", "title": "unrelated"},
                {"action": "set_completed", "checkitem_uid": "item", "is_checked": "false"},
                {"action": "update_list", "checklist_uid": "list"},
                {"action": "update_item", "checkitem_uid": "item", "title": None},
                {"action": "add_item", "checklist_uid": "list", "title": " "},
                {"action": "promote", "checkitem_uid": "item", "project_column_uid": " "},
            ):
                with pytest.raises(ToolError):
                    await client.call_tool(
                        "change_card_checklist", {"project_uid": "project", "card_uid": "card", "change": invalid}
                    )
        assert calls == [
            {"project_uid": "project", "card_uid": "card", "user_or_bot": bot_actor, "service": service, **expected}
        ]
        assert checked == [change_card_checklist]
        assert lifecycle == ["opened", "closed"]
    finally:
        mcp_auth_context.reset(token)


async def test_promotion_retains_canonical_update_permission(monkeypatch, bot_actor):
    from langboard.mcp_integration import Dependencies
    from langboard.mcp_tools.CardMcp import cardify_card_checkitem

    metadata = McpTool.get_tool("cardify_card_checkitem")
    assert metadata["handler"] is cardify_card_checkitem
    checked = []

    def check_role(actor, handler, **arguments):
        checked.append(handler)
        actions = McpRoleFilter.get_filtered(handler)[1]
        assert actions == [ProjectRoleAction.CardUpdate.value]
        return False

    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", check_role)
    native_factory = Dependencies._factory_dependency
    monkeypatch.setattr(
        Dependencies,
        "_factory_dependency",
        lambda _: native_factory(lambda: pytest.fail("Denied command must not create services")),
    )
    token = mcp_auth_context.set({"transport": "api_key", "user_or_bot": bot_actor})
    try:
        with pytest.raises(AuthorizationError, match="Insufficient permissions"):
            await McpServer._wrap_tool("change_card_checklist", change_card_checklist)(
                project_uid="project",
                card_uid="card",
                change=PromoteItem(action="promote", checkitem_uid="item", project_column_uid="column"),
            )
        assert checked == [change_card_checklist]
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"deleted": False},
        {"deleted": 1},
        {"deleted": True, "checkitem": {"uid": "item"}},
        {"card": {"uid": "card"}},
        {"source_checkitem_uid": "item", "deleted": True},
    ],
)
def test_checklist_action_output_rejects_missing_mixed_or_incomplete_result(payload):
    from langboard.mcp_integration.CreationOutputs import ChecklistActionOutput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ChecklistActionOutput.model_validate(payload)




@pytest.fixture
def native_checklists(counts, native_delivery, monkeypatch):  # noqa: F811
    from langboard.mcp_tools import CardMcp
    from langboard_shared.domain.services.factory.CardService import CardService

    f = counts
    CheckitemTimerRecord.__table__.create(f.engine, checkfirst=True)
    lists, items = f.get(ChecklistService), f.get(CheckitemService)
    monkeypatch.setattr(f.cards, "remove_completion_checklist", lambda *a: None)
    monkeypatch.setattr(f.cards, "ensure_completion_checklist", lambda *a: None)
    monkeypatch.setattr(import_module(CheckitemService.__module__), "execution_readiness_uow",
                        import_module(CardService.__module__).execution_readiness_uow)
    checklist = lists.create(f.user, f.project, f.card, "Before", dispatch_effects=False)
    item = items.create(f.user, f.project, f.card, checklist, "Before item", dispatch_effects=False)
    service = SimpleNamespace(checklist=lists, checkitem=items, card=f.cards,
                             project_column=SimpleNamespace(get_by_id_like=lambda uid:
                                 InfraHelper.get_by_id_like(ProjectColumn, uid)))
    require_task_card = CardMcp._require_task_card
    monkeypatch.setattr(CardMcp, "_require_task_card", lambda *a: (f.project, f.card))
    native_delivery.clear()

    def invoke(surface, action, **fields):
        if surface == "typed":
            change = TypeAdapter(ChecklistChange).validate_python({"action": action, **fields})
            return change_card_checklist(f.project.get_uid(), f.card.get_uid(), change, f.user, service)
        return getattr(CardMcp, CHECKLIST_ACTION_COMMANDS[action])(
            f.project.get_uid(), f.card.get_uid(), user_or_bot=f.user, service=service, **fields
        )

    def reread(model, key):
        with DbSession.atomic() as db:
            return db.exec(SqlBuilder.select.table(model).where(model.id == key)).first()

    def card_count():
        with f.engine.connect() as db:
            return db.execute(select(func.count()).select_from(Card)).scalar_one()

    return SimpleNamespace(f=f, lists=lists, items=items, checklist=checklist, item=item, service=service,
                           effects=native_delivery, invoke=invoke, reread=reread, card_count=card_count,
                           require_task_card=require_task_card)


@pytest.mark.parametrize("surface", ["legacy", "typed"])
@pytest.mark.parametrize("kind", ["list", "item"])
def test_combined_update_failure_rolls_back_all_fields_receipts_and_effects(native_checklists, monkeypatch, surface, kind):
    n = native_checklists
    before = receipt_count(n.f)
    owner = n.lists if kind == "list" else n.items
    monkeypatch.setattr(owner, "toggle_checked", lambda *a, **kw: False)
    fields = {"title": "Changed", "is_checked": True}
    if kind == "item":
        fields.update(checkitem_uid=n.item.get_uid(), deadline_at="2030-04-03T12:00:00+00:00")
    else:
        fields["checklist_uid"] = n.checklist.get_uid()
    with pytest.raises(ValueError, match="not found in card"):
        n.invoke(surface, "update_" + kind, **fields)
    model, key = (Checklist, n.checklist.id) if kind == "list" else (Checkitem, n.item.id)
    current = n.reread(model, key)
    assert current.title == ("Before" if kind == "list" else "Before item")
    assert current.is_checked is False
    if kind == "item":
        assert current.deadline_at is None
    assert receipt_count(n.f) == before
    assert n.effects == []


@pytest.mark.parametrize("surface", ["legacy", "typed"])
@pytest.mark.parametrize("kind", ["list", "item"])
def test_combined_update_success_and_explicit_set_state_noop(native_checklists, surface, kind):
    n = native_checklists
    fields = {"title": "Changed", "is_checked": True}
    uid_field = "checklist_uid" if kind == "list" else "checkitem_uid"
    fields[uid_field] = n.checklist.get_uid() if kind == "list" else n.item.get_uid()
    if kind == "item":
        fields["deadline_at"] = "2030-04-03T12:00:00+00:00"
    result = n.invoke(surface, "update_" + kind, **fields)
    assert len(result["checklists"].items) == 1
    assert n.effects and all(not effect[1] for effect in n.effects)
    current = n.reread(Checklist if kind == "list" else Checkitem, n.checklist.id if kind == "list" else n.item.id)
    assert current.title == "Changed" and current.is_checked is True
    if kind == "item":
        deadline = current.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        assert deadline.astimezone(timezone.utc).isoformat() == "2030-04-03T12:00:00+00:00"
    before = receipt_count(n.f)
    n.effects.clear()
    n.invoke(surface, "update_list" if kind == "list" else "set_completed", **{uid_field: fields[uid_field], "is_checked": True})
    assert receipt_count(n.f) == before
    assert n.effects == []
    n.invoke(surface, "update_list" if kind == "list" else "set_completed", **{uid_field: fields[uid_field], "is_checked": False})
    assert n.reread(Checklist if kind == "list" else Checkitem, current.id).is_checked is False


@pytest.mark.parametrize("surface", ["legacy", "typed"])
def test_invalid_deadline_and_wrong_item_or_checklist_reject_before_writes(native_checklists, surface):
    n = native_checklists
    before = receipt_count(n.f)
    for action, fields in (
        ("update_item", {"checkitem_uid": n.item.get_uid(), "title": "Changed", "deadline_at": "invalid"}),
        ("update_item", {"checkitem_uid": "missing", "title": "Changed"}),
        ("update_list", {"checklist_uid": "missing", "title": "Changed"}),
    ):
        with pytest.raises((ValueError, ValidationError)):
            n.invoke(surface, action, **fields)
    assert n.reread(Checkitem, n.item.id).title == "Before item"
    assert n.reread(Checklist, n.checklist.id).title == "Before"
    assert receipt_count(n.f) == before and n.effects == []


@pytest.mark.parametrize("kind", ["list", "item"])
def test_native_combined_owner_rejects_wrong_card_and_non_boolean(native_checklists, kind):
    n = native_checklists
    with DbSession.atomic() as db:
        other = Card(project_id=n.f.project.id, project_column_id=n.f.column.id, title="Other")
        db.insert(other)
    owner, entity = (n.lists, n.checklist) if kind == "list" else (n.items, n.item)
    before = receipt_count(n.f)
    assert owner.update_details(n.f.user, n.f.project, other, entity, title="Changed") is None
    with pytest.raises(ValueError, match="boolean"):
        owner.update_details(n.f.user, n.f.project, n.f.card, entity, title="Changed", is_checked=1)
    assert receipt_count(n.f) == before and n.effects == []


@pytest.mark.parametrize("surface", ["legacy", "typed"])
@pytest.mark.parametrize("column_kind", ["missing", "foreign", "archive", "deleted"])
def test_promotion_rejects_non_active_project_columns_without_writes(native_checklists, surface, column_kind):
    n = native_checklists
    uid = "missing"
    if column_kind != "missing":
        with DbSession.atomic() as db:
            project = n.f.project
            if column_kind == "foreign":
                project = Project(owner_id=n.f.user.id, title="Foreign")
                db.insert(project)
            column = ProjectColumn(project_id=project.id, name="Rejected", is_archive=column_kind == "archive",
                                   deleted_at=SafeDateTime.now() if column_kind == "deleted" else None)
            db.insert(column)
            uid = column.get_uid()
    before, cards = receipt_count(n.f), n.card_count()
    with pytest.raises(ValueError):
        n.invoke(surface, "promote", checkitem_uid=n.item.get_uid(), project_column_uid=uid)
    assert n.reread(Checkitem, n.item.id).cardified_id is None
    assert n.card_count() == cards and receipt_count(n.f) == before and n.effects == []


@pytest.mark.parametrize("surface", ["legacy", "typed"])
def test_promotion_failure_rolls_back_and_unknown_readback_preserves_original_link(native_checklists, monkeypatch, surface):
    n = native_checklists
    before, cards = receipt_count(n.f), n.card_count()
    original = n.f.repo.checkitem.update

    def fail_after_insert(item):
        if item.cardified_id:
            raise RuntimeError("promotion write failure")
        return original(item)

    monkeypatch.setattr(n.f.repo.checkitem, "update", fail_after_insert)
    with pytest.raises(RuntimeError, match="promotion write failure"):
        n.invoke(surface, "promote", checkitem_uid=n.item.get_uid(), project_column_uid=n.f.column.get_uid())
    assert n.card_count() == cards and n.reread(Checkitem, n.item.id).cardified_id is None
    assert receipt_count(n.f) == before and n.effects == []
    monkeypatch.setattr(n.f.repo.checkitem, "update", original)
    # The owner commits before this transport projection. An unreadable result is
    # an unknown outcome, never authority to replay the business operation.
    monkeypatch.setattr(n.f.cards, "get_by_id_like", lambda *a: None)
    owner = Mock(wraps=n.items.cardify)
    monkeypatch.setattr(n.items, "cardify", owner)
    with pytest.raises(RuntimeError, match="could not be read back"):
        n.invoke(surface, "promote", checkitem_uid=n.item.get_uid(), project_column_uid=n.f.column.get_uid())
    owner.assert_called_once()
    persisted = n.reread(Checkitem, n.item.id)
    assert persisted.cardified_id is not None and n.card_count() == cards + 1
    assert n.effects and all(not effect[1] for effect in n.effects)
    with pytest.raises(ValueError):
        n.invoke(surface, "promote", checkitem_uid=n.item.get_uid(), project_column_uid=n.f.column.get_uid())
    assert n.reread(Checkitem, n.item.id).cardified_id == persisted.cardified_id
    assert n.card_count() == cards + 1


@pytest.mark.parametrize("surface", ["legacy", "typed"])
@pytest.mark.parametrize("action", ["update_list", "update_item", "set_completed", "promote"])
def test_native_current_actor_visibility_rejects_other_private_owner(native_checklists, monkeypatch, surface, action):
    from langboard.mcp_tools import CardMcp
    from langboard_shared.Env import Env

    n = native_checklists
    monkeypatch.setattr(CardMcp, "_require_task_card", n.require_task_card)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == n.f.card.id)).first()
        current.visibility = "PRIVATE"
        current.owner_user_id = n.f.other.id
        current.created_by_user_id = n.f.other.id
        db.update(current)
    before = receipt_count(n.f)
    fields = ({"checklist_uid": n.checklist.get_uid()} if action == "update_list"
              else {"checkitem_uid": n.item.get_uid()})
    fields.update({"project_column_uid": n.f.column.get_uid()} if action == "promote" else {"is_checked": True})
    # Native primary reads validate the caller and channel; no resolver or policy
    # is mocked. A stale shared-card object cannot authorize a private mutation.
    with pytest.raises(ValidationError, match="CARD_UNAVAILABLE"):
        n.invoke(surface, action, **fields)
    assert receipt_count(n.f) == before and n.effects == []
    assert n.reread(Checklist, n.checklist.id).is_checked is False
    assert n.reread(Checkitem, n.item.id).is_checked is False
    assert n.reread(Checkitem, n.item.id).cardified_id is None
