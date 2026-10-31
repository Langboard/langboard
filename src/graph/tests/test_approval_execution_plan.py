"""Approval snapshots must describe the exact business arguments executed."""

from copy import deepcopy
from types import SimpleNamespace
from typing import TypedDict
import pytest
from langboard_shared.domain.models import GraphApprovalRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from langboard_graph.core.graph.default import nodes, tooling


@pytest.mark.parametrize("kind", ["initial", "tool"])
@pytest.mark.parametrize(
    ("scope", "expected_table", "expected_uid"),
    [
        ("project_wiki", "project_wiki", "selected-wiki"),
        ("project_column", "project_column", "selected-column"),
        ("project", "project", "selected-project"),
        ("card", "card", "selected-card"),
        (None, "card", "selected-card"),
        ("unknown", "card", "selected-card"),
    ],
)
def test_approval_scope_uid_matches_selected_document_type(kind, scope, expected_table, expected_uid):
    variables = {
        "project_uid": "selected-project",
        "rest_data": {
            "project_uid": "selected-project",
            "project_column_uid": "selected-column",
            "card_uid": "selected-card",
            "project_wiki_uid": "selected-wiki",
            "chat_scope": scope,
        },
    }
    tweaks = {"Graph": {"approval_request": True}, "LangboardCalledVariablesComponent": variables}
    if kind == "initial":
        request = nodes._get_approval_request(tweaks, {"thread_id": "thread", "session_id": "session"})
    else:
        request = tooling.create_langboard_api_tool_approval_request(
            tweaks,
            {"api_name": "edit_document", "schema": {"permission": "edit"}, "variables": variables},
            {},
            tool_name="edit_document",
            thread_id="thread",
            session_id="session",
        )
    assert (request["scope_table"], request["scope_uid"]) == (expected_table, expected_uid)


@pytest.mark.parametrize("kind", ["initial", "tool"])
def test_missing_selected_wiki_uid_never_falls_back_to_an_unrelated_card(kind):
    variables = {"project_uid": "project", "rest_data": {"chat_scope": "project_wiki", "card_uid": "other-card"}}
    tweaks = {"Graph": {"approval_request": True}, "LangboardCalledVariablesComponent": variables}
    if kind == "initial":
        request = nodes._get_approval_request(tweaks, {})
    else:
        request = tooling.create_langboard_api_tool_approval_request(
            tweaks,
            {"api_name": "edit_document", "schema": {"permission": "edit"}, "variables": variables},
            {},
            tool_name="edit_document",
            thread_id="thread",
            session_id="session",
        )
    assert request["scope_table"] == "project_wiki"
    assert request["scope_uid"] is None


def native_resume(request):
    run = SimpleNamespace(
        graph_thread_id=request["thread_id"],
        graph_session_id=request["session_id"],
        client_task_id="native-run",
        attempt=1,
        get_uid=lambda: "native-uid",
    )
    return {
        "approved": True,
        "approval_uid": "approval-uid",
        "execution_plan": GraphApprovalRequest.freeze_execution_plan(request, "native-interrupt", run),
    }


@pytest.mark.asyncio
async def test_orchestration_approval_snapshot_matches_executed_arguments(monkeypatch):
    observed, writes = [], []

    async def record(form: dict):
        writes.append(deepcopy(form))
        return "recorded"

    tool = StructuredTool.from_function(coroutine=record, name="record", description="Record bypass")

    class Model:
        def bind_tools(self, tools):
            return self

    async def model(_, messages):
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record",
                    "id": "call-1",
                    "type": "tool_call",
                    "args": {"form": {"allowed": False, "risk_level": "high", "action_type": "delete"}},
                }
            ],
        )

    def approve(request):
        observed.append(deepcopy(request))
        return native_resume(request)

    monkeypatch.setattr(nodes, "_invoke_chat_model", model)
    monkeypatch.setattr(nodes, "interrupt", approve)
    await nodes._invoke_agent(
        Model(),
        [HumanMessage(content="record")],
        [tool],
        tweaks={"Graph": {"api_approval_policy": {"write": "ask"}, "execution_plan_version": 1}},
        state={"thread_id": "thread", "session_id": "session"},
        api_tool_context={"record": {"api_name": "record_orchestration_bypass", "schema": {"permission": "write"}}},
    )
    assert len(writes) == 1
    assert observed[0]["request_payload"]["tool_args"]["form"] == writes[0]


