"""Named Polls on actual native SQLite/PostgreSQL and authenticated HTTP."""
# ruff: noqa: F811 -- imported pytest fixture is intentionally injected by name

import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from langboard.routes.board.forms.Decision import CreateDecisionForm
from langboard_shared.domain import models as m
from langboard_shared.domain.services.DecisionFlow import DecisionConflict, DecisionDenied, card_target_revision
from pydantic import ValidationError
from sqlalchemy import select, update
from test_decision_flow import form, native, respond  # noqa: F401


def create_poll(native, participants=None, *, as_payload=False, **settings):
    _, flow, users, board, _, card = native
    config = dict(
        options=[dict(key="yes", label="Proceed"), dict(key="later", label="Defer")],
        participation_quorum=1,
        participation_policy="vote_only",
        winner_policy="plurality",
        tie_policy="needs_info",
        no_quorum_policy="expired",
        no_winner_policy="needs_info",
        needs_info_policy="record",
        allow_change=True,
        tally_visibility="before",
    )
    config.update(settings)
    data = dict(
        request_id=str(uuid4()),
        source_kind="poll",
        source_ref=card.get_uid(),
        question="Which option?",
        target_revision=card_target_revision(card),
        participant_uids=participants or [user.get_uid() for user in users[1:5]],
        deadline=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        poll=config,
    )
    payload = CreateDecisionForm(**data)
    return payload if as_payload else flow.create(users[0], board.get_uid(), card.get_uid(), payload)


def vote(native, case, actor=1, option=0, **changes):
    choice = changes.pop("choice", "vote")
    payload = form(
        case, choice=choice, option_uid=case["poll"]["options"][option]["uid"] if choice == "vote" else None, **changes
    )
    return respond(native, case, actor, payload)


def summary(native, actor=0):
    _, flow, users, board, _, card = native
    return flow.list(users[actor], board.get_uid(), card.get_uid())["items"][0]


def close(native, case, monkeypatch):
    deadline = datetime.fromisoformat(case["deadline"])

    class Clock:
        @staticmethod
        def now(tz):
            return deadline

    monkeypatch.setattr(importlib.import_module("langboard_shared.domain.services.DecisionFlow"), "datetime", Clock)
    return summary(native)


def test_relational_options_latest_history_and_immutable_replay(native):
    engine, flow, users, board, _, _ = native
    case = create_poll(native)
    assert case["state"] == "pending" and case["effect_result"] is None
    payload = form(case, choice="vote", option_uid=case["poll"]["options"][0]["uid"])
    first = respond(native, case, payload=payload)
    second = vote(native, first, option=1)
    assert second["response_revision"] == 2
    assert second["poll"]["tally"]["vote_count"] == 1
    assert second["poll"]["tally"]["counts_by_option"][case["poll"]["options"][0]["uid"]] == 0
    replay = respond(native, second, payload=payload)
    assert replay == {**first, "replayed": True}
    with pytest.raises(DecisionConflict):
        respond(native, second, payload=payload.model_copy(update={"choice": "abstain", "option_uid": None}))
    with engine.connect() as db:
        assert len(db.execute(select(m.DecisionOption)).all()) == 2
        rows = (
            db.execute(select(m.DecisionResponse.__table__).order_by(m.DecisionResponse.response_order))
            .mappings()
            .all()
        )
        assert [row["response_revision"] for row in rows] == [1, 2]
        assert rows[0]["receipt"] == first
    with engine.begin() as db:
        db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
    with pytest.raises(DecisionDenied):
        respond(native, second, payload=payload)
    assert summary(native)["poll"]["tally"]["vote_count"] == 0


