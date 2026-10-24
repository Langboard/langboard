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
