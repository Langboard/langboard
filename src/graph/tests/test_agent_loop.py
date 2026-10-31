import asyncio
import os
import pytest
import pytest_asyncio
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
@pytest.mark.parametrize("calls_per_round", [1, 8, 10])
async def test_repeated_calls_stop_after_three_rounds_and_final_unbound_model(calls_per_round):
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
            else AIMessage(
                content="", tool_calls=[tool_call(id=f"call-{len(models)}-{index}") for index in range(calls_per_round)]
            )
        )

    result, _ = await invoke_agent_loop("unbound", "bound", [HumanMessage(content="run")], [tool], model, allow, 1)
    assert models == ["bound"] * 3 + ["unbound"]
    assert writes == [1] * (3 * min(8, calls_per_round))
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
@pytest.mark.parametrize("approved", [True, False])
async def test_tool_interrupt_bubbles_to_parent_and_resumes(approved):
    from typing import TypedDict
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    writes = []

    async def echo(value: int):
        if interrupt({"value": value}):
            writes.append(value)
            return {"value": value}
        return "not approved"

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo")

    async def model(_, messages):
        return (
            AIMessage(content="done")
            if any(isinstance(m, ToolMessage) for m in messages)
            else AIMessage(content="", tool_calls=[tool_call()])
        )

    class State(TypedDict):
        response: str
        receipts: list

    async def agent(state):
        result, receipts = await invoke_agent_loop(None, None, [HumanMessage(content="run")], [tool], model, allow, 1)
        return {"response": result.content, "receipts": receipts}

    builder = StateGraph(State)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "tool-interrupt"}}
    paused = await graph.ainvoke({}, config)
    assert paused.get("__interrupt__"), paused
    assert writes == []
    resumed = await graph.ainvoke(Command(resume=approved), config)
    assert resumed["response"] == "done"
    assert writes == ([1] if approved else [])
    assert resumed["receipts"][0]["result"] == ({"value": 1} if approved else "not approved")


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


