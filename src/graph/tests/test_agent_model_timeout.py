"""Model deadlines retain their reason without replaying models or starting tools."""

import asyncio
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langboard_graph.core.graph.default import nodes


class WaitingModel:
    def __init__(self):
        self.calls = 0
        self.cancelled = False

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


@pytest.mark.asyncio
async def test_model_deadline_retains_reason_and_cancels_one_call(monkeypatch):
    monkeypatch.setattr(type(nodes.Env), "AI_REQUEST_TIMEOUT", property(lambda _: 0.01))
    model = WaitingModel()
    with pytest.raises(TimeoutError, match="Graph chat model request timed out"):
        await nodes._invoke_chat_model(model, [HumanMessage(content="fixture")])
    assert model.calls == 1 and model.cancelled


@pytest.mark.asyncio
async def test_agent_model_deadline_starts_no_tool_or_second_model(monkeypatch):
    monkeypatch.setattr(type(nodes.Env), "AI_REQUEST_TIMEOUT", property(lambda _: 0.01))
    model = WaitingModel()
    writes = []

    async def echo(value: int):
        writes.append(value)
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo")
    with pytest.raises(TimeoutError, match="Graph chat model request timed out"):
        await nodes._invoke_agent(model, [HumanMessage(content="fixture")], [tool], tweaks={}, state={})
    assert model.calls == 1 and model.cancelled
    assert writes == []


@pytest.mark.asyncio
async def test_caller_cancellation_remains_cancellation_without_retry(monkeypatch):
    monkeypatch.setattr(type(nodes.Env), "AI_REQUEST_TIMEOUT", property(lambda _: 10))
    model = WaitingModel()
    task = asyncio.create_task(nodes._invoke_chat_model(model, [HumanMessage(content="fixture")]))
    while not model.calls:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.calls == 1 and model.cancelled
