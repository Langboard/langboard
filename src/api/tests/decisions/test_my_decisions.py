"""Native outstanding inbox: current visibility and response state, no mutation."""
# ruff: noqa: F811

from datetime import datetime, timedelta, timezone
import pytest
from langboard_shared.domain import models as m
from langboard_shared.domain.services.DecisionFlow import DecisionDenied, DecisionMissing
from sqlalchemy import event, select, update
from test_decision_flow import create, native, respond  # noqa: F401
from test_decision_poll import create_poll, vote


def inbox(native, actor=1, **kwargs):
    _, flow, users, board, _, _ = native
    return flow.my_decisions(users[actor], board.get_uid(), **kwargs)


def test_outstanding_pagination_and_immutable_history(native):
    engine, _, _, _, _, _ = native
    first = create(native)
    second = create(native)
    create(native, participants=[native[2][2].get_uid()])
    page = inbox(native, limit=1)
    assert [item["uid"] for item in page["items"]] == [second["uid"]]
    assert page["items"][0]["allowed_actions"] == ["respond"]
    assert page["next_cursor"] == second["uid"]
    assert [item["uid"] for item in inbox(native, before=page["next_cursor"])["items"]] == [first["uid"]]
    outcome = respond(native, second)
    assert outcome["state"] == "passed"
    assert [item["uid"] for item in inbox(native)["items"]] == [first["uid"]]
    with engine.connect() as db:
        rows = db.execute(select(m.DecisionResponse.__table__)).mappings().all()
        assert len(rows) == 1 and rows[0]["receipt"] == outcome


def test_sequence_only_active_stage_and_expired_target(native):
    engine, _, users, _, _, card = native
    case = create(
        native,
        participants=[u.get_uid() for u in users[1:3]],
        policy={"op": "sequence", "required": 0, "children": [{"actor_uid": u.get_uid()} for u in users[1:3]]},
    )
    assert inbox(native, actor=2)["items"] == []
    respond(native, case)
    assert len(inbox(native, actor=2)["items"]) == 1
    assert inbox(native)["items"] == []
    with engine.begin() as db:
        db.execute(update(m.Card).where(m.Card.id == card.id).values(title="New target revision"))
    assert inbox(native, actor=2)["items"] == []


def test_poll_existing_ballot_and_closed_deadline(native):
    engine, _, _, _, _, _ = native
    case = create_poll(native)
    assert len(inbox(native)["items"]) == 1
    vote(native, case)
    assert inbox(native)["items"] == []  # allow_change remains available on card
    assert len(inbox(native, actor=2)["items"]) == 1
    with engine.begin() as db:
        db.execute(update(m.DecisionCase).values(deadline=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert inbox(native, actor=2)["items"] == []


def test_current_acl_private_card_membership_and_read_only(native):
    engine, flow, users, board, foreign, card = native
    create(native)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert len(inbox(native)["items"]) == 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any("LOCK TABLE" in sql or "BEGIN IMMEDIATE" in sql for sql in statements)
    assert not any(sql.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) for sql in statements)
    with pytest.raises(DecisionMissing):
        inbox(native, actor=5)
    with pytest.raises(DecisionMissing):
        flow.my_decisions(users[1], foreign.get_uid())
    with engine.begin() as db:
        db.execute(
            update(m.Card)
            .where(m.Card.id == card.id)
            .values(visibility="PRIVATE", owner_user_id=users[0].id, created_by_user_id=users[0].id)
        )
    assert inbox(native)["items"] == []
    with engine.begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
    with pytest.raises(DecisionDenied):
        inbox(native)


@pytest.mark.parametrize("changes", [{"limit": 0}, {"limit": True}, {"limit": 51}, {"before": "invalid"}])
def test_inbox_invalid_pagination(native, changes):
    with pytest.raises(ValueError):
        inbox(native, **changes)


def test_sparse_page_has_bounded_scan_and_continuation(native):
    from uuid import uuid4
    from langboard_shared.core.db import DbSession

    _, _, users, _, _, _ = native
    outstanding = create(native)
    other = create(native, participants=[users[2].get_uid()])
    with DbSession.atomic() as db:
        row = db.exec(select(m.DecisionCase).where(m.DecisionCase.round_id == other["round_id"])).first()
        data = {name: getattr(row[0], name) for name in m.DecisionCase.model_fields if name != "id"}
        for _ in range(50):
            db.insert(m.DecisionCase(**{**data, "request_id": str(uuid4()), "round_id": str(uuid4())}))
    page = inbox(native)
    assert page["items"] == [] and page["next_cursor"] is not None
    last = inbox(native, before=page["next_cursor"])
    assert [item["uid"] for item in last["items"]] == [outstanding["uid"]]
    assert last["next_cursor"] is None
