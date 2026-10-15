import pytest
from langboard_graph.core.graph.default.privacy import redact_graph_credentials
from langboard_graph.core.graph.default.tooling import create_langboard_context_prompt, create_langboard_event_input


@pytest.mark.parametrize("delta", [False, True])
@pytest.mark.parametrize("summary", [False, True])
def test_deleted_card_comment_history_does_not_expose_body(monkeypatch, delta, summary):
    from datetime import timedelta
    from types import SimpleNamespace
    from langboard_shared.core.types import SafeDateTime
    from langboard_graph.core.graph.default import history

    now = SafeDateTime.now()
    comment = SimpleNamespace(
        id=1,
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=1),
        deleted_at=now,
        content=SimpleNamespace(content="deleted secret body"),
        get_uid=lambda: "deleted-comment",
    )
    message = history.normalize_card_comment(comment)
    assert message["change_type"] == "deleted"
    assert message["content"] is None
    assert message["updated_at"] == now.isoformat()
    monkeypatch.setattr(history, "collect_langboard_history_messages", lambda tweaks, config: [message])
    state = {"last_history_sync_at": (now - timedelta(hours=1)).isoformat()} if delta else {}
    prompt = history.create_langboard_history_context_prompt(
        {"Graph": {"history_summary_enabled": summary}},
        state,
    )
    assert "deleted secret body" not in str(state)
    assert "deleted secret body" not in prompt
    assert "deleted-comment" in str(state)
    if delta:
        assert state["history_delta"]["deleted"] == [message]


def test_nested_credentials_are_redacted_without_mutating_authority_input():
    secret = "fixture-secret-not-real"
    state = {
        "values": {"approval_result": {"approved": True, "app_api_token": secret}},
        "interrupts": [
            {
                "value": {
                    "headers": {"Authorization": secret, "X-API-Key": secret},
                    "request_payload": {"items": [{"client_secret": secret, "title": "Keep title"}]},
                }
            }
        ],
    }
    public = redact_graph_credentials(state)
    assert secret not in str(public)
    assert public["values"]["approval_result"]["approved"] is True
    assert public["interrupts"][0]["value"]["request_payload"]["items"][0]["title"] == "Keep title"
    assert state["values"]["approval_result"]["app_api_token"] == secret


def test_model_runtime_prompts_do_not_serialize_structured_credentials():
    secret = "fixture-secret-not-real"
    tweaks = {
        "LangboardCalledVariablesComponent": {
            "event": "card_created",
            "project_uid": "board",
            "current_runner_data": {"settings": {"api_key": secret}},
            "rest_data": {"title": "Keep title", "nested": [{"refresh_token": secret, "Cookie": secret}]},
        }
    }
    for prompt in (create_langboard_context_prompt(tweaks), create_langboard_event_input("", tweaks)):
        assert secret not in prompt
        assert "Keep title" in prompt
    assert tweaks["LangboardCalledVariablesComponent"]["current_runner_data"]["settings"]["api_key"] == secret


@pytest.mark.asyncio
async def test_status_route_redacts_public_values_but_preserves_internal_resume_match(monkeypatch):
    import json
    from langboard_graph.routes import RunApi

    secret = "fixture-secret-not-real"
    resume = {"approved": True, "app_api_token": secret}
    status = {
        "thread_id": "fixture",
        "values": {"approval_result": resume, "response": "done"},
        "interrupts": [],
        "next": [],
    }

    async def current(thread):
        return status

    monkeypatch.setattr(RunApi, "get_default_graph_status", current)
    response = await RunApi.get_graph_status("fixture")
    assert secret.encode() not in response.body
    assert json.loads(response.body)["values"]["approval_result"]["approved"] is True
    request = RunApi.GraphResumeRequest(resume=resume)
    recovered = RunApi._get_completed_resume_response("fixture", request, status)
    assert recovered is not None
    assert status["values"]["approval_result"]["app_api_token"] == secret


