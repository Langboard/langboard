# Bounded default-agent model and tool graph

The default agent now delegates its model/tool loop to a compiled LangGraph
StateGraph. `model` routes to `tools` only when a tool-bound model returns calls;
`tools` returns to `model`. After three tool-bound rounds the final invocation
uses the original unbound model and exits. The graph recursion limit is forty, allowing the bounded serial call steps.
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

## Later approval resume boundary

A later approval originally caused the tool node to restart its entire call
loop, replaying a completed first write (`[1, 1, 2]`). Each allowed call now
finishes its own graph step, with the pending calls and next index persisted
in graph state. Over-limit replies batch without executing writes. The same
reproduction resumes with `[1, 2]` on InMemorySaver and AsyncPostgresSaver after
rebuilding the parent graph instance. This establishes checkpointed resume
after a completed step. It does not guarantee exactly-once external writes
if a process dies between external commit and checkpoint persistence; API
idempotency receipts and worker restart/concurrent resume remain required.

## Structured credential output boundary

Model runtime-context and event prompts now redact nested credential fields.
The public graph status route applies the same redaction, while its internal
status function keeps the original value for existing resume receipt matching.
The helper reuses audit contract sensitive-key names and adds graph/OAuth/API
header names, traversing dictionaries, lists and tuples without mutating the
input. Three tests cover real prompt functions and the status route handler,
including preserved approval/title fields and internal completed-resume matching.

This is an output boundary, not a checkpoint secrecy guarantee. `tweaks` still
contains provider/API credentials in persisted graph state, and credentials in
unstructured strings or provider errors are not handled by key redaction. The
State/Runtime Context roadmap must still move credentials to non-persisted
server runtime inputs and resolve fresh credentials on resume. It must also
verify current actor/project authority rather than trusting saved context.

## PostgreSQL worker restart proof

A dedicated subprocess test performs its controlled external effects in a
separate PostgreSQL table, invokes the real nested agent graph, and stops at
the second call's approval interrupt. The parent verifies only the first effect
exists, force-kills the stopped worker, and starts a new Python process that
loads the same AsyncPostgresSaver thread and resumes. The resulting table has
exactly one row for effect 1 and one for effect 2. The parent removes the thread
and temporary effect table. With the other loop cases, twelve tests passed
locally in 6.26 seconds against disposable PostgreSQL.

This kill occurs after a completed tool step and persisted approval checkpoint.
It does not cover the external-commit/checkpoint crash window, concurrent resume,
event generation fencing or external API idempotency. These acceptance items
remain incomplete. The test worker uses fixed generated identifiers and
controlled SQL writes; it does not invoke a production provider or service.

## Concurrent resume proof

Two additional PostgreSQL cases invoke the real `resume_graph` route handler,
thread advisory lock and AsyncPostgresSaver. The graph provider helpers are
replaced with a controlled StateGraph that interrupts before a SQL effect;
this isolates resume concurrency without a production provider or auth flow.
The first approved request remains inside the lock until `pg_stat_activity`
confirms the second request is waiting on the PostgreSQL advisory lock.
After release, identical approval returns the completed response, while a
conflicting decision returns HTTP 409. Both cases preserve the first decision
and leave exactly one effect row. The lock/checkpointer pools, thread and table
are removed after each case. The full agent-loop suite passed fourteen tests
in 6.75 seconds against disposable PostgreSQL.

This proves the existing handler serializes these same-thread resume requests.
It does not prove HTTP authentication, actor/project revalidation, generation
fencing, provider-side idempotency or recovery across an external commit before
its checkpoint. The broader Durable Execution checklist remains incomplete.

## Approval payload privacy boundary

Approval requests are recursively redacted before entering graph state or an
interrupt. This covers initial graph approvals and per-tool approvals, while
preserving titles and permission metadata. A disposable PostgreSQL case checks
the persisted interrupt path; a second case verifies tool arguments remain
unchanged for execution while the approval projection is redacted. The suite
now passes nineteen tests. This does not yet remove credentials from the
`tweaks` state supplied at graph start; provider/API credential resolution must
move to server-owned runtime context before the State/Runtime checklist can
close.