@pytest.mark.parametrize("visibility", ["before", "after_vote", "after_close"])
def test_tally_privacy_own_ballot_and_other_named_ballots(native, monkeypatch, visibility):
    case = create_poll(native, tally_visibility=visibility)
    first = vote(native, case)
    own = first["poll"]
    assert own["my_response"]["choice"] == "vote"
    assert own["tally_visible"] == (visibility != "after_close")
    if visibility == "after_close":
        assert own["tally"] is own["responses"] is None
    other = summary(native, 2)["poll"]
    assert other["my_response"] is None
    assert other["tally_visible"] == (visibility == "before")
    if visibility != "before":
        assert other["tally"] is other["responses"] is None
    second = vote(native, first, 2, choice="abstain")
    assert second["poll"]["tally_visible"] == (visibility != "after_close")
    closed = close(native, second, monkeypatch)
    assert closed["state"] == "passed" and closed["poll"]["phase"] == "closed"
    assert closed["poll"]["tally_visible"] == (visibility != "after_vote")
    assert closed["poll"]["winner_option_uid"] == (
        case["poll"]["options"][0]["uid"] if visibility != "after_vote" else None
    )
    assert closed["allowed_actions"] == []
    with pytest.raises(DecisionConflict):
        vote(native, second, 3)
    with native[0].connect() as db:
        assert db.execute(select(m.DecisionCase.state)).scalar_one() == "pending"  # Closure is computed, not scheduled.


@pytest.mark.parametrize(
    "settings,choices,state,reason",
    [
        ({}, [("vote", 0), ("vote", 1)], "needs_info", "tie"),
        ({"tie_policy": "rejected"}, [("vote", 0), ("vote", 1)], "rejected", "tie"),
        ({"participation_quorum": 2}, [("vote", 0), ("abstain", 0)], "expired", "no_quorum"),
        ({"participation_quorum": 2, "no_quorum_policy": "rejected"}, [("vote", 0)], "rejected", "no_quorum"),
        (
            {"participation_quorum": 2, "participation_policy": "all_responses"},
            [("vote", 0), ("abstain", 0)],
            "passed",
            "winner",
        ),
        ({"participation_policy": "all_responses"}, [("abstain", 0)], "needs_info", "no_valid_votes"),
        (
            {"participation_policy": "all_responses", "no_winner_policy": "rejected"},
            [("needs_info", 0)],
            "rejected",
            "no_valid_votes",
        ),
        ({"needs_info_policy": "block_result"}, [("vote", 0), ("needs_info", 0)], "needs_info", "needs_info_response"),
        ({"needs_info_policy": "record"}, [("vote", 0), ("needs_info", 0)], "passed", "winner"),
        (
            {
                "winner_policy": "majority_of_votes",
                "options": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}, {"key": "c", "label": "C"}],
            },
            [("vote", 0), ("vote", 0), ("vote", 1), ("vote", 2)],
            "needs_info",
            "no_majority",
        ),
        (
            {
                "winner_policy": "plurality",
                "options": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}, {"key": "c", "label": "C"}],
            },
            [("vote", 0), ("vote", 0), ("vote", 1), ("vote", 2)],
            "passed",
            "winner",
        ),
    ],
)
def test_explicit_quorum_winner_and_nonapproval_rules(native, monkeypatch, settings, choices, state, reason):
    case = create_poll(native, **settings)
    for actor, (choice, option) in enumerate(choices, 1):
        case = vote(native, case, actor, option, choice=choice)
        assert case["state"] in ("pending", "needs_info")
    result = close(native, case, monkeypatch)
    assert (result["state"], result["poll"]["outcome_reason"]) == (state, reason)
    assert result["poll"]["tally"]["vote_count"] == sum(choice == "vote" for choice, _ in choices)


