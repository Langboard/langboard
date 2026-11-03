"""Persisted relationship snapshots must not promote PRIVATE into graph context."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import Bot, Card, ProjectColumn
from sqlalchemy.orm import Session
from test_chat_history_authority import history_db  # noqa: F401
from test_runtime_authority import authority_graph, user_tweaks  # noqa: F401
from langboard_graph.core.graph.default import graph, nodes


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["parents", "children"])
@pytest.mark.parametrize("phase", ["initial", "resume"])
async def test_graph_revalidates_private_relationships_before_state_and_inference(
    authority_graph,  # noqa: F811
    history_db,  # noqa: F811
    monkeypatch,
    direction,
    phase,
):
    for model in (ProjectColumn, Bot, Card):
        model.__table__.create(history_db)
    with Session(history_db) as db:
        db.add(ProjectColumn(id=40, project_id=30, name="Work"))
        db.commit()
        db.add_all(
            [
                Card(id=50, project_id=30, project_column_id=40, title="Trigger", visibility="SHARED"),
                Card(id=51, project_id=30, project_column_id=40, title="Allowed relation", visibility="SHARED"),
                Card(
                    id=52,
                    project_id=30,
                    project_column_id=40,
                    title="Private marker",
                    visibility="PRIVATE",
                    owner_user_id=1,
                    created_by_user_id=1,
                ),
            ]
        )
        db.commit()
    context = {
        "card_uid": SnowflakeID(50).to_short_code(),
        "event_detail": "Keep event",
        "related_cards": {
            direction: [
                {"card_uid": SnowflakeID(52).to_short_code(), "title": "Private marker"},
                {"card_uid": SnowflakeID(51).to_short_code(), "title": "Allowed relation"},
            ]
        },
    }
    tweaks = user_tweaks()
    tweaks["LangboardCalledVariablesComponent"]["rest_data"] = deepcopy(context)
    tweaks["rest_data"] = deepcopy(context)
    original = deepcopy(tweaks)
    thread = uuid4().hex
    config = {"configurable": {"thread_id": thread}}
    if phase == "resume":
        for variables in (tweaks, tweaks["LangboardCalledVariablesComponent"]):
            variables["rest_data"]["related_cards"] = {}
    result = await graph.run_default_graph("run", tweaks, thread, thread)
    assert result.interrupts
    if phase == "initial":
        saved = (await graph.build_default_graph(authority_graph).aget_state(config)).values
        assert "Private marker" not in str(saved)
        assert "Allowed relation" in str(saved)
        assert tweaks == original
    else:
        saved = (await graph.build_default_graph(authority_graph).aget_state(config)).values
        poisoned = deepcopy(saved["tweaks"])
        for variables in (poisoned, poisoned["LangboardCalledVariablesComponent"]):
            variables["rest_data"] = deepcopy(context)
        historical_config = await graph.build_default_graph(authority_graph).aupdate_state(
            config,
            {
                "tweaks": poisoned,
                "parent_cards": [{"title": "Private marker"}],
                "relationship_context": [{"title": "Private marker"}],
            },
        )
        historical_before = deepcopy((await authority_graph.aget_tuple(historical_config)).checkpoint)
    messages = []

    async def invoke(_, prompt, *args, **kwargs):
        messages.extend(prompt)
        return SimpleNamespace(content="Safe result"), []

    monkeypatch.setattr(nodes, "_invoke_agent", invoke)
    result = await graph.run_default_graph(
        None,
        None,
        thread,
        thread,
        resume={"approved": True, "app_api_token": AuthSecurity.create_bot_one_time_token(1)},
    )
    assert result.message == "Safe result"
    assert "Private marker" not in str(messages)
    assert "Allowed relation" in str(messages) and "Keep event" in str(messages)
    final = (await graph.build_default_graph(authority_graph).aget_state(config)).values
    assert "Private marker" not in str(final)
    if phase == "resume":
        historical = (await authority_graph.aget_tuple(historical_config)).checkpoint
        assert "Private marker" in str(historical["channel_values"])
        assert historical == historical_before
