"""Native lifecycle progress must arrive before a blocked node completes."""

import asyncio
import json
import pytest
from langgraph.graph import END, START, StateGraph
from langboard_graph.core.graph.GraphRunner import GraphRunner
from langboard_graph.core.graph.progress import GraphProgress
from langboard_graph.core.schema import RunResponse


@pytest.mark.asyncio
async def test_native_node_progress_precedes_completion_and_never_contains_state():
    entered, release = asyncio.Event(), asyncio.Event()
    events = asyncio.Queue()

    async def agent(state: dict):
        entered.set()
        await release.wait()
        return {"secret": "private-result"}

    builder = StateGraph(dict)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    graph = builder.compile()
    callback = GraphProgress(events.put_nowait, "session", "thread")
    task = asyncio.create_task(graph.ainvoke({"secret": "private-input"}, config={"callbacks": [callback]}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        early = await asyncio.wait_for(events.get(), 2)
        assert early["stage"] == "node_started"
        assert early["node"] == "agent"
        assert early["run_id"]
        assert early["thread_id"] == "thread"
        assert not task.done()
        release.set()
        await task
        remaining = []
        while not events.empty():
            remaining.append(events.get_nowait())
        assert any(event["stage"] == "node_completed" for event in remaining)
        assert "private-" not in json.dumps([early, *remaining])
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_runner_delivers_progress_while_graph_blocked_and_final_message_once(monkeypatch):
    runner = GraphRunner(None)
    release = asyncio.Event()
    calls = []

    async def run(*, progress):
        calls.append(True)
        progress({"stage": "node_started", "node": "agent"})
        await release.wait()
        return RunResponse.from_message("done")

    monkeypatch.setattr(runner, "run", run)
    task, stream = await runner.run_stream()
    early = json.loads(await asyncio.wait_for(anext(stream), 2))
    assert early["event"] == "progress"
    assert early["data"]["stage"] == "node_started"
    assert not task.done()
    release.set()
    rest = [json.loads(frame) async for frame in stream]
    assert [frame["event"] for frame in rest] == ["token", "end"]
    assert rest[0]["data"]["chunk"] == "done"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_disconnect_cancels_graph_and_bounded_progress_does_not_block(monkeypatch):
    runner = GraphRunner(None)
    stopped = asyncio.Event()

    async def run(*, progress):
        try:
            for _ in range(1000):
                progress({"stage": "tool_started"})
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(runner, "run", run)
    task, stream = await runner.run_stream()
    await asyncio.wait_for(anext(stream), 2)
    await stream.aclose()
    await asyncio.wait_for(stopped.wait(), 2)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_native_interrupt_remains_paused_not_failed_and_resumes_once():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command, interrupt

    events, effects = [], []

    async def agent(state: dict):
        decision = interrupt({"private": "approval-body"})
        if decision:
            effects.append(True)
        return {"done": True}

    builder = StateGraph(dict)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}, "callbacks": [GraphProgress(events.append, "session", "thread")]}
    paused = await graph.ainvoke({}, config=config)
    assert paused["__interrupt__"]
    assert not effects
    assert any(event["stage"] == "node_paused" for event in events)
    assert all(event["stage"] != "node_failed" for event in events)
    await graph.ainvoke(Command(resume=True), config=config)
    assert effects == [True]
    assert "approval-body" not in json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_native_nested_runnables_count_only_actual_nodes_and_preserve_tools(fail):
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool

    events = []

    @tool
    async def local_tool(value: str) -> str:
        """Return a local test value or fail without contacting a provider."""
        if fail:
            raise ValueError("private-tool-error")
        return value

    async def nested(state):
        await local_tool.ainvoke({"value": "private-tool-input"})
        return {"secret": "private-result"}

    # Matching the node's name must not make an ordinary nested runnable a node.
    chain = RunnableLambda(lambda state: state).with_config(run_name="agent") | RunnableLambda(nested)

    async def agent(state):
        return await chain.ainvoke(state)

    graph = StateGraph(dict).add_node("agent", agent).add_edge(START, "agent").add_edge("agent", END).compile()
    callback = GraphProgress(events.append, "session", "thread")
    if fail:
        with pytest.raises(ValueError, match="private-tool-error"):
            await graph.ainvoke({"secret": "private-input"}, config={"callbacks": [callback]})
    else:
        await graph.ainvoke({"secret": "private-input"}, config={"callbacks": [callback]})

    nodes = [event for event in events if event["stage"].startswith("node_")]
    tools = [event for event in events if event["stage"].startswith("tool_")]
    assert [event["stage"] for event in nodes] == ["node_started", "node_failed" if fail else "node_completed"]
    assert [event["stage"] for event in tools] == ["tool_started", "tool_failed" if fail else "tool_completed"]
    assert nodes[0]["run_id"] == nodes[1]["run_id"]
    assert tools[0]["run_id"] == tools[1]["run_id"]
    assert tools[0]["parent_run_id"]
    assert all(event["node"] == "agent" for event in nodes)
    assert all(event["session_id"] == "session" and event["thread_id"] == "thread" for event in events)
    assert all(
        set(event) <= {"stage", "run_id", "parent_run_id", "node", "session_id", "thread_id"} for event in events
    )
    assert "private-" not in json.dumps(events)
    assert callback.nodes == {}


@pytest.mark.asyncio
async def test_native_subgraph_counts_each_actual_node_with_same_name_once():
    from langchain_core.runnables import RunnableLambda

    async def inner_agent(state):
        return await RunnableLambda(lambda value: value).ainvoke(state)

    inner = StateGraph(dict).add_node("agent", inner_agent).add_edge(START, "agent").add_edge("agent", END).compile()
    outer = StateGraph(dict).add_node("agent", inner).add_edge(START, "agent").add_edge("agent", END).compile()
    events = []
    callback = GraphProgress(events.append, "session", "thread")
    await outer.ainvoke({"secret": "private-subgraph"}, config={"callbacks": [callback]})
    assert [event["stage"] for event in events] == ["node_started", "node_started", "node_completed", "node_completed"]
    assert events[0]["run_id"] == events[3]["run_id"]
    assert events[1]["run_id"] == events[2]["run_id"]
    assert events[0]["run_id"] != events[1]["run_id"]
    assert "private-" not in json.dumps(events)
    assert callback.nodes == {}
