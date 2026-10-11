# ruff: noqa: F811
"""Committed execution acceptance dispatches only approved frozen destinations."""
import importlib
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import AppExecutionOutbox, BoardAppBinding
from langboard_shared.domain.services.AppEventDestination import bind_app_event_destination
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from sqlalchemy import text
from test_app_event_destination import destination_scope
from test_app_execution_requests import accepted_request


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("broker_failure", [False, True])
def test_approved_execution_dispatch_commit_duplicate_and_rollback(board, monkeypatch, broker_failure):
    connection, definition, setting, _ = destination_scope(board)
    with DbSession.atomic() as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
        binding.granted_capabilities = [*binding.granted_capabilities, "events.receive"]
        db.update(binding)
    bind_app_event_destination(board[1], connection.id, setting.id, None)
    tasks = importlib.import_module("langboard_shared.tasks.webhooks.AppEventDeliveryTask")
    enqueues = []
    def enqueue(*args):
        # Callback is after commit: independent connection sees persisted event.
        with DbEngine.get_main_engine().connect() as db:
            assert db.execute(text("SELECT COUNT(*) FROM app_execution_outbox WHERE payload IS NOT NULL")).scalar() >= 2
        enqueues.append(args)
        if broker_failure:
            raise RuntimeError("Test broker unavailable")
    monkeypatch.setattr(tasks, "app_event_delivery_task", enqueue)
    from langboard_shared.domain.models import Card
    from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
    with DbSession.use(readonly=False) as db:
        card = db.exec(SqlBuilder.select.table(Card)).first()
    token = issue_connection_credential(board[1], connection.id)["token"]
    with DbEngine.get_main_engine().begin() as db:
        db.execute(text("UPDATE card_execution_generation SET execution_generation=4"))
    receipt = accepted_request(token, board[2].id, card.id, 4)
    assert receipt["changed"] and receipt["started"] is False
    with DbSession.use(readonly=False) as db:
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox).order_by(AppExecutionOutbox.id.desc())).first()
        assert event.payload["destination"]["revision"] == 1
        assert event.state == "pending" and event.attempt_count == 0
    assert enqueues == [(event.get_uid(), 1)]
    assert accepted_request(token, board[2].id, card.id, 4)["changed"] is False
    assert len(enqueues) == 1
    with DbEngine.get_main_engine().begin() as db:
        db.execute(text("UPDATE card_execution_generation SET execution_generation=5"))
    with pytest.raises(RuntimeError, match="Rollback"):
        with DbSession.atomic():
            accepted_request(token, board[2].id, card.id, 5)
            raise RuntimeError("Rollback")
    assert len(enqueues) == 1
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(AppExecutionOutbox)).all()) == 2
    # Without a current board event grant no network dispatch is inferred.
    with DbSession.atomic() as db:
        binding.granted_capabilities = ["resources.read", "execution.run"]
        db.update(binding)
    assert accepted_request(token, board[2].id, card.id, 5)["changed"] is True
    assert len(enqueues) == 1
