"""Allowlisted native LangChain lifecycle projection; never forwards graph values."""

from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.errors import GraphBubbleUp


class GraphProgress(AsyncCallbackHandler):
    def __init__(self, emit, session_id: str, thread_id: str):
        self.emit = emit
        self.session_id = session_id
        self.thread_id = thread_id
        self.nodes = {}

    def _send(self, stage, run_id, parent_run_id=None, node=None):
        payload = {
            "stage": stage,
            "run_id": str(run_id),
            "thread_id": self.thread_id,
            "session_id": self.session_id,
        }
        if parent_run_id is not None:
            payload["parent_run_id"] = str(parent_run_id)
        if node is not None:
            payload["node"] = node
        self.emit(payload)

    async def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        node = (metadata or {}).get("langgraph_node")
        if node in {"history", "agent", "model", "prepare", "tools"} and len(self.nodes) < 128:
            self.nodes[run_id] = (node, parent_run_id)
            self._send("node_started", run_id, parent_run_id, node)

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        context = self.nodes.pop(run_id, None)
        if context is not None:
            self._send("node_completed", run_id, context[1], context[0])

    async def on_chain_error(self, error, *, run_id, **kwargs):
        context = self.nodes.pop(run_id, None)
        if context is not None:
            self._send(
                "node_paused" if isinstance(error, GraphBubbleUp) else "node_failed", run_id, context[1], context[0]
            )

    async def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self._send("tool_started", run_id, parent_run_id)

    async def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        self._send("tool_completed", run_id, parent_run_id)

    async def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._send("tool_failed", run_id, parent_run_id)
