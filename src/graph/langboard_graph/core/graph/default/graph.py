from contextlib import AsyncExitStack
from copy import deepcopy
from typing import Any
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from ...schema import GraphInterrupt, GraphRunResult
from ..checkpoint import lock_graph_thread, open_graph_checkpointer
from ..progress import GraphProgress
from .context import create_runtime_context_state
from .nodes import _apply_approved_api_context, collect_default_history_context, run_default_agent
from .privacy import redact_graph_credentials
from .runtime import DefaultGraphContext, resolve_execution_scope, restore_model_credentials, validate_resume_scope
from .state import DefaultGraphState


def build_default_graph(checkpointer: BaseCheckpointSaver[Any] | None = None):
    graph = StateGraph(DefaultGraphState, context_schema=DefaultGraphContext)
    graph.add_node("history", collect_default_history_context)
    graph.add_node("agent", run_default_agent)
    graph.add_edge(START, "history")
    graph.add_edge("history", "agent")
    graph.add_edge("agent", END)
    return graph.compile(checkpointer=checkpointer)


async def run_default_graph(
    input_value: str | None,
    tweaks: dict[str, Any] | None,
    session_id: str,
    thread_id: str,
    resume: Any | None = None,
    *,
    model_source: dict[str, Any] | None = None,
    progress=None,
) -> GraphRunResult:
    requires_checkpoint = _requires_checkpoint(tweaks, resume)
    async with open_graph_checkpointer(enabled=requires_checkpoint) as checkpointer, AsyncExitStack() as stack:
        if requires_checkpoint and checkpointer is None:
            raise RuntimeError("Graph checkpoint storage requires a PostgreSQL MAIN_DATABASE_URL.")

        if checkpointer is not None and resume is None:
            await stack.enter_async_context(lock_graph_thread(thread_id))
        graph = build_default_graph(checkpointer)
        graph_input: Command[Any] | DefaultGraphState
        config = {"configurable": {"thread_id": thread_id}}
        if progress is not None:
            config["callbacks"] = [GraphProgress(progress, session_id, thread_id)]
        if resume is not None:
            snapshot = await graph.aget_state(config)
            runtime_tweaks = restore_model_credentials(snapshot.values)
            execution_scope = validate_resume_scope(snapshot.values, runtime_tweaks, resume)
            _apply_approved_api_context(runtime_tweaks, resume)
            # The history node advances its cursor before the agent can interrupt.
            # Replaying that agent rebuilds its messages, so it needs the full
            # currently visible window, including unchanged history it never saw.
            from .history import create_langboard_history_context_prompt

            history_state = deepcopy(snapshot.values)
            history_state["last_history_sync_at"] = None
            create_langboard_history_context_prompt(runtime_tweaks, history_state)
            history_update = {
                key: history_state.get(key)
                for key in (
                    "history_messages",
                    "history_delta",
                    "history_summary",
                    "last_history_sync_at",
                    "history_context_prompt",
                )
            }
            graph_input = Command(resume=redact_graph_credentials(resume), update=history_update)
        else:
            input_tweaks = tweaks or {}
            execution_scope = resolve_execution_scope(input_tweaks)
            if execution_scope.get("bot_id") and model_source != {"table": "bot", "id": execution_scope["bot_id"]}:
                raise ValueError("Graph bot model source does not match its authenticated actor")
            if checkpointer is not None and (await graph.aget_state(config)).values:
                raise ValueError("Graph thread already exists; resume it or start a new thread")
            state: DefaultGraphState = {
                "input_value": input_value or "",
                "tweaks": redact_graph_credentials(input_tweaks),
                "model_source": model_source or {},
                "execution_scope": execution_scope,
                "session_id": session_id,
                "thread_id": thread_id,
                **create_runtime_context_state(input_tweaks),
            }
            graph_input = state
            runtime_tweaks = input_tweaks

        result = await graph.ainvoke(
            graph_input, config=config, context={"tweaks": runtime_tweaks, "execution_scope": execution_scope}
        )
        graph_result = _create_graph_run_result(result)
        if checkpointer is not None and resume is None and not graph_result.interrupts:
            await checkpointer.adelete_thread(thread_id)
        return graph_result


async def validate_default_graph_resume(thread_id: str, resume: Any) -> None:
    """Authorize recovery as well as execution before exposing saved results."""
    async with open_graph_checkpointer() as checkpointer:
        if checkpointer is None:
            raise ValueError("Graph checkpoint storage is unavailable")
        graph = build_default_graph(checkpointer)
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        validate_resume_scope(snapshot.values, deepcopy(snapshot.values.get("tweaks") or {}), resume)


async def get_default_graph_status(thread_id: str) -> dict[str, Any]:
    async with open_graph_checkpointer() as checkpointer:
        if checkpointer is None:
            return {"thread_id": thread_id, "interrupts": [], "values": {}, "next": []}

        graph = build_default_graph(checkpointer)
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return _create_graph_status_response(thread_id, snapshot)


async def delete_default_graph_thread(thread_id: str) -> None:
    async with open_graph_checkpointer() as checkpointer:
        if checkpointer is not None:
            await checkpointer.adelete_thread(thread_id)


def _create_graph_status_response(thread_id: str, snapshot: Any) -> dict[str, Any]:
    values = getattr(snapshot, "values", {}) or {}
    return {
        "thread_id": thread_id,
        "interrupts": _serialize_interrupts(getattr(snapshot, "interrupts", []) or []),
        "values": {
            key: values[key]
            for key in ("session_id", "thread_id", "approval_requests", "approval_result", "response")
            if key in values
        },
        "next": list(getattr(snapshot, "next", []) or []),
    }


def _requires_checkpoint(tweaks: dict[str, Any] | None, resume: Any | None) -> bool:
    if resume is not None:
        return True

    graph_config = (tweaks or {}).get("Graph")
    if not isinstance(graph_config, dict):
        return False
    if graph_config.get("approval_request") is not None:
        return True

    approval_policy = graph_config.get("api_approval_policy")
    if isinstance(approval_policy, dict) and "ask" in {str(value) for value in approval_policy.values()}:
        return True

    return bool(graph_config.get("api_names"))


def _create_graph_run_result(result: dict[str, Any]) -> GraphRunResult:
    interrupts = _serialize_interrupts(result.get("__interrupt__") or [])
    return GraphRunResult(
        message=result.get("response") or "",
        interrupts=[GraphInterrupt(**item) for item in interrupts],
    )


def _serialize_interrupts(interrupts: list[Any] | tuple[Any, ...]) -> list[dict[str, Any]]:
    serialized_interrupts = []
    for item in interrupts:
        serialized_interrupts.append(
            {
                "id": getattr(item, "id", None),
                "value": getattr(item, "value", item),
            }
        )
    return serialized_interrupts