class FixtureModel:
    def bind_tools(self, tools):
        return self


def fixture_graph(monkeypatch, *, calls=None, variables=None, capability=1):
    writes, models = [], []
    variables = variables if variables is not None else {}
    graph_config = {"api_approval_policy": {"create": "ask"}}
    if capability is not None:
        graph_config["execution_plan_version"] = capability

    async def write(form: dict):
        writes.append(deepcopy(form))
        return deepcopy(form)

    tool = StructuredTool.from_function(coroutine=write, name="write", description="Write fixture")
    supplied = calls or [
        {"name": "write", "id": "first", "type": "tool_call", "args": {"form": {"title": "A"}}},
        {"name": "write", "id": "second", "type": "tool_call", "args": {"form": {"title": "B"}}},
    ]

    async def model(_, messages):
        models.append(1)
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="done")
        return AIMessage(content="", tool_calls=deepcopy(supplied))

    monkeypatch.setattr(nodes, "_invoke_chat_model", model)

    class State(TypedDict):
        response: str

    async def agent(state):
        result, receipts = await nodes._invoke_agent(
            FixtureModel(),
            [HumanMessage(content="write")],
            [tool],
            tweaks={"Graph": deepcopy(graph_config)},
            state={"thread_id": "thread", "session_id": "session"},
            api_tool_context={
                "write": {"api_name": "write_fixture", "schema": {"permission": "create"}, "variables": variables}
            },
        )
        return {"response": result.content}

    builder = StateGraph(State)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder, writes, models


@pytest.mark.asyncio
async def test_native_plan_checkpoint_preserves_sequential_approved_calls(monkeypatch):
    builder, writes, models = fixture_graph(monkeypatch)
    saver = InMemorySaver()
    graph = builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "thread"}}
    first = (await graph.ainvoke({}, config))["__interrupt__"][0].value
    assert writes == []
    assert len(models) == 1
    first_plan = deepcopy(first["execution_plan"])
    # Recompile, retaining the saver: preparation and model must remain checkpointed.
    second_result = await builder.compile(checkpointer=saver).ainvoke(Command(resume=native_resume(first)), config)
    second = second_result["__interrupt__"][0].value
    assert writes == [{"title": "A"}]
    assert len(models) == 1
    assert second["execution_plan"]["plan_id"] != first_plan["plan_id"]
    assert first["execution_plan"] == first_plan
    result = await graph.ainvoke(Command(resume=native_resume(second)), config)
    assert result["response"] == "done"
    assert writes == [{"title": "A"}, {"title": "B"}]
    assert len(models) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "plan_id",
        "tool_name",
        "action_type",
        "api_name",
        "args_hash",
        "thread_id",
        "session_id",
        "arguments",
        "version",
        "approval_uid",
        "missing_plan",
    ],
)
async def test_changed_or_unbound_approval_never_executes(monkeypatch, field):
    builder, writes, _ = fixture_graph(monkeypatch)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    request = (await graph.ainvoke({}, config))["__interrupt__"][0].value
    resume = native_resume(request)
    if field == "approval_uid":
        resume.pop("approval_uid")
    elif field == "missing_plan":
        resume.pop("execution_plan")
    else:
        resume["execution_plan"][field] = {} if field == "arguments" else "changed"
    result = await graph.ainvoke(Command(resume=resume), config)
    assert writes == []
    # Second call still requires its own approval after the invalid first decision.
    second = result["__interrupt__"][0].value
    assert second["request_payload"]["tool_args"]["form"]["title"] == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [{"rejected": True}, {"status": "cancelled"}, {"status": "expired"}])
