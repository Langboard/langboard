import pytest
from langboard_graph.core.graph.default.privacy import redact_graph_credentials
from langboard_graph.core.graph.default.tooling import create_langboard_context_prompt, create_langboard_event_input


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
    from langboard_graph.core.graph.default.graph import run_default_graph

    raw_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Requires disposable PostgreSQL")
    url = raw_url.replace("postgresql+psycopg://", "postgresql://")
    await checkpoint.close_graph_checkpointer()
    monkeypatch.setattr(type(checkpoint.Env), "MAIN_DATABASE_URL", property(lambda self: url))
    secret = "fixture-secret-not-real"
    thread = "private-interrupt-" + uuid4().hex
    tweaks = {
        "Graph": {
            "approval_request": {
                "message": "Approve action",
                "request_payload": {"headers": {"Authorization": secret}, "title": "Keep title"},
            }
        }
    }
    try:
        result = await run_default_graph("run", tweaks, thread, thread)
        assert result.interrupts
        assert secret not in str(result.model_dump())
        async with checkpoint.open_graph_checkpointer() as saver:
            snapshot = await saver.aget_tuple({"configurable": {"thread_id": thread}})
            assert snapshot is not None
            # Only approval metadata is covered; raw tweaks remain a separate gap.
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
