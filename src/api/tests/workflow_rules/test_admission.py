"""Source-transaction admission and recovery use real rows, independent of transport mocks."""
import importlib.util
from datetime import timedelta
from pathlib import Path
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.domain.models import CardComment, WorkflowActionRun, WorkflowRuleRun
from langboard_shared.domain.services.factory.WorkflowRuleService import CAUSE_RUN, admit_native_task, rows


_spec = importlib.util.spec_from_file_location("workflow_common_bot_fixtures", Path(__file__).parents[1] / "workflow_stages/test_bot_bindings.py")
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
bots = _fixtures.bots


def producer():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_seconds", [600, -60])
async def test_live_dispatch_does_not_consume_the_remaining_queue_slot(bots, lease_seconds):
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    f = bots
    running_id = _fixtures.reserve(f, identity="live-dispatch")
    queued_id = _fixtures.reserve(f, identity="waiting-dispatch")
    with DbSession.atomic() as db:
        running = rows(db, WorkflowRuleRun, id=running_id)[0]
        running.status = "running"
        db.update(running)
        claim = rows(db, WorkflowActionRun, rule_run_id=running_id)[0]
        claim.status, claim.attempt = "running", 1
        claim.started_at = f.now
        claim.lease_expires_at = f.now + timedelta(seconds=lease_seconds)
        db.update(claim)
    assert await run_due_rules(now=f.now + timedelta(seconds=1)) == (1 if lease_seconds > 0 else 2)
    with DbSession.use(readonly=False) as db:
        assert rows(db, WorkflowRuleRun, id=queued_id)[0].status == "accepted"
        retained = rows(db, WorkflowActionRun, rule_run_id=running_id)[0]
        assert retained.status == ("running" if lease_seconds > 0 else "uncertain")
        assert retained.attempt == 1
    assert f.execute.await_count == 1


@pytest.mark.parametrize("ineligible", ["private", "linked", "stale_residency"])
def test_interval_budget_reaches_eligible_card_after_rejected_candidate(bots, ineligible):
    from langboard_shared.tasks.WorkflowRuleTask import _admit_intervals

    f = bots
    _fixtures.configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    with DbSession.atomic() as db:
        f.card.workflow_stage_entered_at = f.now - timedelta(minutes=5)
        if ineligible == "private":
            f.card.visibility = "PRIVATE"
            f.card.owner_user_id = f.actor.id
            f.card.created_by_user_id = f.actor.id
        elif ineligible == "linked":
            f.card.source_type, f.card.source_uid = "wiki", "linked-source"
        else:
            f.card.workflow_stage_entered_key = "old"
        db.update(f.card)
        f.other_card.project_column_id = f.column.id
        f.other_card.workflow_stage_entered_key = f.stage.key
        f.other_card.workflow_stage_entered_at = f.now - timedelta(minutes=5)
        db.update(f.other_card)
    assert f.card.id < f.other_card.id
    assert _admit_intervals(f.now, 1) == 1
    with DbSession.use(readonly=False) as db:
        assert [run.card_id for run in rows(db, WorkflowRuleRun)] == [f.other_card.id]
        assert len(rows(db, WorkflowActionRun)) == 1
    assert _admit_intervals(f.now, 1) == 0


