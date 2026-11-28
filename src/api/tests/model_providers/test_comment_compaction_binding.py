"""The compaction alias stays independent of executable bots and current on the primary."""

import json
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import InternalBot
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
from langboard_shared.infrastructure.repositories import Repository
from langboard_shared.publishers import InternalBotPublisher


@pytest.fixture
def bindings(board, monkeypatch):
    InternalBot.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(InternalBotPublisher, "created", lambda *_: None)
    monkeypatch.setattr(InternalBotPublisher, "updated", lambda *_: None)
    return InternalBotService(lambda _: None, lambda _: None, Repository())


def create(service, kind, default=False, **settings):
    return service.create(kind, "Fixture provider", BotPlatform.Default, BotPlatformRunningType.Default,
                          is_default=default, value=json.dumps({"agent_llm": "OpenAI", "model_name": "fixture-model"}), **settings)


def test_compaction_uses_only_its_current_default_and_never_executable_chat_fallback(bindings):
    chat = create(bindings, InternalBotType.ProjectChat, True)
    assert bindings.get_comment_compaction_binding() is None
    first = create(bindings, InternalBotType.CommentCompaction, True)
    second = create(bindings, InternalBotType.CommentCompaction)
    assert bindings.get_comment_compaction_binding().id == first.id
    bindings.repo.internal_bot.replace_default(second, InternalBotType.CommentCompaction)
    assert bindings.get_comment_compaction_binding().id == second.id
    # The fixture forbids replica reads; this resolver must remain on the primary.
    assert InternalBotType.CommentCompaction.is_executable is False
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(InternalBot).where(InternalBot.bot_type.in_(InternalBotType.executable_types()))).all()
    assert [row.id for row in rows] == [chat.id]


@pytest.mark.parametrize("platform,running,url", [
    (BotPlatform.N8N, BotPlatformRunningType.Default, ""),
    (BotPlatform.Default, BotPlatformRunningType.Endpoint, ""),
    (BotPlatform.Default, BotPlatformRunningType.Default, "https://external.example.invalid"),
])
def test_non_native_compaction_cannot_be_created_or_changed(bindings, platform, running, url):
    with pytest.raises(ValueError, match="native provider"):
        bindings.create(InternalBotType.CommentCompaction, "Invalid", platform, running, api_url=url)
    binding = create(bindings, InternalBotType.CommentCompaction, True)
    old = bindings.get_comment_compaction_binding().api_response(is_setting=True)
    with pytest.raises(ValueError, match="native provider"):
        bindings.update(binding, {"platform": platform, "platform_running_type": running, "api_url": url})
    assert bindings.get_comment_compaction_binding().api_response(is_setting=True) == old


def test_invalid_legacy_default_is_not_used_as_a_compaction_provider(bindings):
    binding = create(bindings, InternalBotType.CommentCompaction, True)
    with DbSession.use(readonly=False) as db:
        binding.api_url = "https://legacy-runner.example.invalid"
        db.update(binding)
    assert bindings.get_comment_compaction_binding() is None


def test_deleted_compaction_default_is_not_used(bindings):
    binding = create(bindings, InternalBotType.CommentCompaction, True)
    with DbSession.use(readonly=False) as db:
        binding.deleted_at = SafeDateTime.now()
        db.update(binding)
    assert bindings.get_comment_compaction_binding() is None
