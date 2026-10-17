"""Workflow state must use one narrow column/policy read without changing semantics."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Card, ProjectColumn
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.infrastructure.repositories.factory.WorkflowStageRepository import WorkflowStageRepository
from sqlalchemy import event


def test_work_state_joins_only_column_policy_fields_and_preserves_unknowns(board, monkeypatch):  # noqa: F811
    _, _, project, _, _, columns, stages = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    with DbSession.use(readonly=False) as db:
        stages = [
            db.exec(SqlBuilder.select.table(type(stage)).where(type(stage).id == stage.id)).first() for stage in stages
        ]
        columns = [
            db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == column.id)).first()
            for column in columns
        ]
        stages[0].description = "Large guidance " * 4000
        stages[0].translations = {"ko": {"name": "작업", "description": "번역 " * 4000}}
        stages[0].is_active = False  # Bound inactive definitions still retain their semantics.
        stages[0].active_queue_policy = "include"
        stages[2].counts_as_completed = True
        stages[2].active_queue_policy = "exclude"
        stages[2].overdue_policy = "suppress"
        db.update(stages[0])
        db.update(stages[2])
        for column in columns:
            column.description = "Column guidance " * 4000
            column.translations = {"ja": {"name": "作業", "description": "説明 " * 4000}}
            db.update(column)
        extras = [
            ProjectColumn(project_id=project.id, name="Unknown", workflow_stage="absent"),
            ProjectColumn(project_id=project.id, name="Unclassified"),
            ProjectColumn(project_id=11, name="Foreign", workflow_stage="closed", is_archive=True),
            ProjectColumn(
                project_id=project.id, name="Deleted", workflow_stage="closed", deleted_at=SafeDateTime.now()
            ),
            ProjectColumn(project_id=project.id, name="Archive", workflow_stage="active", is_archive=True),
        ]
        for column in extras:
            db.insert(column)
    cards = [
        Card(id=i + 100, project_id=project.id, project_column_id=column.id, title=column.name)
        for i, column in enumerate([*columns, *extras])
    ]
    cards.append(Card(id=999, project_id=project.id, project_column_id=999999, title="Missing"))
    repository = SimpleNamespace(
        workflow_stage=WorkflowStageRepository(None, None),
        checkitem=SimpleNamespace(get_work_state_counts=Mock(return_value={})),
        card_verification=SimpleNamespace(get_latest_by_card_ids=Mock(return_value={})),
    )
    module = import_module(CardService.__module__)
    for name in ("dependency_blockers", "execution_generations", "pending_card_approvals", "card_signal_projections"):
        monkeypatch.setattr(module, name, Mock(return_value={}))
    service = CardService(None, None, repository)
    selects = []

    def capture(_conn, _cursor, statement, *_):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = service.get_work_states(cards)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    states = {card.title: result[card.id] for card in cards}
    active, review, closed = [result[card.id] for card in cards[:3]]
    assert active["workflow_stage"] == "active" and active["active_queue_policy"] == "include"
    assert review["workflow_stage"] == "review"
    assert closed["workflow_stage"] == "closed" and closed["completed"] is True
    assert closed["overdue_suppressed"] is True
    for title in ("Unknown", "Unclassified", "Foreign", "Deleted", "Missing"):
        assert states[title]["workflow_stage"] is None
        assert states[title]["completed"] is None
        assert states[title]["lifecycle"] == "active"
    assert states["Archive"]["lifecycle"] == "archived"
    assert states["Archive"]["workflow_stage"] == "active"
    assert len(selects) == 1, "Column and workflow policy must be loaded together"
    selected = selects[0].split("FROM", 1)[0].replace('"', "")
    assert "description" not in selected
    assert "translations" not in selected
    assert "entry_effects" not in selected
    assert "project_column.name" not in selected
