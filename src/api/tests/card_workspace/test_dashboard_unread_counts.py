"""Dashboard cardinality retains the board cursor and existing dot audience."""
from types import SimpleNamespace
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, Project, ProjectAssignedUser, ProjectColumn
from langboard_shared.domain.services.factory.ProjectService import ProjectService
from sqlalchemy import event, update
from test_card_change_counts import counts as counts_fixture


counts = counts_fixture


def test_native_grouped_count_preserves_dot_boundaries_and_board_read_independence(counts, monkeypatch):
    f = counts
    with DbSession.atomic() as db:
        # A source mirror and archived/deleted-column cards already qualify for the board dot.
        deleted_column = ProjectColumn(project_id=f.project.id, name="Deleted", deleted_at=SafeDateTime.now())
        db.insert(deleted_column)
        for fields in (
            {"title": "one changed card", "last_change_seq": 900},
            {"title": "archived", "last_change_seq": 12, "archived_at": SafeDateTime.now()},
            {"title": "mirror", "last_change_seq": 13, "source_type": "project_card", "source_uid": f.card.get_uid()},
            {"title": "deleted column", "last_change_seq": 14, "project_column_id": deleted_column.id},
            {"title": "seen", "last_change_seq": 10},
            {"title": "deleted", "last_change_seq": 999, "deleted_at": SafeDateTime.now()},
            {"title": "private other", "last_change_seq": 999, "visibility": "PRIVATE", "owner_user_id": f.other.id},
            {"title": "internal", "last_change_seq": 999, "visibility": "INTERNAL"},
            {"title": "private mine", "last_change_seq": 15, "visibility": "PRIVATE", "owner_user_id": f.user.id},
        ):
            fields.setdefault("created_by_user_id", fields.get("owner_user_id", f.user.id))
            row = Card(project_id=f.project.id, project_column_id=fields.pop("project_column_id", f.column.id),
                       visibility=fields.pop("visibility", "SHARED"), **fields)
            db.insert(row)
        second = Project(owner_id=f.other.id, title="Second")
        db.insert(second)
        second_member = ProjectAssignedUser(project_id=second.id, user_id=f.user.id, board_seen_seq=0)
        db.insert(second_member)
        second_card = Card(project_id=second.id, project_column_id=f.column.id, title="Second card", visibility="SHARED", last_change_seq=2)
        db.insert(second_card)
    with f.engine.begin() as db:
        db.execute(update(ProjectAssignedUser).where(ProjectAssignedUser.id == f.member.id).values(board_seen_seq=10))
    ids = [f.project.id, second.id]
    contexts = {int(uid): f.context for uid in ids}
    sql = []
    def capture(_c, _cu, statement, *_):
        sql.append(statement)
    event.listen(f.engine, "before_cursor_execute", capture)
    try:
        result = f.repo.project.get_unread_card_counts(f.user, ids, contexts=contexts)
    finally:
        event.remove(f.engine, "before_cursor_execute", capture)
    assert result == {int(f.project.id): 5, int(second.id): 1}
    prior_dot = f.repo.project.get_max_card_change_seq(ids, contexts=contexts)
    assert {uid: seq > (10 if uid == int(f.project.id) else 0) for uid, seq in prior_dot.items()} == {
        uid: count > 0 for uid, count in result.items()
    }
    assert len(sql) == 1 and "count(distinct(" in sql[0].lower()
    assert "card_change_receipt" not in sql[0] and "user_card_read_state" not in sql[0]
    assert f.repo.project.get_unread_card_counts(f.user, [], contexts=contexts) == {}
    assert f.repo.project.get_unread_card_counts(f.other, ids, contexts=contexts) == {}
    assert f.repo.project.get_unread_card_counts(f.user, ids, contexts={}) == {}
    # Individual card reading never changes board cardinality.
    f.repo.user_card_read_state.set_read_state(f.user, second_card, True)
    assert f.repo.project.get_unread_card_counts(f.user, ids, contexts=contexts) == result
    f.repo.user_card_read_state.set_read_state(f.user, second_card, False)
    assert f.repo.project.get_unread_card_counts(f.user, ids, contexts=contexts) == result
    # Count serialization and legacy boolean travel through the real service entry point.
    f.member.board_seen_seq = 10
    raw = [(f.project, f.member, None, False, None), (second, second_member, None, False, None)]
    monkeypatch.setattr(f.repo.project, "get_all_by_user", lambda *_, **__: raw)
    monkeypatch.setattr(f.repo.role.project, "get_list", lambda **_: [SimpleNamespace(project_id=uid, actions=[]) for uid in ids])
    service = f.get(ProjectService)
    projects, _ = service.get_api_list(f.user, contexts=contexts)
    assert [(p["board_unread_card_count"], p["board_has_unread_change"]) for p in projects] == [(5, True), (1, True)]
    # Current board cursor and membership are reread, even when model objects are retained.
    with f.engine.begin() as db:
        db.execute(update(ProjectAssignedUser).where(ProjectAssignedUser.id == f.member.id).values(board_seen_seq=1000))
        db.execute(ProjectAssignedUser.__table__.delete().where(ProjectAssignedUser.id == second_member.id))
    assert f.repo.project.get_unread_card_counts(f.user, ids, contexts=contexts) == {}
    projects, _ = service.get_api_list(f.user, contexts=contexts)
    assert all(p["board_unread_card_count"] == 0 and not p["board_has_unread_change"] for p in projects)
