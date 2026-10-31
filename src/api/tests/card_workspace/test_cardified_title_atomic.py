"""Cardified title changes commit together and publish frozen state after commit."""

from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Card, CardChangeReceipt, Checkitem, Checklist
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.infrastructure.repositories import Repository
from langboard_shared.publishers import CardPublisher, CheckitemPublisher
from langboard_shared.tasks.activities import CardCheckitemActivityTask
from langboard_shared.tasks.bots import CardCheckitemBotTask
from sqlalchemy import select, text


@pytest.mark.parametrize("mode", ["second_write_failure", "outer_rollback", "commit"])
def test_cardified_title_transaction_and_after_commit(board, monkeypatch, mode):
    _, user, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for model in (Card, Checklist, Checkitem, CardChangeReceipt):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        if engine.dialect.name == "postgresql":
            db.exec(text("CREATE SEQUENCE content_change_seq"))
        owner = Card(project_id=project.id, project_column_id=columns[0].id, title="owner")
        linked = Card(project_id=project.id, project_column_id=columns[0].id, title="old")
        db.insert(owner)
        db.insert(linked)
        checklist = Checklist(card_id=owner.id, title="tasks")
        db.insert(checklist)
        item = Checkitem(checklist_id=checklist.id, cardified_id=linked.id, title="old")
        db.insert(item)
    published = []
    monkeypatch.setattr(
        CheckitemPublisher, "title_changed", lambda p, c, i, linked: published.append((i.title, linked.title))
    )
    activity = Mock()
    bot = Mock()
    metadata = Mock()
    monkeypatch.setattr(CardCheckitemActivityTask, "card_checkitem_title_changed", activity)
    monkeypatch.setattr(CardCheckitemBotTask, "card_checkitem_title_changed", bot)
    monkeypatch.setattr(CardPublisher, "metadata_changed", metadata)

    native_update = DbSession.update

    def update_then_fail(db, model):
        native_update(db, model)
        if isinstance(model, Checkitem) and mode == "second_write_failure":
            raise RuntimeError("second write failed")

    monkeypatch.setattr(DbSession, "update", update_then_fail)
    repository = Repository()
    cards = CardService(None, None, repository)
    service = CheckitemService(lambda _: cards, None, repository)

    with pytest.raises(AppGovernanceDenied):
        service.change_title(object(), project, owner, item, "rejected")

    def change():
        with DbSession.atomic():
            assert service.change_title(user, project.get_uid(), owner.get_uid(), item.get_uid(), "first") is True
            assert not published and not metadata.called
            assert service.change_title(user, project.get_uid(), owner.get_uid(), item.get_uid(), "second") is True
            assert not published and not metadata.called
            if mode == "outer_rollback":
                raise RuntimeError("outer rollback")

    if mode == "commit":
        change()
        assert published == [("first", "first"), ("second", "second")]
        assert activity.call_count == bot.call_count == 2
        assert metadata.call_count == 2
    else:
        with pytest.raises(RuntimeError):
            change()
        assert published == []
        activity.assert_not_called()
        bot.assert_not_called()
        metadata.assert_not_called()
    with engine.connect() as connection:
        expected = "second" if mode == "commit" else "old"
        assert connection.execute(select(Card.title).where(Card.id == linked.id)).scalar_one() == expected
        assert connection.execute(select(Checkitem.title).where(Checkitem.id == item.id)).scalar_one() == expected
        seq = connection.execute(select(Card.last_change_seq).where(Card.id == owner.id)).scalar_one()
        receipts = connection.execute(
            select(CardChangeReceipt.change_seq).where(CardChangeReceipt.card_id == owner.id)
            .order_by(CardChangeReceipt.change_seq)
        ).scalars().all()
        assert receipts == ([1, 2] if mode == "commit" else [])
        assert seq == (2 if mode == "commit" else 0)
