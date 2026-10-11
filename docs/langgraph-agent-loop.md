# Bounded default-agent model and tool graph

The default agent now delegates its model/tool loop to a compiled LangGraph
StateGraph. `model` routes to `tools` only when a tool-bound model returns calls;
`tools` returns to `model`. After three tool-bound rounds the final invocation
uses the original unbound model and exits. The graph recursion limit is ten.
Existing API allowlisting, UID resolution and approval remain in
`nodes._get_tool_approval_result`, before ToolNode execution. Calls run
sequentially to preserve approval order and avoid concurrent writes.

Each requested call receives a ToolMessage with its original call ID, including
unknown names, denied calls and calls beyond the eight-per-round ceiling.
These rejected calls cause no tool invocation. Runtime errors become tool
responses. A tool timeout uses the existing AI request timeout and reports an
unknown result rather than claiming cancellation reversed an external effect.
A StructuredTool adapter keeps the original Python result as the ToolMessage
artifact so the existing `tool_results` receipt retains its value and type.
Messages use LangGraph's message reducer; receipts append across graph steps.

## Local proof

Using the checked-in graph dependency versions (LangGraph 1.2.4,
langgraph-prebuilt 1.1.0, LangChain 1.3.4, langchain-core 1.4.2), seven tests
exercise actual StateGraph and ToolNode execution:

- All requested call IDs receive replies, including over-limit calls, while
  exactly eight allowed calls execute and raw result dictionaries survive.
- Unknown, denied, failed and timed-out calls return explicit messages.
- Repeated tool requests stop after three rounds plus one unbound final model.
- A parent graph's in-memory checkpoint interrupts before side effects and
  resumes one approved call; rejection produces zero writes.
- The existing agent UID gate rejects an invalid UID before tool execution.
- Repeated invocations start with fresh round and receipt state.

The model and external tools are controlled test inputs. No live provider
invocation or deployment is covered. These tests do not establish PostgreSQL
checkpoint recovery, multi-write replay safety, total wall-clock budgets or
monetary cost ceilings. Those remain explicit roadmap acceptance work.

Run with the graph dependencies and pytest/pytest-asyncio installed:

```sh
PROJECT_NAME=langboard PYTHONPATH=src/graph:src/shared/py python -m pytest -p no:tach -o log_cli=false src/graph/tests/test_agent_loop.py -q
```

Reference: [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).
