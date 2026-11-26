from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from threading import Barrier
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard.card_workspace.application.creation_replay import create_once
from langboard.card_workspace.domain import CardInputRejected
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import Card
from langboard_shared.domain.models.CardCreationReceipt import CardCreationReceipt
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env


@pytest.fixture
def creation_board(board, monkeypatch):
    _, actor, project, member, _, columns, _ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    Card.__table__.create(engine)
    migration_path = (
        Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261126040500-2be76ee96598.py"
    )
    spec = spec_from_file_location("card_creation_replay_migration", migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()

    def save_card(session_callback=None):
        with DbSession.atomic() as db:
            card = Card(project_id=project.id, project_column_id=columns[0].id, title="QA", visibility="SHARED")
            db.insert(card)
            if session_callback:
                session_callback()
            return card, {"uid": card.get_uid(), "project_uid": project.get_uid()}

    def call(key, intent=None, create=save_card):
        service = DomainService()
        try:
            return create_once(service, actor, project.get_uid(), key, ["QA"] if intent is None else intent, create)
        finally:
            service.close()

    return call, save_card, member


def test_creation_replay_requires_same_intent_and_current_visibility(creation_board):
    call, _, member = creation_board
    key = str(uuid4())
    first = call(key)
    assert call(key.upper()) == {**first, "replayed": True}
    with pytest.raises(CardInputRejected, match="different input"):
        call(key, ["different"])
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(Card)).all()) == 1
        db.delete(member)
    with pytest.raises(CardInputRejected, match="unavailable"):
        call(key)
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(CardCreationReceipt)).all()) == 1


def test_save_failure_discards_reservation_and_card(creation_board):
    call, save_card, _ = creation_board
    key = str(uuid4())

    def fail():
        raise RuntimeError("injected creation failure")

    with pytest.raises(RuntimeError, match="injected creation failure"):
        call(key, create=lambda: save_card(fail))
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(Card)).all() == []
        assert db.exec(SqlBuilder.select.table(CardCreationReceipt)).all() == []
    assert "replayed" not in call(key)


def test_concurrent_requests_return_one_card(creation_board):
    if DbEngine.get_main_engine().dialect.name != "postgresql":
        pytest.skip("Concurrent sessions require PostgreSQL with this fixture")
    call, _, _ = creation_board
    key = str(uuid4())
    start = Barrier(2)

    def attempt():
        start.wait(timeout=2)
        return call(key)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [job.result(timeout=8) for job in [executor.submit(attempt), executor.submit(attempt)]]
    assert first["uid"] == second["uid"]
    assert sum(result.get("replayed", False) for result in (first, second)) == 1
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(Card)).all()) == 1