def test_no_change_foreign_option_fences_and_cancelled_target(native):
    engine, _, _, _, _, _ = native
    foreign = create_poll(native)
    case = create_poll(native, allow_change=False)
    with pytest.raises(ValueError, match="belong"):
        respond(native, case, payload=form(case, choice="vote", option_uid=foreign["poll"]["options"][0]["uid"]))
    with pytest.raises(ValueError):
        respond(native, case, payload=form(case, choice="approve"))
    for changes in (
        {"round_id": str(uuid4())},
        {"expected_revision": 7},
        {"target_revision": "old"},
        {"policy_revision": "old"},
    ):
        with pytest.raises(DecisionConflict):
            vote(native, case, **changes)
    first = vote(native, case)
    assert not first["poll"]["can_change"] and first["allowed_actions"] == []
    with pytest.raises(DecisionConflict):
        vote(native, first)
    with engine.begin() as db:
        db.execute(update(m.Card).values(title="Changed"))
    assert summary(native)["state"] == "cancelled"
    with pytest.raises(DecisionConflict):
        vote(native, first, 2)


def test_poll_concurrent_uuid_and_expected_revision_serialization(native):
    case = create_poll(native)
    payload = form(case, choice="vote", option_uid=case["poll"]["options"][0]["uid"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: respond(native, case, payload=payload), range(2)))
    assert sorted(row["replayed"] for row in results) == [False, True]
    current = summary(native)

    def submit(actor):
        try:
            return vote(native, current, actor)
        except DecisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(submit, [2, 3]))
    assert sum(item is not None for item in raced) == 1
    result = summary(native)
    assert result["revision"] == 2 and result["poll"]["tally"]["vote_count"] == 2


def test_poll_invalid_configuration_rejected(native):
    for settings in (
        {"participation_quorum": 5},
        {"participation_quorum": True},
        {"allow_change": 1},
        {"tally_visibility": "before_vote"},
        {"options": [{"key": "a", "label": "Same"}, {"key": "b", "label": "Same"}]},
        {"options": [{"key": "a", "label": " "}, {"key": "b", "label": "B"}]},
    ):
        with pytest.raises((ValueError, ValidationError)):
            create_poll(native, **settings)


