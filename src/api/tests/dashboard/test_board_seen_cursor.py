# ruff: noqa: F811
"""Out-of-order board snapshots cannot rewind read state or fabricate visits."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, ProjectAssignedUser
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.infrastructure.repositories.factory.ProjectAssignedUserRepository import (
    ProjectAssignedUserRepository,
)
from sqlalchemy import event


@pytest.mark.parametrize("current_seq", [0, 50, 100])
def test_stale_snapshot_cursor_is_monotonic_and_preserves_visit_fields(board, monkeypatch, current_seq):
    _, user, project, member, *_ = board
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    previous = SafeDateTime.now() - timedelta(days=2)
    with DbSession.atomic() as db:
        db.exec(SqlBuilder.update.table(ProjectAssignedUser).where(ProjectAssignedUser.id == member.id).values(
            board_seen_seq=current_seq, last_viewed_at=previous, updated_at=previous, view_count=7,
            starred=True, card_unread_baseline_seq=11,
        ))
        foreign_project = ProjectAssignedUser(project_id=11, user_id=user.id)
        foreign_user = ProjectAssignedUser(project_id=project.id, user_id=2)
        db.insert(foreign_project)
        db.insert(foreign_user)
    with DbSession.use(readonly=False) as db:
        saved = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.id == member.id)).first()
        previous, previous_updated = saved.last_viewed_at, saved.updated_at
    stale = member.model_copy(update={"board_seen_seq": 0})
    membership = ProjectAssignedUserRepository(None, None)
    monkeypatch.setattr(membership, "get_by_user_and_project", lambda *args: stale)
    card = Card(id=99, project_id=project.id, project_column_id=40, title="Snapshot", last_change_seq=50)
    repository = SimpleNamespace(
        card=SimpleNamespace(get_board_list=lambda *a, **kw: [(card, 0)], get_board_creators=lambda *a: {}),
        card_assigned_user=SimpleNamespace(get_all_by_project=lambda *a: []),
        card_relationship=SimpleNamespace(get_all_by_project=lambda *a: []),
        project_label=SimpleNamespace(get_all_card_labels_by_project=lambda *a: []),
        checklist=SimpleNamespace(get_all_by_project=lambda *a, **kw: []),
        project_assigned_user=membership,
        user_card_read_state=SimpleNamespace(get_seen_seq_map=lambda *a: {}),
    )
    service = CardService(None, None, repository)
    service.resolve_visibility_context = Mock(return_value=(project, object()))
    service.get_active_workers = Mock(return_value={})
    service.get_work_states = Mock(return_value={card.id: {"checklist_progress": {"total": 0, "completed": 0}}})

    affected = []

    def capture(_conn, cursor, statement, *_args):
        if statement.startswith("UPDATE project_assigned_user"):
            affected.append(cursor.rowcount)

    engine = DbEngine.get_main_engine()
    event.listen(engine, "after_cursor_execute", capture)
    try:
        snapshot = service.get_board_list(project, user)
    finally:
        event.remove(engine, "after_cursor_execute", capture)
    assert affected == [int(current_seq < 50)]
    assert snapshot[0]["has_unread_change"] is True
    with DbSession.use(readonly=False) as db:
        rows = {row.id: row for row in db.exec(SqlBuilder.select.table(ProjectAssignedUser)).all()}
    saved = rows[member.id]
    assert saved.board_seen_seq == max(current_seq, 50)
    assert saved.last_viewed_at == previous
    assert saved.updated_at == previous_updated
    assert saved.view_count == 7 and saved.starred is True and saved.card_unread_baseline_seq == 11
    assert rows[foreign_project.id].board_seen_seq == rows[foreign_user.id].board_seen_seq == 0

    # A real project visit retains its existing recency/frequency behavior.
    membership.set_last_view(user, project)
    with DbSession.use(readonly=False) as db:
        saved = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.id == member.id)).first()
    assert saved.last_viewed_at > previous and saved.view_count == 8
    assert saved.board_seen_seq == max(current_seq, 50)


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
def test_parallel_cursor_advances_and_removed_membership(board):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, user, project, member, *_ = board
    repository = ProjectAssignedUserRepository(None, None)
    barrier = Barrier(2)

    def advance(seq):
        barrier.wait(timeout=5)
        repository.advance_board_seen(user, project, seq)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(advance, seq) for seq in (50, 100)]
        for future in futures:
            future.result(timeout=10)
    with DbSession.atomic() as db:
        saved = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.id == member.id)).first()
        assert saved.board_seen_seq == 100
        db.delete(saved)
    repository.advance_board_seen(user, project, 200)
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(
            ProjectAssignedUser.user_id == user.id, ProjectAssignedUser.project_id == project.id,
        )).first() is None
