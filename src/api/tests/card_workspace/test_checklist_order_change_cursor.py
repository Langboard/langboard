"""Order, membership, unread cursor and notifications commit together."""

from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Card, CardChangeReceipt, Checkitem, Checklist
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.domain.services.factory.ChecklistService import ChecklistService
from langboard_shared.infrastructure.repositories import Repository
from langboard_shared.publishers import CardPublisher, CheckitemPublisher, ChecklistPublisher
from sqlalchemy import select, text


@pytest.mark.parametrize("kind", ["checklist", "checkitem", "transfer"])
@pytest.mark.parametrize("mode", ["commit", "stamp_failure", "outer_rollback", "noop"])
def test_order_cursor_and_event_transaction(board, monkeypatch, kind, mode):
    _, user, project, _, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for model in (Card, Checklist, Checkitem, CardChangeReceipt):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        if engine.dialect.name == "postgresql":
            db.exec(text("CREATE SEQUENCE content_change_seq START 8"))
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="fixture", last_change_seq=7)
        db.insert(card)
        old = Checklist(card_id=card.id, title="first", order=0)
        new = Checklist(card_id=card.id, title="second", order=1)
        db.insert(old)
        db.insert(new)
        item = Checkitem(checklist_id=old.id, title="task", order=0)
        db.insert(item)
    events = [Mock(), Mock(), Mock()]
    monkeypatch.setattr(ChecklistPublisher, "order_changed", events[0])
    monkeypatch.setattr(CheckitemPublisher, "order_changed", events[1])
    monkeypatch.setattr(CardPublisher, "metadata_changed", events[2])

    native_insert = DbSession.insert

    def insert_then_fail(db, model):
        native_insert(db, model)
        if isinstance(model, CardChangeReceipt) and mode == "stamp_failure":
            raise RuntimeError("stamp failed")

    monkeypatch.setattr(DbSession, "insert", insert_then_fail)
    repository = Repository()
    card_service = CardService(None, None, repository)
    cls = ChecklistService if kind == "checklist" else CheckitemService
    service = cls(lambda _: card_service, lambda _: card_service, repository)

    def invoke():
        if kind == "checklist":
            return service.change_order(user, project, card, old, 0 if mode == "noop" else 1)
        target = new.get_uid() if kind == "transfer" and mode != "noop" else ""
        return service.change_order(user, project, card, item, 0 if kind == "transfer" or mode == "noop" else 1, target)

    if mode == "stamp_failure":
        with pytest.raises(RuntimeError, match="stamp failed"):
            invoke()
    elif mode == "outer_rollback":
        with pytest.raises(RuntimeError, match="rollback"):
            with DbSession.atomic():
                assert invoke() is True
                assert not any(event.called for event in events)
                raise RuntimeError("rollback")
    else:
        assert invoke() is True
    with engine.connect() as connection:
        model, target = (Checklist, old) if kind == "checklist" else (Checkitem, item)
        position = connection.execute(select(model.order).where(model.id == target.id)).scalar_one()
        parent = connection.execute(select(Checkitem.checklist_id).where(Checkitem.id == item.id)).scalar_one()
        seq, target_type = connection.execute(
            select(Card.last_change_seq, Card.last_change_target_type).where(Card.id == card.id)
        ).one()
        receipts = connection.execute(select(CardChangeReceipt.change_seq)).scalars().all()
    state = (position, parent, seq)
    if mode == "commit":
        assert state == (0 if kind == "transfer" else 1, new.id if kind == "transfer" else old.id, 8)
        event = events[0] if kind == "checklist" else events[1]
        event.assert_called_once()
        events[2].assert_called_once()
        changed_card = events[2].call_args.args[0]
        assert changed_card.last_change_target_type == ("checklist" if kind == "checklist" else "checkitem")
        assert target_type == changed_card.last_change_target_type
        assert receipts == [8]
    else:
        assert state == (0, old.id, 7)
        assert receipts == []
        assert not any(event.called for event in events)