def test_interval_scan_pages_past_rejected_candidates_without_loading_all_cards(bots, monkeypatch):
    from langboard_shared.domain.models import Card
    from langboard_shared.tasks import WorkflowRuleTask as task
    from sqlalchemy import event

    f = bots
    _fixtures.configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    monkeypatch.setattr(task, "INTERVAL_SCAN_PAGE_SIZE", 2, raising=False)
    with DbSession.atomic() as db:
        f.card.visibility = "PRIVATE"
        f.card.owner_user_id = f.actor.id
        f.card.created_by_user_id = f.actor.id
        db.update(f.card)
        for index in range(5):
            db.insert(Card(project_id=f.project.id, project_column_id=f.column.id,
                title=f"Private candidate {index}", visibility="PRIVATE",
                owner_user_id=f.actor.id, created_by_user_id=f.actor.id,
                workflow_stage_entered_key=f.stage.key, workflow_stage_entered_at=f.now - timedelta(minutes=5)))
        eligible = Card(project_id=f.project.id, project_column_id=f.column.id, title="Eligible after prefix",
            workflow_stage_entered_key=f.stage.key, workflow_stage_entered_at=f.now - timedelta(minutes=5))
        db.insert(eligible)
    scans = []

    def inspect_scan(connection, cursor, statement, parameters, context, many):
        normalized = " ".join(statement.lower().split())
        if "from card join project_column" in normalized:
            scans.append(normalized)
            assert " limit " in normalized, "Interval candidates must not materialize the whole board"

    event.listen(f.engine, "before_cursor_execute", inspect_scan)
    try:
        assert task._admit_intervals(f.now, 1) == 1
        assert len(scans) >= 4
        with DbSession.use(readonly=False) as db:
            assert [run.card_id for run in rows(db, WorkflowRuleRun)] == [eligible.id]
        assert task._admit_intervals(f.now, 1) == 0
    finally:
        event.remove(f.engine, "before_cursor_execute", inspect_scan)


producer.__module__ = "langboard_shared.tasks.bots.CardCommentBotTask"
producer.__name__ = "card_comment_added"


def test_source_rollback_removes_durable_rule_and_action_reservations(bots):
    f = bots
    _fixtures.configure(f, trigger={"kind": "event", "event_type": "card_comment_added"})
    comment = CardComment(card_id=f.card.id, user_id=f.actor.id, content=EditorContentModel(content="Source text stays outside rule history"))
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            assert len(admit_native_task(producer, (f.actor, f.project, f.card, comment), {})) == 1
            raise RuntimeError("roll back original mutation")
    with DbSession.use(readonly=False) as db:
        assert rows(db, WorkflowRuleRun) == [] and rows(db, WorkflowActionRun) == []


def test_same_native_snapshot_has_stable_identity_and_no_source_text(bots):
    f = bots
    _fixtures.configure(f, trigger={"kind": "event", "event_type": "card_comment_added"})
    comment = CardComment(card_id=f.card.id, user_id=f.actor.id, content=EditorContentModel(content="Never copy this private body into event history"))
    first = admit_native_task(producer, (f.actor, f.project, f.card, comment), {})
    assert admit_native_task(producer, (f.actor, f.project, f.card, comment), {}) == first
    with DbSession.use(readonly=False) as db:
        run = rows(db, WorkflowRuleRun)[0]
        assert run.event_context["refs"]["CardComment"] == comment.get_uid()
        assert "private body" not in str(run.model_dump())
        assert len(rows(db, WorkflowActionRun)) == 1


def test_cause_reference_prevents_self_recursion(bots):
    f = bots
    _fixtures.configure(f, trigger={"kind": "event", "event_type": "card_comment_added"})
    comment = CardComment(card_id=f.card.id, user_id=f.actor.id, content=EditorContentModel(content="Message"))
    first = admit_native_task(producer, (f.actor, f.project, f.card, comment), {})
    token = CAUSE_RUN.set(first[0])
    try:
        assert admit_native_task(producer, (f.actor, f.project, f.card, comment), {}) == []
    finally:
        CAUSE_RUN.reset(token)


@pytest.mark.asyncio
async def test_expired_dispatch_is_uncertain_and_never_replayed(bots):
    f = bots
    run_id = _fixtures.reserve(f)
    with DbSession.atomic() as db:
        claim = rows(db, WorkflowActionRun)[0]
        claim.status, claim.attempt = "running", 1
        claim.started_at = f.now - timedelta(minutes=10)
        claim.lease_expires_at = f.now - timedelta(minutes=1)
        db.update(claim)
    await _fixtures.run_rule(run_id)
    f.execute.assert_not_awaited()
    with DbSession.use(readonly=False) as db:
        assert rows(db, WorkflowActionRun)[0].status == "uncertain"
        assert rows(db, WorkflowRuleRun)[0].status == "uncertain"


