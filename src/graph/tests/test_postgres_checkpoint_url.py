"""Real PostgreSQL checkpoint and lock compatibility with ORM connection URLs."""

import os
from uuid import uuid4
import pytest
from langboard_shared.Env import Env
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict
from langboard_graph.core.graph import checkpoint
from langboard_graph.routes import RunApi


class State(TypedDict):
    answer: str


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
async def test_postgres_urls_persist_resume_and_share_pools(monkeypatch, scheme):
    url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    url = scheme + "://" + url.split("://", 1)[1]
    monkeypatch.setattr(type(Env), "MAIN_DATABASE_URL", property(lambda self: url))
    thread = "url-compat-" + uuid4().hex
    config = {"configurable": {"thread_id": thread}}

    def ask(state):
        return {"answer": interrupt("Proceed?")}

    builder = StateGraph(State)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    try:
        async with checkpoint.open_graph_checkpointer() as saver:
            graph = builder.compile(checkpointer=saver)
            result = await graph.ainvoke({"answer": ""}, config)
            assert result["__interrupt__"]
            pool = checkpoint._checkpoint_pool
        # Release pool and recreate it: resume must load persisted database state.
        await checkpoint.close_graph_checkpointer()
        async with checkpoint.open_graph_checkpointer() as saver:
            graph = builder.compile(checkpointer=saver)
            result = await graph.ainvoke(Command(resume="approved"), config)
            assert result["answer"] == "approved"
            assert checkpoint._checkpoint_pool is not pool
            assert await checkpoint._get_checkpoint_pool() is checkpoint._checkpoint_pool
            await saver.adelete_thread(thread)
        async with checkpoint.lock_graph_thread(thread):
            assert checkpoint._thread_lock_pool is not None
        slot = await RunApi._acquire_background_slot()
        assert slot is not None
        await RunApi._release_background_slot(slot)
    finally:
        await checkpoint.close_graph_checkpointer()


def test_non_postgres_checkpoint_url_fails_without_disclosing_credentials(monkeypatch):
    monkeypatch.setattr(type(Env), "MAIN_DATABASE_URL", property(lambda self: "mysql://secret@example.invalid/db"))
    with pytest.raises(RuntimeError, match="requires a PostgreSQL") as error:
        checkpoint.graph_postgres_url()
    assert "secret" not in str(error.value)