@pytest.mark.asyncio
async def test_real_postgres_initial_approval_interrupt_redacts_payload(monkeypatch):
    import os
    from uuid import uuid4
    from langboard_graph.core.graph import checkpoint
    from langboard_graph.core.graph.default import history
    from langboard_graph.core.graph.default.graph import run_default_graph

    raw_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Requires disposable PostgreSQL")
    url = raw_url.replace("postgresql+psycopg://", "postgresql://")
    await checkpoint.close_graph_checkpointer()
    monkeypatch.setattr(type(checkpoint.Env), "MAIN_DATABASE_URL", property(lambda self: url))
    secret = "fixture-secret-not-real"
    thread = "private-interrupt-" + uuid4().hex
    monkeypatch.setattr(
        history,
        "collect_langboard_history_messages",
        lambda tweaks, config: [
            {
                "msg_id": "activity:fixture",
                "msg_type": "card_action",
                "source": "project_activity",
                "change_type": "new",
                "timestamp": "2026-10-02T00:00:00+00:00",
                "content": {"title": "Keep history title", "api_key": secret},
            }
        ],
    )
    tweaks = {
        "Graph": {
            "approval_request": {
                "message": "Approve action",
                "request_payload": {"headers": {"Authorization": secret}, "title": "Keep title"},
            }
        },
        "LangboardCalledVariablesComponent": {
            "current_runner_data": {"uid": "fixture-actor", "settings": {"api_key": secret}}
        },
    }
    try:
        result = await run_default_graph("run", tweaks, thread, thread)
        assert result.interrupts
        assert secret not in str(result.model_dump())
        async with checkpoint.open_graph_checkpointer() as saver:
            snapshot = await saver.aget_tuple({"configurable": {"thread_id": thread}})
            assert snapshot is not None
            project_context = snapshot.checkpoint["channel_values"]["project_context"]
            assert secret not in str(project_context)
            assert project_context["current_runner_data"]["uid"] == "fixture-actor"
            for channel in ("history_messages", "history_delta", "history_context_prompt"):
                assert secret not in str(snapshot.checkpoint["channel_values"][channel])
            assert "Keep history title" in snapshot.checkpoint["channel_values"]["history_context_prompt"]
            # Only projections/approval metadata are covered; raw tweaks remain a separate gap.
            pending = snapshot.pending_writes
            assert pending
            assert secret not in str([value for _, channel, value in pending if channel == "__interrupt__"])
            await saver.adelete_thread(thread)
        assert tweaks["Graph"]["approval_request"]["request_payload"]["headers"]["Authorization"] == secret
    finally:
        async with checkpoint.open_graph_checkpointer() as saver:
            await saver.adelete_thread(thread)
        await checkpoint.close_graph_checkpointer()


@pytest.mark.asyncio
async def test_tool_approval_redacts_interrupt_without_changing_execution_arguments(monkeypatch):
    from langboard_graph.core.graph.default import nodes

    secret = "fixture-secret-not-real"
    args = {"client_secret": secret, "title": "Keep title"}
    payload = {"message": "Approve action", "request_payload": {"tool_args": args}}
    observed = []

    async def resolve(*unused):
        return None

    def approve(value):
        observed.append(value)
        return {"approved": False, "rejected": True}

    monkeypatch.setattr(nodes, "_resolve_tool_uid_args", resolve)
    monkeypatch.setattr(nodes, "create_langboard_api_tool_approval_request", lambda *unused, **kwargs: payload)
    monkeypatch.setattr(nodes, "interrupt", approve)
    state = {}
    await nodes._get_tool_approval_result("fixture", args, tweaks={}, state=state, api_tool_context={})
    assert secret not in str(observed)
    assert secret not in str(state["approval_requests"])
    assert observed[0]["request_payload"]["tool_args"]["title"] == "Keep title"
    assert args["client_secret"] == secret
    assert payload["request_payload"]["tool_args"]["client_secret"] == secret


