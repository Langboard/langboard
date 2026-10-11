"""Bounded model/tools graph; approval and API policy remain owned by the caller."""

import asyncio
from operator import add
from typing import Annotated, Any, Awaitable, Callable, TypedDict
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode


class AgentLoopState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_results: Annotated[list[dict[str, Any]], add]
    rounds: int
    final: bool


async def invoke_agent_loop(
    chat_model: Any,
    tool_model: Any,
    messages: list[BaseMessage],
    tools: list[Any],
    invoke_model: Callable[..., Awaitable[BaseMessage]],
    approval: Callable[[str, dict[str, Any]], Awaitable[str | None]],
    timeout: float,
) -> tuple[BaseMessage, list[dict[str, Any]]]:
    tool_map = {tool.name: tool for tool in tools}

    def receipt_tool(tool):
        async def invoke(**args):
            try:
                result = await tool.ainvoke(args)
            except Exception as exc:
                result = f"Tool {tool.name} failed: {exc}"
            return str(result), result

        return StructuredTool.from_function(
            coroutine=invoke,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            response_format="content_and_artifact",
        )

    tool_node = ToolNode([receipt_tool(tool) for tool in tools], handle_tool_errors=lambda exc: f"Tool failed: {exc}")

    async def model(state: AgentLoopState):
        final = state["rounds"] >= 3
        result = await invoke_model(chat_model if final else tool_model, state["messages"])
        return {"messages": [result], "rounds": state["rounds"] + 1, "final": final}

    def route(state: AgentLoopState):
        return "tools" if not state["final"] and getattr(state["messages"][-1], "tool_calls", []) else END

    async def execute(state: AgentLoopState):
        replies: list[ToolMessage] = []
        results: list[dict[str, Any]] = []
        for index, call in enumerate(state["messages"][-1].tool_calls):
            name = str(call.get("name") or "")
            args = dict(call.get("args") or {})
            call_id = call.get("id") or name
            if index >= 8:
                reply = ToolMessage(
                    content="Tool call not executed: per-round limit of 8 exceeded.", tool_call_id=call_id
                )
            elif name not in tool_map:
                reply = ToolMessage(content=f"Unknown tool: {name}", tool_call_id=call_id)
            else:
                denied = await approval(name, args)
                if denied is not None:
                    reply = ToolMessage(content=denied, tool_call_id=call_id)
                else:
                    # Sequential calls preserve approval ordering and avoid parallel writes.
                    invocation = {
                        "messages": [AIMessage(content="", tool_calls=[{**call, "id": call_id, "args": args}])]
                    }
                    try:
                        output = await asyncio.wait_for(tool_node.ainvoke(invocation), timeout=timeout)
                        reply = output["messages"][0]
                    except TimeoutError:
                        reply = ToolMessage(
                            content=f"Tool {name} timed out; result may be unknown.", tool_call_id=call_id
                        )
            replies.append(reply)
            results.append(
                {"name": name, "args": args, "result": reply.artifact if reply.artifact is not None else reply.content}
            )
        return {"messages": replies, "tool_results": results}

    graph = StateGraph(AgentLoopState)
    graph.add_node("model", model)
    graph.add_node("tools", execute)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    result = await graph.compile().ainvoke(
        {"messages": messages, "tool_results": [], "rounds": 0, "final": False},
        config={"recursion_limit": 10},
    )
    return result["messages"][-1], result["tool_results"]
