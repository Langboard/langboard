"""Human intent must produce the canonical request, delivery identity and generation."""

import pytest
from sqlalchemy import select, text
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import AppExecutionOutbox, AppExecutionRequest, CardExecutionIntent, EmployeeMembershipPolicy
from langboard_shared.domain.services.CardExecutionIntent import (
    read_execution_intent_context, read_execution_intent_receipt, request_card_execution_intent,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from test_app_execution_grant import grant_scope


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("initial_generation", [0, 3])
def test_human_intent_uses_canonical_request_and_replay_fences(board, initial_generation):
    connection, definition, _, _, card, _ = grant_scope(board)
    CardExecutionIntent.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    AppExecutionOutbox.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    EmployeeMembershipPolicy.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.atomic() as db:
        db.exec(text("UPDATE card_execution_generation SET execution_generation=:generation"),
                params={"generation": initial_generation})
    actor, project = board[1:3]
    snapshot = read_execution_intent_context(actor, project.id, card.id, connection.app_key, connection.id)
    key = "human_run_request_001"
    result = request_card_execution_intent(actor, project.id, card.id, connection.app_key,
                                           connection.id, key, initial_generation, snapshot)
    assert result["generation"] == initial_generation + 1 and result["changed"] and not result["started"]
    request_id = SnowflakeID.from_short_code(result["request_uid"])
    with DbSession.use(readonly=False) as db:
        request = db.exec(SqlBuilder.select.table(AppExecutionRequest).where(AppExecutionRequest.id == request_id)).first()
        event = db.exec(SqlBuilder.select.table(AppExecutionOutbox).where(AppExecutionOutbox.request_id == request_id)).first()
        assert request.generation == initial_generation + 1
        assert event.payload["request_uid"] == result["request_uid"]
        assert db.exec(select(text("execution_generation")).select_from(text("card_execution_generation")).where(text("card_id=:card")),
                       params={"card": int(card.id)}).first()[0] == initial_generation + 1
    replay = request_card_execution_intent(actor, project.id, card.id, connection.app_key,
                                           connection.id, key, initial_generation, snapshot)
    assert replay == {**result, "changed": False}
    with pytest.raises(AppGovernanceConflict):
        request_card_execution_intent(actor, project.id, card.id, connection.app_key,
                                      connection.id, "another_human_run_001", initial_generation, snapshot)
    with DbSession.atomic() as db:
        definition.is_enabled = False
        db.update(definition)
    receipt = read_execution_intent_receipt(actor, project.id, card.id, connection.app_key, connection.id, request_id)
    assert receipt["request_uid"] == result["request_uid"] and receipt["snapshot"] == snapshot
    with pytest.raises(AppGovernanceDenied):
        request_card_execution_intent(actor, project.id, card.id, connection.app_key,
                                      connection.id, key, initial_generation, snapshot)