def test_state_context_projections_do_not_duplicate_credentials():
    from langboard_graph.core.graph.default.context import create_runtime_context_state

    secret = "fixture-secret-not-real"
    tweaks = {
        "Graph": {"ontology_context": {"title": "Ontology", "api_key": secret}},
        "LangboardCalledVariablesComponent": {
            "project_uid": "fixture-project",
            "current_runner_data": {"uid": "fixture-actor", "settings": {"api_key": secret}},
            "rest_data": {
                "card_uid": "fixture-card",
                "related_cards": {
                    "parents": [{"title": "Parent", "headers": {"Authorization": secret}}],
                    "children": [{"title": "Child", "refresh_token": secret}],
                },
            },
        },
    }
    state = create_runtime_context_state(tweaks)
    assert secret not in str(state)
    assert state["project_context"]["project_uid"] == "fixture-project"
    assert state["project_context"]["current_runner_data"]["uid"] == "fixture-actor"
    assert state["card_context"]["card_uid"] == "fixture-card"
    assert state["parent_cards"][0]["title"] == "Parent"
    assert state["child_cards"][0]["title"] == "Child"
    assert [item["direction"] for item in state["relationship_context"]] == ["parent", "child"]
    assert state["ontology_context"]["title"] == "Ontology"
    state["parent_cards"][0]["title"] = "Changed"
    assert tweaks["LangboardCalledVariablesComponent"]["rest_data"]["related_cards"]["parents"][0]["title"] == "Parent"
    assert tweaks["Graph"]["ontology_context"]["api_key"] == secret


@pytest.mark.parametrize("last_sync", [None, "2026-10-01T00:00:00+00:00"])
@pytest.mark.parametrize("summary_enabled", [False, True])
def test_history_redacts_state_and_prompt_without_changing_delta_or_summary(monkeypatch, last_sync, summary_enabled):
    from copy import deepcopy
    from langboard_graph.core.graph.default import history

    secret = "fixture-secret-not-real"
    messages = [
        {
            "msg_id": "activity:new",
            "msg_type": "card_action",
            "source": "project_activity",
            "change_type": "new",
            "timestamp": "2026-10-02T00:00:00+00:00",
            "updated_at": "2026-10-02T00:00:00+00:00",
            "content": {"title": "New card", "nested": {"client_secret": secret}},
        },
        {
            "msg_id": "comment:edited",
            "msg_type": "comment",
            "source": "card_comment",
            "change_type": "edited",
            "timestamp": "2026-09-30T00:00:00+00:00",
            "updated_at": "2026-10-03T00:00:00+00:00",
            "content": "Edited comment",
            "lineage": {"Authorization": secret},
        },
        {
            "msg_id": "comment:deleted",
            "msg_type": "comment",
            "source": "card_comment",
            "change_type": "deleted",
            "timestamp": "2026-09-30T01:00:00+00:00",
            "updated_at": "2026-10-04T00:00:00+00:00",
            "content": "Deleted comment",
        },
    ]
    original = deepcopy(messages)
    monkeypatch.setattr(history, "collect_langboard_history_messages", lambda tweaks, config: messages)
    state = {} if last_sync is None else {"last_history_sync_at": last_sync}
    prompt = history.create_langboard_history_context_prompt(
        {"Graph": {"history_message_limit": 2, "history_summary_enabled": summary_enabled}}, state
    )
    assert secret not in prompt
    assert secret not in str(state)
    assert state["last_history_sync_at"] == "2026-10-04T00:00:00+00:00"
    assert state["history_delta"]["type"] == ("full_window" if last_sync is None else "delta")
    if last_sync:
        assert state["history_delta"]["new"][0]["msg_id"] == "activity:new"
        assert state["history_delta"]["edited"][0]["msg_id"] == "comment:edited"
        assert state["history_delta"]["deleted"][0]["msg_id"] == "comment:deleted"
    else:
        assert len(state["history_delta"]["new"]) == 3
    assert len(state["history_messages"]) == 2
    if summary_enabled:
        assert state["history_summary"]["content"]["message_count"] == 2
        assert "history_summary:" in state["history_messages"][0]["msg_id"]
    else:
        assert state["history_summary"] is None
    assert messages == original
    state["history_delta"]["new"][0]["content"] = "Changed"
    assert messages == original
