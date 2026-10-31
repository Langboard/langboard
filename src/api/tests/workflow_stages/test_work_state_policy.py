"""Real registry readback drives batch state; edits never execute entry effects."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import ProjectColumn
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.infrastructure.repositories.factory.ProjectColumnRepository import ProjectColumnRepository
from langboard_shared.infrastructure.repositories.factory.WorkflowStageRepository import WorkflowStageRepository
from sqlalchemy import event, text
from src.api.tests.workflow_stages.test_registry import form


pytest_plugins = ["src.api.tests.workflow_stages.test_registry"]


def test_registry_edits_and_inactive_bindings_reinterpret_a_card_batch_without_effects(registry, monkeypatch):
    registry_service, engine = registry
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE project_column"))
    ProjectColumn.__table__.create(engine)
    column = ProjectColumn(project_id=1, name="User-defined name", workflow_stage="released")
    ProjectColumnRepository(None, None).insert(column)
    fields = form(
        counts_as_completed=True,
        active_queue_policy="exclude",
        overdue_policy="suppress",
        rules=[{"name": "Complete on entry", "is_enabled": True, "trigger": {"kind": "stage_entered"},
                "condition_match": "all", "conditions": [], "actions": [{"kind": "checkitems.complete",
                "executor": {"kind": "event_actor"}, "config": {}, "requires_previous_success": False}]}],
    )
    definition = registry_service.save(fields)
    uid = definition.get_uid()
    cards = [
        SimpleNamespace(
            id=index,
            project_id=1,
            project_column_id=column.id,
            archived_at=None,
            is_linked_resource=False,
            last_change_seq=9,
            get_uid=lambda: "qa",
        )
        for index in range(1, 101)
    ]
    counts = Mock(return_value={index: (3, 1, 1, 0) for index in range(1, 101)})
    repo = SimpleNamespace(
        workflow_stage=WorkflowStageRepository(None, None),
        checkitem=SimpleNamespace(get_work_state_counts=counts),
        card_verification=SimpleNamespace(get_latest_by_card_ids=Mock(return_value={})),
    )
    monkeypatch.setattr(import_module(CardService.__module__), "dependency_blockers", lambda ids, **kwargs: {i: [] for i in ids})
    monkeypatch.setattr(import_module(CardService.__module__), "execution_generations", lambda ids: {i: 0 for i in ids})
    monkeypatch.setattr(import_module(CardService.__module__), "pending_card_approvals", lambda ids: {})
    service = CardService(None, None, repo)
    queries = []
    event.listen(engine, "before_cursor_execute", lambda _c, _cursor, statement, *_: queries.append(statement))
    states = service.get_work_states(cards)
    assert all(s["completed"] is True and s["verification_state"] == "partial" for s in states.values())
    assert all(s["active_queue_eligible"] is False and s["overdue_suppressed"] is True for s in states.values())
    assert len(queries) == 1 and "JOIN workflow_stage_definition" in queries[0]
    # Policy edits alter interpretation immediately; inactive bindings still resolve.
    fields.update(counts_as_completed=False, active_queue_policy="conditional", overdue_policy="normal")
    registry_service.save(fields, uid)
    registry_service.deactivate(uid)
    states = service.get_work_states(cards)
    assert all(s["completed"] is False and s["overdue_suppressed"] is False for s in states.values())
    assert all(s["active_queue_eligible"] is None and s["execution_state"] == "human_active" for s in states.values())
    assert all(s["checklist_progress"] == {"total": 3, "completed": 1} for s in states.values())
    # Read projection creates neither checkitem mutations nor effect execution services.
    assert counts.call_count == 2
    with DbSession.use(readonly=True) as db:
        persisted = db.exec(SqlBuilder.select.table(ProjectColumn)).first()
    assert persisted.workflow_stage == "released"


@pytest.mark.parametrize("registry", ["sqlite://", "postgresql-test"], indirect=True)
def test_policy_edits_publish_current_column_metadata_after_commit_even_without_cards(registry, monkeypatch):
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Organization, Project, User
    from langboard_shared.domain.services.factory.WorkflowStageService import WorkflowStageService
    from langboard_shared.publishers import ProjectColumnPublisher

    service, engine = registry
    fields = form(counts_as_completed=False)
    stage = service.save(fields)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE project_column"))
    User.__table__.create(engine)
    Organization.__table__.create(engine)
    Project.__table__.create(engine)
    ProjectColumn.__table__.create(engine)
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        owner = User(email="policy-owner@example.invalid", password="fixture", firstname="Policy", lastname="Owner")
        db.insert(owner)
        projects = [
            Project(owner_id=owner.id, title="First"),
            Project(owner_id=owner.id, title="Second"),
            Project(owner_id=owner.id, title="Deleted", deleted_at=now),
        ]
        db.insert_all(projects)
        first, second, deleted = [project.id for project in projects]
        project_uids = {project.id: project.get_uid() for project in projects}
        visible = [ProjectColumn(project_id=project_id, name="Empty", workflow_stage="released") for project_id in [first, first, second]]
        db.insert_all(visible + [
            ProjectColumn(project_id=first, name="Other", workflow_stage="active"),
            ProjectColumn(project_id=first, name="Archive", workflow_stage="released", is_archive=True),
            ProjectColumn(project_id=first, name="Deleted", workflow_stage="released", deleted_at=now),
            ProjectColumn(project_id=deleted, name="Deleted project", workflow_stage="released"),
        ])
    # No cards exist. Only the native column query and publisher are relevant.
    monkeypatch.setattr(service.repo.workflow_stage, "get_policy_affected_cards", Mock(return_value=[]))
    monkeypatch.setattr(service, "_publish_work_states", WorkflowStageService._publish_work_states.__get__(service))
    publish = Mock()
    monkeypatch.setattr(ProjectColumnPublisher, "put_dispather", publish)
    from langboard_shared.core.db.DbEngine import DbEngine
    replica = Mock(side_effect=AssertionError("policy dispatch must use current primary state"))
    monkeypatch.setattr(DbEngine, "get_readonly_engine", replica)
    for completed in [True, False, True]:
        publish.reset_mock()
        with DbSession.atomic():
            service.save({**fields, "counts_as_completed": completed}, stage.get_uid())
            publish.assert_not_called()
        assert publish.call_count == len(visible)
        events = {call.args[0]["uid"]: call.args for call in publish.call_args_list}
        assert set(events) == {column.get_uid() for column in visible}
        for column in visible:
            payload, destinations = events[column.get_uid()]
            assert payload == {"uid": column.get_uid(), "workflow_stage": "released", "workflow_counts_as_completed": completed}
            assert {destination.event for destination in destinations} == {
                f"board:column:workflow-stage:changed:{project_uids[column.project_id]}",
                f"dashboard:project:column:workflow-stage:changed:{project_uids[column.project_id]}",
            }
    publish.reset_mock()
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            service.save({**fields, "counts_as_completed": False}, stage.get_uid())
            raise RuntimeError("rollback")
    publish.assert_not_called()
    with DbSession.atomic():
        service.save({**fields, "counts_as_completed": True, "name": "Renamed only"}, stage.get_uid())
    publish.assert_not_called()
    # Existing card/dependent refresh must coexist with the new column events.
    service.repo.workflow_stage.get_policy_affected_cards.return_value = [(first, 101), (first, 102), (second, 103)]
    projections = Mock()
    monkeypatch.setattr(service, "_get_service", Mock(return_value=SimpleNamespace(publish_work_states=projections)))
    with DbSession.atomic():
        service.save({**fields, "counts_as_completed": False}, stage.get_uid())
    assert publish.call_count == len(visible)
    assert sorted((call.args[0].id, call.args[1]) for call in projections.call_args_list) == [
        (first, [101, 102]), (second, [103]),
    ]
    replica.assert_not_called()
