# ruff: noqa: F811
"""Persisted native MCP boundaries and preservation under real row contention."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
import pytest
from fastmcp.exceptions import ToolError
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import FileModel
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    CardComment,
    CardRelationship,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.publishers import ProjectPublisher
from sqlalchemy import event, select, text
from tests.card_visibility.conftest import current_card  # noqa: F401
from tests.mcp_integration.test_card_visibility_actions import (
    invoke,
    persisted,
    visibility_card,  # noqa: F401 - shared pytest fixture
)


DEPENDENTS = (CardComment, Checklist, Checkitem, GlobalCardRelationshipType, CardRelationship, CardAttachment)


@pytest.fixture
def populated_card(visibility_card):
    b = visibility_card
    engine = DbEngine.get_main_engine()
    for model in DEPENDENTS:
        model.__table__.create(engine)
    with DbSession.use(readonly=False) as db:
        b.card.description = EditorContentModel(content="Existing description with evidence")
        db.update(b.card)
        db.insert(CardComment(card_id=b.card.id, user_id=b.user.id,
                              content=EditorContentModel(content="Keep this comment")))
        checklist = Checklist(card_id=b.card.id, title="Existing checklist", order=3)
        db.insert(checklist)
        db.insert(Checkitem(checklist_id=checklist.id, user_id=b.user.id,
                            title="Completed evidence", is_checked=True, accumulated_seconds=42))
        related = Card(project_id=b.project.id, title="Related card", visibility="INTERNAL")
        db.insert(related)
        relationship_type = GlobalCardRelationshipType(parent_name="Blocks", child_name="Blocked by")
        db.insert(relationship_type)
        db.insert(CardRelationship(relationship_type_id=relationship_type.id,
                                   card_id_parent=b.card.id, card_id_child=related.id))
        db.insert(CardAttachment(card_id=b.card.id, user_id=b.user.id, filename="evidence.pdf",
                                 content_sha256="a" * 64, document_text="Indexed evidence",
                                 file=FileModel(storage_type="test", storage_name="fixture",
                                                filename="immutable.pdf", original_filename="evidence.pdf",
                                                path="/fixture/immutable.pdf")))
    return b


def snapshot():
    """Compare all stored columns, including identities and deletion markers."""
    with DbEngine.get_main_engine().connect() as connection:
        return {
            model.__tablename__: [dict(row) for row in connection.execute(
                select(model.__table__).order_by(model.id)
            ).mappings()]
            for model in (Card, *DEPENDENTS)
        }


@pytest.mark.parametrize("current_card", ["postgresql-test"], indirect=True)
def test_mcp_reduction_preserves_every_card_and_dependent_column(populated_card):
    b = populated_card
    before = snapshot()
    assert invoke(b)["visibility"] == "INTERNAL"
    after = snapshot()
    expected = before[Card.__tablename__]
    changed = next(row for row in expected if row["id"] == b.card.id)
    changed["visibility"] = "INTERNAL"
    actual = next(row for row in after[Card.__tablename__] if row["id"] == b.card.id)
    assert actual["updated_at"] >= changed["updated_at"]
    changed["updated_at"] = actual["updated_at"]
    assert after == before
    assert len(persisted(b)[1]) == 1
    ProjectPublisher.updated.assert_called_once()


@pytest.mark.parametrize("denial", ["private", "revoked", "stale"])
@pytest.mark.parametrize("target", ["INTERNAL", "SHARED"])
def test_denied_native_transition_preserves_full_persisted_state(populated_card, denial, target):
    b = populated_card
    expected = "SHARED"
    with DbSession.use(readonly=False) as db:
        if denial == "private":
            b.card.visibility, b.card.owner_user_id = "PRIVATE", b.user.id
            db.update(b.card)
        elif denial == "revoked":
            role = db.exec(SqlBuilder.select.table(ProjectRole)).first()
            role.actions = ["read"]
            db.update(role)
        else:
            expected = "INTERNAL"
    before = snapshot()
    with pytest.raises(ToolError, match="^Card visibility transition unavailable; read current state and verify access$"):
        invoke(b, target, expected)
    assert snapshot() == before
    assert persisted(b)[1] == []
    ProjectPublisher.updated.assert_not_called()


@pytest.mark.parametrize("target", ["INTERNAL", "SHARED"])
def test_private_external_card_and_missing_card_have_identical_denial(populated_card, monkeypatch, target):
    b = populated_card
    with DbSession.use(readonly=False) as db:
        external = User(firstname="External", lastname="Reader", email="private-reader@example.invalid",
                        password="test-only", activated_at=b.user.activated_at)
        db.insert(external)
        db.insert(ProjectAssignedUser(project_id=b.project.id, user_id=external.id))
        b.card.visibility, b.card.owner_user_id = "PRIVATE", b.user.id
        db.update(b.card)
    monkeypatch.setattr(b.owner, "_resolve_internal_access", lambda _: False)
    b.user = external
    before = snapshot()
    assert b.owner.resolve_readable_card(b.project, b.card, external, CollaborationChannel.Mcp) is None
    messages = []
    original = b.card
    for card in (original, Card(id=original.id + 1_000_000, project_id=b.project.id, title="Absent")):
        b.card = card
        with pytest.raises(ToolError) as failure:
            invoke(b, target)
        messages.append(str(failure.value))
    b.card = original
    assert messages == ["Card visibility transition unavailable; read current state and verify access"] * 2
    assert snapshot() == before
    assert persisted(b)[1] == []
    ProjectPublisher.updated.assert_not_called()


@pytest.mark.parametrize("current_card", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("competing_change", ["content", "visibility", "revocation"])
def test_waiting_mcp_transition_revalidates_committed_concurrent_state(populated_card, competing_change):
    """Observe PostgreSQL lock waiting before committing an independent writer."""
    b = populated_card
    engine = DbEngine.get_main_engine()
    entered = Event()
    worker_pid = []
    before = snapshot()

    def observe_lock(connection, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            worker_pid.append(connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one())
            entered.set()

    event.listen(engine, "before_cursor_execute", observe_lock)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with engine.connect() as holder:
                transaction = holder.begin()
                holder.execute(select(Card.id).where(Card.id == b.card.id).with_for_update())
                # The observer also sees the holder; reset before starting the worker.
                worker_pid.clear()
                entered.clear()
                future = executor.submit(invoke, b)
                try:
                    assert entered.wait(5), "MCP worker did not attempt its independent lock"
                    deadline = monotonic() + 5
                    with engine.connect() as observer:
                        while monotonic() < deadline:
                            waiting = observer.execute(text(
                                "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"
                            ), {"pid": worker_pid[0]}).scalar_one()
                            observer.commit()
                            if waiting == "Lock":
                                break
                            sleep(0.02)
                    assert waiting == "Lock", "No database-observed concurrent lock contention"
                    assert not future.done()
                    if competing_change == "content":
                        holder.execute(Card.__table__.update().where(Card.id == b.card.id).values(
                            title="Concurrent committed title", description=EditorContentModel(content="Concurrent body")
                        ))
                    elif competing_change == "visibility":
                        holder.execute(Card.__table__.update().where(Card.id == b.card.id).values(visibility="INTERNAL"))
                    else:
                        holder.execute(ProjectRole.__table__.update().where(
                            ProjectRole.user_id == b.user.id, ProjectRole.project_id == b.project.id
                        ).values(actions=["read"]))
                    transaction.commit()
                finally:
                    if transaction.is_active:
                        transaction.rollback()
                if competing_change == "content":
                    assert future.result(timeout=5)["visibility"] == "INTERNAL"
                    with DbSession.use(readonly=False) as db:
                        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == b.card.id)).first()
                    assert current.title == "Concurrent committed title"
                    assert current.description.content == "Concurrent body"
                    assert len(persisted(b)[1]) == 1
                    ProjectPublisher.updated.assert_called_once()
                else:
                    with pytest.raises(ToolError, match="Card visibility transition unavailable"):
                        future.result(timeout=5)
                    assert persisted(b) == ("INTERNAL" if competing_change == "visibility" else "SHARED", [])
                    ProjectPublisher.updated.assert_not_called()
        after = snapshot()
        expected_card = next(row for row in before[Card.__tablename__] if row["id"] == b.card.id)
        actual_card = next(row for row in after[Card.__tablename__] if row["id"] == b.card.id)
        if competing_change == "content":
            expected_card.update(title="Concurrent committed title",
                                 description=EditorContentModel(content="Concurrent body"), visibility="INTERNAL")
        elif competing_change == "visibility":
            expected_card["visibility"] = "INTERNAL"
        # Only the independently committed content write / authorized reduction may update the timestamp.
        if competing_change != "revocation":
            assert actual_card["updated_at"] >= expected_card["updated_at"]
            expected_card["updated_at"] = actual_card["updated_at"]
        assert after == before
        if competing_change == "revocation":
            with DbSession.use(readonly=False) as db:
                role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.user_id == b.user.id, ProjectRole.project_id == b.project.id
                )).first()
            assert role.actions == ["read"]
    finally:
        event.remove(engine, "before_cursor_execute", observe_lock)