@pytest.mark.asyncio
async def test_later_approval_resume_does_not_repeat_completed_call(durable_saver):
    from typing import TypedDict
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    writes = []

    async def echo(value: int):
        writes.append(value)
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo")

    async def approval(name, args):
        if args["value"] == 2:
            return None if interrupt({"name": name}) else "not approved"
        return None

    async def model(_, messages):
        return (
            AIMessage(content="done")
            if any(isinstance(m, ToolMessage) for m in messages)
            else AIMessage(content="", tool_calls=[tool_call(id="first", value=1), tool_call(id="second", value=2)])
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
    compiled = graph.compile(checkpointer=durable_saver)
    config = {"configurable": {"thread_id": "second-approval"}}
    result = await compiled.ainvoke({}, config)
    assert result["__interrupt__"]
    assert writes == [1]
    # Rebuild the graph instance; durable state must come from the saver.
    resumed = await graph.compile(checkpointer=durable_saver).ainvoke(Command(resume=True), config)
    assert resumed["response"] == "done"
    assert writes == [1, 2]


@pytest_asyncio.fixture(params=["memory", "postgres"])
async def durable_saver(request):
    if request.param == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return
    url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(url.replace("postgresql+psycopg://", "postgresql://")) as saver:
        await saver.setup()
        try:
            yield saver
        finally:
            await saver.adelete_thread("second-approval")


@pytest.mark.asyncio
async def test_postgres_approval_checkpoint_survives_killed_worker(tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path
    from uuid import uuid4
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection

    raw_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Requires disposable PostgreSQL")
    url = raw_url.replace("postgresql+psycopg://", "postgresql://")
    table = "graph_restart_" + uuid4().hex
    thread = "restart-" + uuid4().hex
    marker = tmp_path / "checkpointed"
    worker = Path(__file__).with_name("durable_process_worker.py")
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(sys.path),
        LANGBOARD_RESTART_TABLE=table,
        LANGBOARD_RESTART_THREAD=thread,
        LANGBOARD_RESTART_MARKER=str(marker),
        LANGBOARD_RESTART_PHASE="start",
    )
    async with await AsyncConnection.connect(url, autocommit=True) as db:
        await db.execute(f'CREATE TABLE "{table}" (value integer NOT NULL)')
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
    process = subprocess.Popen([sys.executable, str(worker)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert marker.exists(), process.communicate(timeout=1)
        async with await AsyncConnection.connect(url, autocommit=True) as db:
            result = await db.execute(f'SELECT value FROM "{table}"')
            assert await result.fetchall() == [(1,)]
        process.kill()
        process.communicate(timeout=5)
        env["LANGBOARD_RESTART_PHASE"] = "resume"
        resumed = await asyncio.to_thread(
            subprocess.run, [sys.executable, str(worker)], env=env, capture_output=True, text=True, timeout=20
        )
        assert resumed.returncode == 0, resumed.stderr
        async with await AsyncConnection.connect(url, autocommit=True) as db:
            result = await db.execute(f'SELECT value FROM "{table}" ORDER BY value')
            assert await result.fetchall() == [(1,), (2,)]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.adelete_thread(thread)
        async with await AsyncConnection.connect(url, autocommit=True) as db:
            await db.execute(f'DROP TABLE "{table}"')


@pytest.mark.asyncio
@pytest.mark.parametrize("second_decision", [True, False])
async def test_postgres_concurrent_resume_serializes_effect_and_preserves_first_decision(monkeypatch, second_decision):
    from typing import TypedDict
    from uuid import uuid4
    from fastapi import HTTPException
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from psycopg import AsyncConnection
    from langboard_graph.core.graph import checkpoint
    from langboard_graph.core.graph.default.graph import _create_graph_run_result, _create_graph_status_response
    from langboard_graph.routes import RunApi

    raw_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Requires disposable PostgreSQL")
    url = raw_url.replace("postgresql+psycopg://", "postgresql://")
    await checkpoint.close_graph_checkpointer()
    monkeypatch.setattr(type(checkpoint.Env), "MAIN_DATABASE_URL", property(lambda self: url))
    thread = "concurrent-" + uuid4().hex
    table = "graph_concurrent_" + uuid4().hex
    entered, release = asyncio.Event(), asyncio.Event()

    class State(TypedDict):
        approval_result: bool
        response: str

    async def agent(state):
        decision = interrupt({"message": "Approve controlled SQL effect?"})
        if decision:
            entered.set()
            await release.wait()
            async with await AsyncConnection.connect(url, autocommit=True) as db:
                await db.execute(f'INSERT INTO "{table}" VALUES (1)')
        return {"approval_result": decision, "response": "done"}

    builder = StateGraph(State)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    config = {"configurable": {"thread_id": thread}}
    tasks = []
    async with await AsyncConnection.connect(url, autocommit=True) as db:
        await db.execute(f'CREATE TABLE "{table}" (value integer NOT NULL)')
    try:
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            graph = builder.compile(checkpointer=saver)
            assert (await graph.ainvoke({}, config))["__interrupt__"]

            async def get_status(thread_id):
                assert thread_id == thread
                return _create_graph_status_response(thread_id, await graph.aget_state(config))

            async def run_graph(**kwargs):
                result = await graph.ainvoke(Command(resume=kwargs["resume"]), config)
                return _create_graph_run_result(result)

            async def validate_scope(thread_id, resume):
                # This controlled graph has no Langboard actor state. Runtime
                # authority is exercised separately in test_runtime_authority.
                assert thread_id == thread and isinstance(resume, bool)

            monkeypatch.setattr(RunApi, "validate_default_graph_resume", validate_scope)
            monkeypatch.setattr(RunApi, "get_default_graph_status", get_status)
            monkeypatch.setattr(RunApi, "run_default_graph", run_graph)
            tasks.append(asyncio.create_task(RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=True))))
            await asyncio.wait_for(entered.wait(), timeout=5)
            tasks.append(
                asyncio.create_task(RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=second_decision)))
            )

            async def wait_for_advisory_waiter():
                async with await AsyncConnection.connect(url, autocommit=True) as db:
                    while True:
                        cursor = await db.execute(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() AND wait_event = 'advisory' "
                            "AND query LIKE 'SELECT pg_advisory_xact_lock%'"
                        )
                        if (await cursor.fetchone())[0] == 1:
                            return
                        await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_advisory_waiter(), timeout=5)
            assert not tasks[1].done(), "Second request must wait for the real PostgreSQL thread lock"
            release.set()
            first, second = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
            assert first.message == "done"
            if second_decision:
                assert second.model_dump() == first.model_dump()
            else:
                assert isinstance(second, HTTPException)
                assert second.status_code == 409
            snapshot = await graph.aget_state(config)
            assert snapshot.values["approval_result"] is True
            async with await AsyncConnection.connect(url, autocommit=True) as db:
                cursor = await db.execute(f'SELECT value FROM "{table}"')
                assert await cursor.fetchall() == [(1,)]
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await checkpoint.close_graph_checkpointer()
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.adelete_thread(thread)
        async with await AsyncConnection.connect(url, autocommit=True) as db:
            await db.execute(f'DROP TABLE "{table}"')
