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
