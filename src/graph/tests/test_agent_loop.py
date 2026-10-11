import asyncio
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langboard_graph.core.graph.default.agent_loop import invoke_agent_loop


async def allow(name, args):
    return None


def tool_call(name="echo", id="call-1", value=1):
    return {"name": name, "id": id, "args": {"value": value}, "type": "tool_call"}


@pytest.mark.asyncio
async def test_all_call_ids_receive_replies_and_raw_receipts_survive():
    writes = []

    async def echo(value: int):
        writes.append(value)
        return {"value": value}

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo")
    calls = [tool_call(id=f"call-{i}", value=i) for i in range(10)] + [tool_call("missing", "unknown")]
    seen = []

    async def model(_, messages):
        seen.append(list(messages))
        return AIMessage(content="", tool_calls=calls) if len(seen) == 1 else AIMessage(content="done")

    result, receipts = await invoke_agent_loop(None, None, [HumanMessage(content="run")], [tool], model, allow, 1)
    assert result.content == "done"
    assert writes == list(range(8))
    replies = [m for m in seen[-1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in replies] == [c["id"] for c in calls]
    assert receipts[0]["result"] == {"value": 0}
    assert "limit" in receipts[-1]["result"]


@pytest.mark.asyncio
async def test_unknown_denied_failure_and_timeout_never_drop_response():
    async def fail(value: int):
        raise ValueError("failure")

    async def slow(value: int):
        await asyncio.sleep(1)

    tools = [
        StructuredTool.from_function(coroutine=f, name=n, description=n) for f, n in [(fail, "fail"), (slow, "slow")]
    ]
    calls = [
        tool_call("missing", "unknown"),
        tool_call("fail", "denied", 0),
        tool_call("fail", "failed"),
        tool_call("slow", "timeout"),
    ]
    rounds = []

    async def model(_, messages):
        rounds.append(messages)
        return AIMessage(content="", tool_calls=calls) if len(rounds) == 1 else AIMessage(content="done")

    async def approval(name, args):
        return "not approved" if args["value"] == 0 else None

    _, receipts = await invoke_agent_loop(None, None, [HumanMessage(content="run")], tools, model, approval, 0.01)
    assert [r["result"] for r in receipts][:3] == ["Unknown tool: missing", "not approved", "Tool fail failed: failure"]
    assert "result may be unknown" in receipts[-1]["result"]


@pytest.mark.asyncio
async def test_repeated_calls_stop_after_three_rounds_and_final_unbound_model():
    writes = []

    async def echo(value: int):
        writes.append(value)
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo")
    models = []

    async def model(which, messages):
        models.append(which)
        return (
            AIMessage(content="done")
            if which == "unbound"
            else AIMessage(content="", tool_calls=[tool_call(id=f"call-{len(models)}")])
        )

    result, _ = await invoke_agent_loop("unbound", "bound", [HumanMessage(content="run")], [tool], model, allow, 1)
    assert models == ["bound"] * 3 + ["unbound"]
    assert writes == [1] * 3
    assert result.content == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_nested_graph_interrupt_resumes_without_executing_unapproved_tool(approved):
    from typing import TypedDict
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    writes = []

    async def echo(value: int):
        writes.append(value)
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo")

    async def approval(name, args):
        decision = interrupt({"name": name})
        return None if decision else "not approved"

    async def model(_, messages):
        return (
            AIMessage(content="done")
            if any(isinstance(m, ToolMessage) for m in messages)
            else AIMessage(content="", tool_calls=[tool_call()])
        )

    class State(TypedDict):
        response: str

    async def agent(state):
        result, _ = await invoke_agent_loop(None, None, [HumanMessage(content="run")], [tool], model, approval, 1)
        return {"response": result.content}

    graph = StateGraph(State)
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "approval"}}
    result = await compiled.ainvoke({}, config)
    assert result["__interrupt__"]
    assert writes == []
    resumed = await compiled.ainvoke(Command(resume=approved), config)
    assert resumed["response"] == "done"
    assert writes == ([1] if approved else [])


@pytest.mark.asyncio
async def test_existing_agent_gate_rejects_invalid_uid_before_toolnode(monkeypatch):
    from langboard_graph.core.graph.default import nodes

    writes = []

    async def detail(card_uid: str):
        writes.append(card_uid)
        return "unexpected"

    tool = StructuredTool.from_function(coroutine=detail, name="detail", description="Detail")

    class Model:
        def bind_tools(self, tools):
            return self

    invocations = []

    async def model(_, messages):
        invocations.append(messages)
        return (
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "detail", "id": "invalid", "args": {"card_uid": "invented-name"}, "type": "tool_call"}
                ],
            )
            if len(invocations) == 1
            else AIMessage(content="done")
        )

    monkeypatch.setattr(nodes, "_invoke_chat_model", model)
    _, receipts = await nodes._invoke_agent(Model(), [HumanMessage(content="run")], [tool], tweaks={}, state={})
    assert writes == []
    assert "uid" in receipts[0]["result"].lower()


@pytest.mark.asyncio
async def test_repeated_invocations_do_not_reuse_previous_round_state():
    async def echo(value: int):
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo")
    for _ in range(2):
        rounds = []

        async def model(which, messages):
            rounds.append(which)
            return (
                AIMessage(content="done")
                if which == "unbound"
                else AIMessage(content="", tool_calls=[tool_call(id=f"call-{len(rounds)}")])
            )

        _, receipts = await invoke_agent_loop(
            "unbound", "bound", [HumanMessage(content="run")], [tool], model, allow, 1
        )
        assert len(receipts) == 3
        assert rounds == ["bound"] * 3 + ["unbound"]