def test_poll_authenticated_http_privacy_and_current_actor(native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardDecisionApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env

    engine, _, users, board, foreign, card = native
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "poll-native-http-key-at-least-32-bytes"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    creation_payload = create_poll(native, as_payload=True, tally_visibility="after_close")
    case = None
    base = f"/board/{board.get_uid()}/card/{card.get_uid()}/decisions"
    with TestClient(app) as client:
        assert client.get(base).status_code == 401

        def login(index):
            access, refresh = AuthSecurity.authenticate(users[index].id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            return {"Authorization": f"Bearer {access}"}

        manager = login(0)
        listing = client.get(base, headers=manager)
        assert listing.status_code == 200 and listing.json()["items"] == []
        data = creation_payload.model_dump(mode="json")
        data["target_revision"] = listing.json()["target_revision"]
        created = client.post(base, headers=manager, json=data)
        assert created.status_code == 200, created.text
        case = created.json()
        assert client.post(base, headers=manager, json=data).json()["replayed"]
        assert client.post(base, headers=manager, json={**data, "question": "changed"}).status_code == 409
        url = f"/board/{board.get_uid()}/decisions/{case['uid']}/responses"
        body = form(case, choice="vote", option_uid=case["poll"]["options"][0]["uid"]).model_dump(mode="json")
        headers = login(1)
        assert client.post(url, headers=headers, json={**body, "actor_uid": users[2].get_uid()}).status_code == 400
        assert (
            client.post(url.replace(board.get_uid(), foreign.get_uid()), headers=headers, json=body).status_code == 404
        )
        assert client.post(url, headers=login(0), json=body).status_code == 403
        headers = login(1)
        result = client.post(url, headers=headers, json=body)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["poll"]["responses"] is result.json()["poll"]["tally"] is None
        assert client.post(url, headers=headers, json=body).json()["replayed"]
        with engine.begin() as db:
            db.execute(update(m.ProjectRole).where(m.ProjectRole.user_id == users[1].id).values(actions=[]))
        assert client.post(url, headers=headers, json=body).status_code == 403


def test_poll_delta_preserves_proposal_receipts_and_guards_used_history(native):
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text
    from test_decision_flow import create, upgrade_actor_delta

    engine, _, _, _, _, _ = native
    proposal = create(native)
    payload = form(proposal)
    approved = respond(native, proposal, payload=payload)
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024080000-4abc567def90.py"
    spec = importlib.util.spec_from_file_location("poll_delta_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "12ab34cde678"
    # Metadata uses autogenerated FK names. Delta downgrade must target its own
    # named constraints, so start from the actual baseline migration instead.
    for model in (m.DecisionResponse, m.DecisionOption, m.DecisionCase):
        model.__table__.drop(engine)
    baseline_spec = importlib.util.spec_from_file_location(
        "poll_baseline", path.with_name("20261024060000-b1640a24d001.py")
    )
    baseline = importlib.util.module_from_spec(baseline_spec)
    baseline_spec.loader.exec_module(baseline)
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            baseline.upgrade()
            migration.upgrade()
        upgrade_actor_delta(db)
    proposal = create(native)
    payload = form(proposal)
    approved = respond(native, proposal, payload=payload)
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
            assert "response_revision" not in {col["name"] for col in inspect(db).get_columns("decision_response")}
            assert db.execute(text("SELECT count(*) FROM decision_response")).scalar_one() == 1
            migration.upgrade()
    assert respond(native, proposal, payload=payload) == {**approved, "replayed": True}
    with engine.connect() as db:
        row = db.execute(select(m.DecisionResponse.__table__)).mappings().one()
        assert row["response_revision"] == 1 and row["option_id"] is None and row["receipt"] == approved
        for model in (m.DecisionOption, m.DecisionResponse):
            reflected = {
                (fk["constrained_columns"][0], fk["referred_table"], fk["options"].get("ondelete"))
                for fk in inspect(db).get_foreign_keys(model.__tablename__)
            }
            expected = {(fk.parent.name, fk.column.table.name, fk.ondelete) for fk in model.__table__.foreign_keys}
            assert reflected == expected
            assert {
                tuple(item["column_names"]) for item in inspect(db).get_unique_constraints(model.__tablename__)
            } == {
                tuple(col.name for col in constraint.columns)
                for constraint in model.__table__.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            }
    poll = vote(native, create_poll(native))
    vote(native, poll, option=1)
    with pytest.raises(RuntimeError, match="retained Poll"):
        with engine.begin() as db:
            with Operations.context(MigrationContext.configure(db)):
                migration.downgrade()


@pytest.mark.parametrize("needs_info_policy", ["record", "block_result"])
@pytest.mark.parametrize("visibility", ["after_vote", "after_close"])
def test_hidden_aggregate_reason_never_leaks_through_summary_receipt_or_replay(
    native, monkeypatch, needs_info_policy, visibility
):
    case = create_poll(
        native, participation_policy="all_responses", tally_visibility=visibility, needs_info_policy=needs_info_policy
    )
    payload = form(case, choice="needs_info")
    receipt = respond(native, case, payload=payload)
    if visibility == "after_close":
        assert receipt["poll"]["outcome_reason"] == "poll_open"
        assert receipt["poll"]["tally"] is receipt["poll"]["responses"] is None
    assert respond(native, receipt, payload=payload) == {**receipt, "replayed": True}
    assert summary(native)["poll"]["outcome_reason"] == "poll_open"
    closed = close(native, receipt, monkeypatch)
    if visibility == "after_close":
        assert closed["poll"]["outcome_reason"] == (
            "no_valid_votes" if needs_info_policy == "record" else "needs_info_response"
        )
    else:
        assert closed["poll"]["outcome_reason"] == "poll_closed"
        assert closed["poll"]["tally"] is closed["poll"]["responses"] is None
    assert respond(native, receipt, payload=payload) == {**receipt, "replayed": True}
