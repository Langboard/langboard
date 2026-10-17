"""Author enrichment must not load the board's document bodies a second time."""

from datetime import timedelta
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, Card, Project, User
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
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