async def test_rejection_cancel_and_expiry_never_execute(monkeypatch, decision):
    builder, writes, _ = fixture_graph(monkeypatch)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    await graph.ainvoke({}, config)
    result = await graph.ainvoke(Command(resume=decision), config)
    assert writes == []
    if decision.get("rejected"):
        assert result["__interrupt__"]
    else:
        assert result["response"] == "done"


@pytest.mark.asyncio
async def test_changed_runtime_business_target_after_pause_never_executes(monkeypatch):
    variables = {"project_uid": "project-one"}
    builder, writes, _ = fixture_graph(monkeypatch, variables=variables)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    request = (await graph.ainvoke({}, config))["__interrupt__"][0].value
    variables["project_uid"] = "project-two"
    result = await graph.ainvoke(Command(resume=native_resume(request)), config)
    assert writes == []
    assert result["__interrupt__"]


@pytest.mark.asyncio
async def test_approved_projection_matches_native_http_body_without_credential_hash(monkeypatch):
    import json
    import httpx
    from langboard_graph.core.graph.default import tooling

    requests, observed = [], []
    secret = "test-credential-excluded-from-plan"
    variables = {"project_uid": "project-one", "app_api_token": secret, "rest_data": {"card_uid": "card-one"}}
    schema = {
        "permission": "create",
        "method": "POST",
        "path_params": ["project_uid", "card_uid"],
        "form": {
            "properties": {
                "content": {"type": "object", "properties": {"content": {"type": "string"}}},
                "count": {"type": "integer"},
            }
        },
    }
    tool = tooling._create_api_tool("write_fixture", schema, "https://fixture.invalid", {}, variables)
    metadata = {"api_name": "write_fixture", "schema": schema, "variables": variables}

    def handle(request):
        requests.append(json.loads(request.content))
        assert request.headers[tooling.AuthSecurity.API_TOKEN_HEADER] == secret
        return httpx.Response(200, json={"saved": True})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        tooling.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs)
    )

    async def model(_, messages):
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": tool.name,
                    "id": "native",
                    "type": "tool_call",
                    "args": {"form": {"content": "Approved text"}, "form_count": "2"},
                }
            ],
        )

    def approve(request):
        observed.append(deepcopy(request))
        return native_resume(request)

    monkeypatch.setattr(nodes, "_invoke_chat_model", model)
    monkeypatch.setattr(nodes, "interrupt", approve)
    await nodes._invoke_agent(
        FixtureModel(),
        [HumanMessage(content="write")],
        [tool],
        tweaks={
            "Graph": {"api_approval_policy": {"create": "ask"}, "execution_plan_version": 1},
            "LangboardCalledVariablesComponent": variables,
        },
        state={"thread_id": "thread", "session_id": "session"},
        api_tool_context={tool.name: metadata},
    )
    plan = observed[0]["execution_plan"]
    assert secret not in str(plan)
    assert plan["arguments"]["api_request"]["body"] == requests[0]
    assert requests[0]["request_schemas"][0]["query"] == {"project_uid": "project-one", "card_uid": "card-one"}
    assert requests[0]["request_schemas"][0]["form"]["content"] == {"content": "Approved text"}
    assert requests[0]["request_schemas"][0]["form"]["count"] == 2


@pytest.mark.asyncio
async def test_unexpected_business_argument_mutation_requires_new_approval(monkeypatch):
    args = {"form": {"title": "Approved title"}}
    context = {"write": {"api_name": "write_fixture", "schema": {"permission": "create"}}}
    tweaks = {"Graph": {"api_approval_policy": {"create": "ask"}}}
    state = {"thread_id": "thread", "session_id": "session"}
    request = await nodes._prepare_tool_approval_request(
        "write", args, tweaks=tweaks, state=state, api_tool_context=context, bind_plan=True
    )

    def approve(value):
        decision = native_resume(value)
        args["form"]["title"] = "Unexpected replacement"
        return decision

    monkeypatch.setattr(nodes, "interrupt", approve)
    result = await nodes._get_tool_approval_result(
        "write",
        args,
        tweaks=tweaks,
        state=state,
        api_tool_context=context,
        prepared=True,
        prepared_request=request,
    )
    assert "new approval" in result
    assert request["execution_plan"]["arguments"]["tool_args"]["form"]["title"] == "Approved title"


