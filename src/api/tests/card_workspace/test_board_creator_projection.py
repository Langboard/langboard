"""Author enrichment must not load the board's document bodies a second time."""

from datetime import timedelta
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, Project, User
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.infrastructure.repositories.factory.CardRepository import CardRepository
from sqlalchemy import create_engine, event


def test_board_creators_keep_author_and_archive_scope_without_loading_bodies(monkeypatch):
    engine = create_engine("sqlite://")
    for model in (User, Bot, Project, Card):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    cutoff = SafeDateTime.now() - timedelta(days=7)
    loaded_cards = []

    def capture_card(card, _context):
        loaded_cards.append(card.id)

    try:
        with DbSession.use(readonly=False) as db:
            user = User(firstname="Fixture", lastname="Author", email="author@example.invalid", password="test-only")
            bot = Bot(name="Fixture bot", bot_uname="fixture", app_api_token="test-only",
                      platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default)
            db.insert(user)
            db.insert(bot)
            project = Project(title="Board", owner_id=user.id)
            foreign = Project(title="Other", owner_id=user.id)
            db.insert(project)
            db.insert(foreign)
            cards = [
                Card(project_id=project.id, title="Human", created_by_user_id=user.id),
                Card(project_id=project.id, title="Bot", created_by_bot_id=bot.id),
                Card(project_id=project.id, title="No author"),
                Card(project_id=project.id, title="Recent archive", created_by_user_id=user.id, archived_at=cutoff),
                Card(project_id=project.id, title="Cold archive", created_by_user_id=user.id,
                     archived_at=cutoff - timedelta(seconds=1)),
                Card(project_id=foreign.id, title="Foreign", created_by_user_id=user.id),
                Card(project_id=project.id, title="Deleted", created_by_user_id=user.id, deleted_at=SafeDateTime.now()),
            ]
            for card in cards:
                card.description = EditorContentModel(content="Large document 한국어 日本語 " * 4000)
                card.ai_description = "AI body " * 4000
                db.insert(card)
        selects = []

        def capture(_connection, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        event.listen(Card, "load", capture_card)
        authors = CardRepository(None, None).get_board_creators(project, cutoff)
        assert {key: value.get_uid() for key, value in authors.items()} == {
            cards[0].id: user.get_uid(), cards[1].id: bot.get_uid(), cards[3].id: user.get_uid(),
        }
        assert len(selects) == 1
        selected = selects[0].split("\nFROM", 1)[0]
        assert "card.description" not in selected
        assert "card.ai_description" not in selected
        assert authors[cards[0].id].firstname == "Fixture"
        assert authors[cards[1].id].name == "Fixture bot"
        assert loaded_cards == [], "Author enrichment must not hydrate card models again"
    finally:
        if event.contains(Card, "load", capture_card):
            event.remove(Card, "load", capture_card)
        engine.dispose()


def test_board_author_query_only_loads_display_fields_without_serializing_accounts(board, monkeypatch):  # noqa: F811
    _registry, user, project, _other, _connection, columns, _ = board
    for model in (Bot, Card):
        model.__table__.create(DbEngine.get_main_engine())
    avatar = FileModel(
        storage_type="test",
        storage_name="test",
        original_filename="avatar.png",
        filename="avatar.png",
        path="/fixture/avatar.png",
    )
    with DbSession.use(readonly=False) as db:
        user.avatar = avatar
        db.update(user)
        bot = Bot(
            name="Fixture bot",
            bot_uname="fixture-author",
            app_api_token="private-bot-token",
            api_key="private-bot-key",
            value="private configuration " * 2000,
            avatar=avatar,
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(bot)
        human_card = Card(
            project_id=project.id, project_column_id=columns[0].id, title="Human author", created_by_user_id=user.id
        )
        bot_card = Card(
            project_id=project.id, project_column_id=columns[0].id, title="Bot author", created_by_bot_id=bot.id
        )
        db.insert(human_card)
        db.insert(bot_card)
    selects = []
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)

    def capture(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        authors = CardRepository(None, None).get_board_creators(project, SafeDateTime.now() - timedelta(days=7))
        selected = selects[0].split("FROM", 1)[0].replace('"', "")
        for private_column in ("user.password", "user.email", "bot.api_key", "bot.app_api_token", "bot.value"):
            assert private_column not in selected
        monkeypatch.setattr(User, "api_response", Mock(side_effect=AssertionError("No account serialization")))
        monkeypatch.setattr(Bot, "api_response", Mock(side_effect=AssertionError("No bot configuration serialization")))
        for card, creator, name, kind in (
            (human_card, user, user.get_fullname(), "user"),
            (bot_card, bot, "Fixture bot", "bot"),
        ):
            assert CardService._card_creator_projection(None, card, authors[card.id]) == {
                "uid": creator.get_uid(),
                "type": kind,
                "name": name,
                "avatar": avatar.path,
                "created_at": card.created_at.isoformat(),
            }
        assert len(selects) == 1, "Author rendering must not lazy-load deferred account fields"
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("kind", ["missing", "user_without_avatar", "bot_without_avatar", "deleted_user"])
def test_creator_projection_preserves_absent_and_deleted_avatar_behavior(kind):
    card = Card(title="Author display")
    if kind == "missing":
        assert CardService._card_creator_projection(None, card, None) is None
        return
    if kind == "bot_without_avatar":
        creator = Bot(
            name="Bot",
            bot_uname="fixture",
            app_api_token="fixture",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
    else:
        creator = User(firstname="Fixture", lastname="Author", email="author@example.invalid", password="fixture")
        if kind == "deleted_user":
            creator.deleted_at = SafeDateTime.now()
            creator.avatar = FileModel(
                storage_type="test",
                storage_name="test",
                original_filename="avatar.png",
                filename="avatar.png",
                path="/fixture/deleted-avatar.png",
            )
    payload = CardService._card_creator_projection(None, card, creator)
    assert payload["avatar"] is None
    assert payload["uid"] == creator.get_uid()
    assert payload["name"] == creator.get_fullname()