def test_admitted_configurer_and_rule_snapshot_are_immutable_after_edit(bots):
    from langboard_shared.domain.services.factory.WorkflowRuleService import replace_rules, stage_rules
    f = bots
    definition = stage_rules(f.stage.id)[0]
    with DbSession.atomic() as db:
        replace_rules(db, f.stage, [{**definition, "name": "Configured by actor"}], configured_by_id=f.actor.id)
    run_id = _fixtures.reserve(f)
    with DbSession.atomic() as db:
        original = rows(db, WorkflowRuleRun)[0]
        original_snapshot = original.rule_snapshot
        assert original.configured_by_id == f.actor.id
        changed = stage_rules(f.stage.id)[0]
        replace_rules(db, f.stage, [{**changed, "name": "Changed after admission"}], configured_by_id=f.bot.id)
    with DbSession.use(readonly=False) as db:
        admitted = rows(db, WorkflowRuleRun)[0]
        assert admitted.id == run_id and admitted.configured_by_id == f.actor.id
        assert admitted.rule_snapshot == original_snapshot
        assert admitted.rule_snapshot["name"] == "Configured by actor"


def test_distinct_reactions_have_distinct_stable_identity_without_comment_text(bots):
    f = bots
    _fixtures.configure(f, trigger={"kind": "event", "event_type": "card_comment_reacted"})
    def reaction_producer(actor, project, card, comment, reaction):
        pass
    reaction_producer.__module__ = "langboard_shared.tasks.bots.CardCommentBotTask"
    reaction_producer.__name__ = "card_comment_reacted"
    comment = CardComment(card_id=f.card.id, user_id=f.actor.id, content=EditorContentModel(content="Private text"))
    first = admit_native_task(reaction_producer, (f.actor, f.project, f.card, comment, "thumbsup"), {})
    second = admit_native_task(reaction_producer, (f.actor, f.project, f.card, comment, "heart"), {})
    assert first and second and first != second
    assert admit_native_task(reaction_producer, (f.actor, f.project, f.card, comment, "heart"), {}) == second
    with DbSession.use(readonly=False) as db:
        runs = rows(db, WorkflowRuleRun)
        assert {run.event_context["metadata"]["reaction_type"] for run in runs} == {"thumbsup", "heart"}
        assert all("Private text" not in str(run.model_dump()) for run in runs)


@pytest.mark.asyncio
async def test_accepted_interval_dispatch_allows_next_slot_without_replaying_same_slot(bots):
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    f = bots
    _fixtures.configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    for seconds in (120, 240, 360):
        due = f.now + timedelta(seconds=seconds)
        assert await run_due_rules(now=due) == 1
        assert await run_due_rules(now=due) == 0
    assert f.execute.await_count == 3
    with DbSession.use(readonly=False) as db:
        runs, claims = rows(db, WorkflowRuleRun), rows(db, WorkflowActionRun)
        assert len(runs) == len(claims) == 3
        assert {run.status for run in runs} == {"accepted"}
        assert {claim.status for claim in claims} == {"accepted"}
        assert len({claim.idempotency_key for claim in claims}) == 3
        assert all(claim.finished_at and claim.lease_expires_at is None and claim.attempt == 1 for claim in claims)


@pytest.mark.parametrize("status", ["queued", "running", "uncertain"])
def test_interval_dispatch_still_blocks_outstanding_local_or_ambiguous_work(bots, status):
    from langboard_shared.tasks.WorkflowRuleTask import _admit_intervals

    f = bots
    _fixtures.configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    assert _admit_intervals(f.now + timedelta(seconds=120), 5) == 1
    with DbSession.atomic() as db:
        run = rows(db, WorkflowRuleRun)[0]
        run.status = status
        db.update(run)
    assert _admit_intervals(f.now + timedelta(seconds=240), 5) == 0
    with DbSession.use(readonly=False) as db:
        assert len(rows(db, WorkflowRuleRun)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["revision", "residency", "schedule_stopped"])