@pytest.mark.asyncio
async def test_legacy_generic_approval_resume_remains_compatible(monkeypatch):
    builder, writes, _ = fixture_graph(monkeypatch, capability=None)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    first = (await graph.ainvoke({}, config))["__interrupt__"][0].value
    assert "execution_plan" not in first
    second = (await graph.ainvoke(Command(resume={"approved": True}), config))["__interrupt__"][0].value
    assert "execution_plan" not in second
    assert writes == [{"title": "A"}]
    result = await graph.ainvoke(Command(resume={"approved": True}), config)
    assert result["response"] == "done"
    assert writes == [{"title": "A"}, {"title": "B"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", [True, "1", 2])
async def test_unsupported_capability_never_executes(monkeypatch, capability):
    builder, writes, _ = fixture_graph(monkeypatch, capability=capability)
    graph = builder.compile(checkpointer=InMemorySaver())
    result = await graph.ainvoke({}, {"configurable": {"thread_id": "thread"}})
    assert result["response"] == "done"
    assert writes == []


@pytest.mark.asyncio
async def test_native_edit_restarts_to_fresh_plan_and_requires_separate_approval(monkeypatch):
    builder, writes, models = fixture_graph(
        monkeypatch, calls=[{"name": "write", "id": "first", "type": "tool_call", "args": {"form": {"title": "A"}}}]
    )
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "thread"}}
    graph = builder.compile(checkpointer=saver)
    original = (await graph.ainvoke({}, config))["__interrupt__"][0].value
    frozen = deepcopy(original)
    edit = native_resume(original)
    edit.update(
        approved=False,
        rejected=False,
        edit={
            "plan_id": original["execution_plan"]["plan_id"],
            "args_hash": original["execution_plan"]["args_hash"],
            "tool_args": {"form": {"title": "Edited"}},
        },
    )
    pending = await builder.compile(checkpointer=saver).ainvoke(Command(resume=edit), config)
    fresh = pending["__interrupt__"][0].value
    assert writes == []
    assert original == frozen
    assert fresh["execution_plan"]["plan_id"] != original["execution_plan"]["plan_id"]
    assert fresh["execution_plan"]["args_hash"] != original["execution_plan"]["args_hash"]
    assert fresh["execution_plan"]["arguments"]["tool_args"] == {"form": {"title": "Edited"}}
    assert len(models) == 1
    result = await builder.compile(checkpointer=saver).ainvoke(Command(resume=native_resume(fresh)), config)
    assert result["response"] == "done"
    assert writes == [{"title": "Edited"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["stale_plan", "stale_hash", "mixed", "schema", "credential", "nan"])
async def test_invalid_native_edit_never_executes(monkeypatch, mutation):
    builder, writes, _ = fixture_graph(
        monkeypatch, calls=[{"name": "write", "id": "first", "type": "tool_call", "args": {"form": {"title": "A"}}}]
    )
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    original = (await graph.ainvoke({}, config))["__interrupt__"][0].value
    edit = native_resume(original)
    edit.update(
        approved=False,
        rejected=False,
        edit={
            "plan_id": original["execution_plan"]["plan_id"],
            "args_hash": original["execution_plan"]["args_hash"],
            "tool_args": {"form": {"title": "Edited"}},
        },
    )
    if mutation == "stale_plan":
        edit["edit"]["plan_id"] = "stale"
    elif mutation == "stale_hash":
        edit["edit"]["args_hash"] = "stale"
    elif mutation == "mixed":
        edit["approved"] = True
    elif mutation == "schema":
        edit["edit"]["tool_args"] = {"form": "invalid"}
    elif mutation == "credential":
        edit["edit"]["tool_args"] = {"form": {"app_api_token": "forged"}}
    elif mutation == "nan":
        edit["edit"]["tool_args"] = {"form": {"count": float("nan")}}
    await graph.ainvoke(Command(resume=edit), config)
    assert writes == []
