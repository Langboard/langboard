"""Queued relationship snapshots must be revalidated against the primary database."""

import importlib
import json
from copy import deepcopy
from unittest.mock import AsyncMock
import httpx
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.constants.DomainEventType import DomainEventType
from langboard_shared.domain.models import Bot, Card, CardRelationship, GlobalCardRelationshipType
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.tasks.bots.utils.BotTaskDataHelper import BotTaskDataHelper
from langboard_shared.tasks.bots.utils.requests.BaseBotRequest import BotRequestOutcome
from langboard_shared.tasks.bots.utils.requests.Utils import create_request


@pytest.mark.parametrize("platform", [BotPlatform.Default, BotPlatform.Langflow, BotPlatform.N8N])
@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("phase", ["queued", "retry"])
@pytest.mark.parametrize("direction", ["parents", "children"])
@pytest.mark.asyncio
async def test_queued_snapshot_excludes_current_private_before_transport(
    current_card, monkeypatch, platform, phase, direction
):
    _, project, private, _ = current_card
    for model in (GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(DbEngine.get_main_engine())
    with DbSession.use(readonly=False) as db:
        private.visibility = "SHARED"
        private.owner_user_id = None
        db.update(private)
        trigger = Card(project_id=project.id, title="Public trigger", visibility="SHARED")
        allowed = Card(project_id=project.id, title="Allowed relation", visibility="INTERNAL")
        relation = GlobalCardRelationshipType(parent_name="Parent", child_name="Child")
        for row in (trigger, allowed, relation):
            db.insert(row)
        for row in (private, allowed):
            db.insert(
                CardRelationship(
                    relationship_type_id=relation.id,
                    card_id_parent=row.id if direction == "parents" else trigger.id,
                    card_id_child=trigger.id if direction == "parents" else row.id,
                )
            )
    # Freeze the native relationship projection as it would appear in a queued event.
    queued = json.loads(
        json.dumps(
            {
                "card_uid": trigger.get_uid(),
                "card_title": trigger.title,
                "related_cards": BotTaskDataHelper.create_card_relationship_context(trigger),
                "event_detail": {"title": private.title, "unchanged": True},
            },
            default=str,
        )
    )
    original = deepcopy(queued)
    bot = Bot(
        name="Snapshot test",
        bot_uname="snapshot",
        app_api_token="test-only-token",
        platform=platform,
        platform_running_type=BotPlatformRunningType.Endpoint
        if platform == BotPlatform.Langflow
        else BotPlatformRunningType.Default,
        api_url="http://transport.invalid",
        value="{}",
    )
    request = create_request(bot, DomainEventType.BotMentioned.value, queued, project, trigger)
    monkeypatch.setattr(request, "_create_log", AsyncMock(return_value=(bot, None)))
    monkeypatch.setattr(request, "_update_log", AsyncMock())
    captured = []

    async def transport(http_request):
        captured.append(json.loads(http_request.content))
        return httpx.Response(200, text="accepted")

    client = httpx.AsyncClient
    module = importlib.import_module("langboard_shared.tasks.bots.utils.requests.BaseBotRequest")
    monkeypatch.setattr(
        module, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(transport), **kwargs)
    )
    if phase == "retry":
        envelope = request.create_request_data((bot, None))
        envelope_before = deepcopy(envelope)
    with DbSession.use(readonly=False) as db:
        private.visibility = "PRIVATE"
        private.owner_user_id = current_card[0].id
        db.update(private)
    # A stale replica must not authorize snapshot content.
    monkeypatch.setattr(
        DbEngine, "get_readonly_engine", lambda: (_ for _ in ()).throw(AssertionError("primary required"))
    )
    if phase == "retry":
        outcome = await request.request(envelope, {}, (bot, None), retried=1)
        assert envelope == envelope_before
    else:
        outcome = await request.execute()
    assert outcome == BotRequestOutcome.Accepted
    assert len(captured) == 1
    tweaks = captured[0]["tweaks"]
    contexts = [tweaks["LangboardCalledVariablesComponent"]["rest_data"]]
    if "rest_data" in tweaks:
        contexts.append(tweaks["rest_data"])
    for context in contexts:
        assert [row["card_uid"] for row in context["related_cards"][direction]] == [allowed.get_uid()]
        assert context["event_detail"] == original["event_detail"]
        assert context["card_title"] == trigger.title
    assert queued == original
    assert private.get_uid() in repr(original["related_cards"])


def test_public_webhook_projection_excludes_snapshot(current_card):
    from langboard_shared.tasks.webhooks.WebhookTask import minimal_event_data

    _, _, private, _ = current_card
    data = {
        "card_title": "Allowed",
        "related_cards": {"parents": [{"card_uid": private.get_uid(), "title": private.title}]},
    }
    assert minimal_event_data(data) == {"card_title": "Allowed"}


@pytest.mark.parametrize("retirement", ["private", "soft_deleted", "hard_deleted"])
def test_snapshot_excludes_retired_related_cards(current_card, retirement):
    _, project, current, _ = current_card
    with DbSession.use(readonly=False) as db:
        current.visibility = "SHARED"
        current.owner_user_id = None
        db.update(current)
        allowed = Card(project_id=project.id, title="Allowed", visibility="INTERNAL")
        retired = Card(
            project_id=project.id,
            title="Retired",
            visibility="SHARED",
            created_by_user_id=current_card[0].id,
        )
        db.insert(allowed)
        db.insert(retired)
        if retirement == "private":
            retired.visibility = "PRIVATE"
            retired.owner_user_id = current_card[0].id
            db.update(retired)
        elif retirement == "soft_deleted":
            retired.deleted_at = SafeDateTime.now()
            db.update(retired)
        else:
            db.delete(retired, purge=True)
    snapshot = {
        "card_uid": current.get_uid(),
        "event_detail": {"preserve": True},
        "related_cards": {
            "parents": [
                {"card_uid": allowed.get_uid(), "title": allowed.title},
                {"card_uid": retired.get_uid(), "title": retired.title},
            ],
            "children": [],
        },
    }
    filtered = BotTaskDataHelper.revalidate_related_cards(snapshot)
    assert [row["card_uid"] for row in filtered["related_cards"]["parents"]] == [allowed.get_uid()]
    assert filtered["event_detail"] == snapshot["event_detail"]
    assert snapshot["related_cards"]["parents"][1]["card_uid"] == retired.get_uid()


@pytest.mark.parametrize(
    "related_cards",
    [
        None,
        [],
        {"parents": "malformed", "children": [None, "bad", {"title": "missing uid"}, {"card_uid": "bad"}]},
        {"parents": [{"card_uid": "bad"}], "children": {"not": "a list"}},
    ],
)
def test_snapshot_malformed_relationship_shapes_are_dropped_without_event_loss(current_card, related_cards):
    data = {"card_uid": current_card[2].get_uid(), "event_detail": {"preserve": True}, "related_cards": related_cards}
    filtered = BotTaskDataHelper.revalidate_related_cards(data)
    assert filtered["event_detail"] == data["event_detail"]
    if isinstance(related_cards, dict):
        assert filtered["related_cards"] == {"parents": [], "children": []}
    else:
        assert filtered is data
