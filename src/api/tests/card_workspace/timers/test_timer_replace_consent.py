"""MCP replacement consent is enforced inside the real timer transition lock."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import pytest
from langboard.mcp_tools import CardMcp
from langboard_shared.domain.models import Card, Checkitem, CheckitemTimerRecord, Checklist
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.helpers import InfraHelper
from langboard_shared.publishers import CheckitemPublisher
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from test_timer_transitions import transition as transition_fixture


transition = transition_fixture


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("action", ["start", "resume"])
@pytest.mark.parametrize("replace_active", [False, True])
def test_concurrent_mcp_starts_preserve_replacement_consent(
    transition, timer_engine, monkeypatch, action, replace_active, canonical
):
    if timer_engine.dialect.name != "postgresql":
        pytest.skip("Replacement-consent concurrency requires PostgreSQL")
    service, actor, project, card, item, now = transition
    initial_status = CheckitemStatus.Paused if action == "resume" else CheckitemStatus.Stopped
    with Session(timer_engine, expire_on_commit=False) as db:
        db.execute(update(Checkitem).where(Checkitem.id == 1).values(status=initial_status))
        db.execute(update(CheckitemTimerRecord).where(CheckitemTimerRecord.id == 100).values(status=initial_status))
        other_card = Card(id=2001, project_id=10, project_column_id=40, title="Other card")
        db.add(other_card)
        db.commit()
        db.add(Checklist(id=3001, card_id=2001, title="Other work"))
        db.commit()
        other_item = Checkitem(id=2, checklist_id=3001, title="Other timer", user_id=2, status=initial_status)
        db.add(other_item)
        db.commit()
    # Keep authorization/ancestry reads, repository writes and the actual lock;
    # only the unrelated card visibility resolver is supplied by this fixture.
    monkeypatch.setattr(CardMcp, "_require_task_card", lambda p, c, *_: (project, InfraHelper.get_by_id_like(Card, c)))
    monkeypatch.setattr(service, "_work_visibility_contexts", lambda _: None)
    facade_service = SimpleNamespace(
        checkitem=service,
        checklist=SimpleNamespace(get_by_id_like=lambda uid: InfraHelper.get_by_id_like(Checklist, uid)),
    )
    ready = Barrier(2)
    change_status = service.change_status

    def overlap_before_lock(*args, **kwargs):
        if args[4] == CheckitemStatus.Started:
            # Both facade prechecks finish before either request takes the lock.
            ready.wait(timeout=10)
        return change_status(*args, **kwargs)

    monkeypatch.setattr(service, "change_status", overlap_before_lock)

    def request(target_card, target_item):
        try:
            if canonical:
                from langboard.mcp_tools.WorkActionsMcp import TimerChange, work

                return work(
                    project.get_uid(),
                    target_card.get_uid(),
                    TimerChange(action=action, checkitem_uid=target_item.get_uid(), replace_active=replace_active),
                    actor,
                    facade_service,
                ).model_dump(mode="json")
            return CardMcp.change_card_checkitem_work(
                project.get_uid(),
                target_card.get_uid(),
                target_item.get_uid(),
                action,
                actor,
                facade_service,
                replace_active=replace_active,
            )
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request, card, item)
        second = pool.submit(request, other_card, other_item)
        results = [first.result(timeout=15), second.result(timeout=15)]
    successes = [result for result in results if isinstance(result, dict)]
    assert len(successes) == (2 if replace_active else 1), results
    if not replace_active:
        assert "Another work timer is active; set replace_active to pause it" in results
    with Session(timer_engine) as db:
        statuses = [db.get(Checkitem, uid).status for uid in (1, 2)]
        assert statuses.count(CheckitemStatus.Started) == 1
        assert statuses.count(CheckitemStatus.Paused if replace_active else initial_status) == 1
        assert len(db.scalars(select(CheckitemTimerRecord)).all()) == (4 if replace_active else 2)
        assert db.get(Checkitem, 1).accumulated_seconds == 7
        assert db.get(Checkitem, 2).accumulated_seconds == 0
    assert CheckitemPublisher.status_changed.call_count == (3 if replace_active else 1)
    assert service._mark_card_changed_for_unread.call_count == (3 if replace_active else 1)


def test_explicit_false_keeps_same_timer_start_idempotent(transition, timer_engine):
    service, actor, project, card, item, now = transition
    assert service.change_status(actor, project, card, item, CheckitemStatus.Started, now, replace_active=False)
    with Session(timer_engine) as db:
        assert db.get(Checkitem, 1).status == CheckitemStatus.Started
        assert db.get(Checkitem, 1).accumulated_seconds == 7
        assert len(db.scalars(select(CheckitemTimerRecord)).all()) == 1
    CheckitemPublisher.status_changed.assert_not_called()
    service._mark_card_changed_for_unread.assert_not_called()
