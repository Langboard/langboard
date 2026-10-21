"""An explicit provider connection creates a separate, replay-safe compactor."""

import json
import pytest
from langboard.commands.CreateCommentCompactorCommand import create_comment_compactor
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import InternalBot
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.publishers import InternalBotPublisher


@pytest.fixture
def connection(board, monkeypatch):
    InternalBot.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(InternalBotPublisher, "created", lambda *_: None)
    with DbSession.atomic() as db:
        source = InternalBot(
            display_name="Editor Chat", bot_type=InternalBotType.EditorChat, is_default=True,
            platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
            value=json.dumps({"agent_llm": "Z.ai Coding Plan", "base_url": "https://api.z.ai/api/coding/paas/v4",
                              "model_name": "glm-5.3", "api_key": "fixture-key-not-a-real-credential",
                              "api_names": ["change_card"], "system_prompt": "Editor instructions",
                              "comfort_tool_names": ["batch_apis"]}),
        )
        db.insert(source)
    return source


def test_separate_compactor_keeps_source_and_strips_chat_behavior(connection):
    source_value = connection.value
    first = create_comment_compactor(connection.get_uid())
    second = create_comment_compactor(connection.get_uid())
    assert first["created"] is True and second["created"] is False
    assert first["uid"] == second["uid"] != connection.get_uid()
    assert first["display_name"] == "컴팩터"
    assert "fixture-key" not in json.dumps(first)
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(InternalBot)).all()
    assert len(rows) == 2
    source = next(row for row in rows if row.id == connection.id)
    compactor = next(row for row in rows if row.id != connection.id)
    assert source.value == source_value and source.is_default
    assert compactor.bot_type == InternalBotType.CommentCompaction and compactor.is_default
    assert not compactor.bot_type.is_executable
    assert set(json.loads(compactor.value)) == {"agent_llm", "base_url", "api_key", "model_name"}
    assert json.loads(compactor.value)["api_key"] == json.loads(source.value)["api_key"]


@pytest.mark.parametrize("patch", [
    {"agent_llm": "OpenAI"}, {"base_url": "https://other.invalid"},
    {"model_name": ""}, {"api_key": ""}, {"base_url": None},
])
def test_invalid_connection_creates_nothing(connection, patch):
    with DbSession.atomic() as db:
        connection.value = json.dumps({**json.loads(connection.value), **patch})
        db.update(connection)
    with pytest.raises(ValueError, match="configured Z.ai"):
        create_comment_compactor(connection.get_uid())
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(InternalBot)).all()) == 1


def test_existing_compactor_is_not_overwritten_when_connection_changes(connection):
    first = create_comment_compactor(connection.get_uid())
    with DbSession.atomic() as db:
        connection.value = json.dumps({**json.loads(connection.value), "model_name": "another-model"})
        db.update(connection)
    assert create_comment_compactor(connection.get_uid())["created"] is False
    with DbSession.use(readonly=False) as db:
        compactor = db.exec(SqlBuilder.select.table(InternalBot).where(
            InternalBot.bot_type == InternalBotType.CommentCompaction,
        )).first()
    assert compactor.get_uid() == first["uid"]
    assert json.loads(compactor.value)["model_name"] == "glm-5.3"


def test_existing_default_is_not_replaced(connection):
    with DbSession.atomic() as db:
        existing = InternalBot(
            display_name="Existing compaction", bot_type=InternalBotType.CommentCompaction, is_default=True,
            platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
            value='{"agent_llm":"OpenAI"}',
        )
        db.insert(existing)
    with pytest.raises(ValueError, match="existing compaction default"):
        create_comment_compactor(connection.get_uid())
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(InternalBot)).all()
    assert len(rows) == 2
    assert next(row for row in rows if row.id == existing.id).is_default
