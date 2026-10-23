"""Native PostgreSQL edit/preview interleaving, including the FK parent lock."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.card_workspace.application.work_plan import WorkPlan, WorkPlanService
from langboard.card_workspace.domain import WorkPlanConflict
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import (
    Card,
    CardChangeReceipt,
    CardMetadata,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    EmployeeMembershipPolicy,
    ProjectRole,
    ScimGroup,
    ScimGroupMember,
    UserIdentityLink,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CardService import CardService
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from src.api.tests.card_workspace.test_card_change_counts import counts


@pytest.fixture
def native_edit_board(monkeypatch):
    url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL") or os.getenv("LANGBOARD_COUNT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an explicitly disposable PostgreSQL database")
    monkeypatch.setenv("LANGBOARD_COUNT_TEST_DATABASE_URL", url)
    fixture = counts.__wrapped__(SimpleNamespace(param="postgresql"), monkeypatch)
    try:
        f = next(fixture)
        for model in (CheckitemTimerRecord, EmployeeMembershipPolicy, ScimGroup, ScimGroupMember, UserIdentityLink):
            model.__table__.create(f.engine, checkfirst=True)
        with DbSession.atomic() as db:
            db.insert(ProjectRole(project_id=f.project.id, user_id=f.user.id, actions=["read", "card_update"]))
        module = import_module(CardService.__module__)
        # Preserve native persistence and receipts; delivery is outside this DB regression.
        monkeypatch.setattr(module.CardPublisher, "updated", Mock())
        monkeypatch.setattr(module.CardActivityTask, "card_updated", Mock())
        monkeypatch.setattr(module.CardBotTask, "card_updated", Mock())
        for name in ("ChecklistService", "CheckitemService"):
            cls = getattr(import_module(f"langboard_shared.domain.services.factory.{name}"), name)
            monkeypatch.setattr(cls, "dispatch_created", Mock())
        metadata = import_module("langboard.card_workspace.application.work_plan").MetadataPublisher
        monkeypatch.setattr(metadata, "updated_metadata", Mock())
        yield f
    finally:
        fixture.close()


@pytest.mark.parametrize("operation", ["preview", "apply"])
def test_native_edit_finishes_while_work_plan_holds_project_lock(native_edit_board, monkeypatch, operation):
    f = native_edit_board
    plan = WorkPlan(
        project_uid=f.project.get_uid(),
        anchor_card_uid=f.card.get_uid(),
        new_checklists=[{"target_card_ref": f.card.get_uid(), "title": "Proposed", "items": ["Verify"]}],
    )
    with closing(DomainService()) as service:
        reviewed = WorkPlanService(f.user, service).preview(plan)["revision"]
    card_locked, project_locked = threading.Event(), threading.Event()
    worker = threading.local()
    original_mark = CardService.mark_card_changed

    def mark_changed(service, card, *args, **kwargs):
        if getattr(worker, "role", None) == "editor":
            # CardService has already persisted the title in its open transaction.
            card_locked.set()
            assert project_locked.wait(5), "Work Plan never acquired the project lock"
        return original_mark(service, card, *args, **kwargs)

    def after_sql(_connection, _cursor, statement, _parameters, _context, _many):
        if getattr(worker, "role", None) == "plan" and "FROM project" in statement and "FOR " in statement:
            project_locked.set()

    monkeypatch.setattr(CardService, "mark_card_changed", mark_changed)
    event.listen(f.engine, "after_cursor_execute", after_sql)

    def edit():
        worker.role = "editor"
        with closing(DomainService()) as service:
            return service.card.update(
                f.user, f.project.get_uid(), f.card.get_uid(), {"title": "Native concurrent edit"}
            )

    def execute_plan():
        worker.role = "plan"
        with closing(DomainService()) as service:
            owner = WorkPlanService(f.user, service)
            if operation == "apply":
                with pytest.raises(WorkPlanConflict):
                    owner.apply(plan, reviewed, "concurrent-edit")
                return None
            return owner.preview(plan)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            editor = pool.submit(edit)
            assert card_locked.wait(5), "Native edit never acquired the card lock"
            preview = pool.submit(execute_plan)
            assert editor.result(timeout=15)["title"] == "Native concurrent edit"
            result = preview.result(timeout=15)
    finally:
        event.remove(f.engine, "after_cursor_execute", after_sql)
    with f.engine.connect() as db:
        assert db.execute(select(Card.title).where(Card.id == f.card.id)).scalar_one() == "Native concurrent edit"
        assert db.execute(select(func.count()).select_from(CardChangeReceipt)).scalar_one() == 1
        for model in (CardMetadata, Checklist, Checkitem):
            assert db.execute(select(func.count()).select_from(model)).scalar_one() == 0
    with closing(DomainService()) as service:
        current = WorkPlanService(f.user, service).preview(plan)["revision"]
    assert current != reviewed
    if result:
        assert result["revision"] == current, "Preview must reread the committed edit after its lock wait"


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_project_mutations_still_wait_for_work_plan(native_edit_board, mutation):
    f = native_edit_board
    locked, release = threading.Event(), threading.Event()

    def after_sql(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM project" in statement and "FOR " in statement:
            locked.set()
            assert release.wait(5), "Project mutation did not finish its lock probe"

    def preview():
        with closing(DomainService()) as service:
            return WorkPlanService(f.user, service).preview(
                WorkPlan(
                    project_uid=f.project.get_uid(),
                    anchor_card_uid=f.card.get_uid(),
                    new_checklists=[{"target_card_ref": f.card.get_uid(), "title": "Proposed", "items": ["Verify"]}],
                )
            )

    event.listen(f.engine, "after_cursor_execute", after_sql)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(preview)
            try:
                assert locked.wait(5), "Work Plan never acquired the project lock"
                with pytest.raises(OperationalError) as rejected:
                    with f.engine.begin() as db:
                        db.execute(text("SET LOCAL lock_timeout = '100ms'"))
                        sql = (
                            "UPDATE project SET title = 'Must wait'" if mutation == "update" else "DELETE FROM project"
                        )
                        db.execute(text(sql + " WHERE id = :id"), {"id": int(f.project.id)})
                assert rejected.value.orig.sqlstate == "55P03", (
                    "Expected row lock timeout, not an unrelated SQL failure"
                )
            finally:
                release.set()
            assert len(future.result(timeout=15)["revision"]) == 64
    finally:
        event.remove(f.engine, "after_cursor_execute", after_sql)
    with f.engine.connect() as db:
        assert (
            db.execute(text("SELECT title FROM project WHERE id = :id"), {"id": int(f.project.id)}).scalar_one()
            == f.project.title
        )