async def test_next_interval_after_acceptance_rechecks_current_context(bots, boundary):
    from langboard_shared.domain.models.BotSchedule import BotScheduleStatus
    from langboard_shared.domain.services.factory.WorkflowRuleService import replace_rules, stage_rules, utc
    from langboard_shared.tasks.WorkflowRuleTask import run_due_rules

    f = bots
    _fixtures.configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
    assert await run_due_rules(now=f.now + timedelta(seconds=120)) == 1
    with DbSession.atomic() as db:
        first = rows(db, WorkflowRuleRun)[0]
        original_revision, original_residency = first.rule_revision, first.residency
        if boundary == "revision":
            definition = stage_rules(f.stage.id)[0]
            replace_rules(db, f.stage, [{**definition, "name": "Revised interval"}])
        elif boundary == "residency":
            f.card.workflow_stage_entered_at = f.now + timedelta(seconds=120)
            db.update(f.card)
        else:
            f.schedule.status = BotScheduleStatus.Stopped
            db.update(f.schedule)
    # A settings edit cannot replay the already accepted occurrence.
    assert await run_due_rules(now=f.now + timedelta(seconds=120)) == 0
    assert await run_due_rules(now=f.now + timedelta(seconds=240)) == 1
    assert f.execute.await_count == (1 if boundary == "schedule_stopped" else 2)
    with DbSession.use(readonly=False) as db:
        runs = sorted(rows(db, WorkflowRuleRun), key=lambda run: run.id)
        assert runs[0].status == "accepted"
        assert runs[0].rule_revision == original_revision and runs[0].residency == original_residency
        assert runs[1].status == ("skipped" if boundary == "schedule_stopped" else "accepted")
        assert runs[1].rule_revision == original_revision + (boundary == "revision")
        assert runs[1].residency == (utc(f.card.workflow_stage_entered_at).isoformat() if boundary == "residency" else original_residency)


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [True, False])
@pytest.mark.parametrize("boundary", ["visibility", "conditions"])
async def test_dispatch_lease_survives_current_context_revocation(bots, expired, boundary):
    from langboard_shared.tasks.WorkflowRuleTask import _admit_intervals

    f = bots
    if boundary == "visibility":
        _fixtures.configure(f, trigger={"kind": "interval", "interval_seconds": 120}, schedule=True)
        assert _admit_intervals(f.now + timedelta(seconds=120), 5) == 1
    else:
        from langboard_shared.domain.services.factory.WorkflowRuleService import replace_rules
        definition = _fixtures.definition(f.bot.get_uid())
        definition["conditions"] = [{"kind": "actor_is_not_author", "operator": "eq", "value": True}]
        with DbSession.atomic() as db:
            replace_rules(db, f.stage, [definition])
        _fixtures.reserve(f)
    with DbSession.atomic() as db:
        run = rows(db, WorkflowRuleRun)[0]
        run_id = run.id
        run.status = "running"
        db.update(run)
        claim = rows(db, WorkflowActionRun)[0]
        claim.status, claim.attempt = "running", 1
        claim.started_at = f.now - timedelta(minutes=10)
        claim.lease_expires_at = f.now + timedelta(minutes=-1 if expired else 10)
        db.update(claim)
        if boundary == "visibility":
            f.card.visibility = "PRIVATE"
            f.card.owner_user_id = f.actor.id
        f.card.created_by_user_id = f.actor.id
        db.update(f.card)

    await _fixtures.run_rule(run_id, now=f.now + timedelta(seconds=120))
    f.execute.assert_not_awaited()
    with DbSession.atomic() as db:
        claim = rows(db, WorkflowActionRun)[0]
        assert claim.status == ("uncertain" if expired else "running")
        assert claim.attempt == 1
        if expired:
            assert claim.error_code == "lease_expired"
            assert claim.finished_at is not None and claim.lease_expires_at is None
            assert claim.attempts[-1]["status"] == "uncertain"
        else:
            assert claim.error_code is None and claim.finished_at is None
            assert claim.lease_expires_at is not None and claim.attempts == []
        assert rows(db, WorkflowRuleRun)[0].status == ("uncertain" if expired else "running")
        f.card.visibility = "INTERNAL"
        f.card.owner_user_id = None
        db.update(f.card)

    # Restoring access does not make the unresolved transport safe to repeat.
    if boundary == "visibility":
        assert _admit_intervals(f.now + timedelta(seconds=240), 5) == 0
    await _fixtures.run_rule(run_id, now=f.now + timedelta(seconds=240))
    f.execute.assert_not_awaited()
