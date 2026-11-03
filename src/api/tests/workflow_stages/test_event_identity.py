"""Context refreshes cannot turn one source mutation into multiple executions."""

from datetime import timedelta
import pytest
import test_bot_bindings as fixtures
from langboard_shared.core.db import DbSession
from langboard_shared.domain.constants.DomainEventType import DomainEventType
from langboard_shared.domain.models import WorkflowActionRun, WorkflowRuleRun
from langboard_shared.domain.services.factory.WorkflowRuleService import admit_native_task, rows


bots = fixtures.bots


def producer(actor, project, card):
    pass


producer.__module__ = "langboard_shared.tasks.bots.CardBotTask"
producer.__name__ = "card_updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("refreshed", ["actor", "project"])
async def test_source_replay_after_context_refresh_keeps_one_dispatch(bots, refreshed):
    f = bots
    fixtures.configure(f, trigger={"kind": "event", "event_type": "card_updated"})
    with DbSession.atomic() as db:
        f.hook.conditions = [DomainEventType.CardUpdated]
        db.update(f.hook)
    first = admit_native_task(producer, (f.actor, f.project, f.card), {})
    await fixtures.run_rule(first[0])
    # The user/profile or board may change between retries of the same mutation.
    # Neither change creates a new card event.
    getattr(f, refreshed).updated_at += timedelta(seconds=1)
    replay = admit_native_task(producer, (f.actor, f.project, f.card), {})
    for run_id in replay:
        await fixtures.run_rule(run_id)
    f.execute.assert_awaited_once()
    assert replay == first
    with DbSession.use(readonly=False) as db:
        assert len(rows(db, WorkflowRuleRun)) == len(rows(db, WorkflowActionRun)) == 1

    # A later source mutation still receives its own reservation and dispatch.
    f.card.last_change_seq += 1
    second = admit_native_task(producer, (f.actor, f.project, f.card), {})
    assert second and second != first
    await fixtures.run_rule(second[0])
    assert f.execute.await_count == 2
