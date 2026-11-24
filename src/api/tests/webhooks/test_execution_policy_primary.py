"""A lagging replica must not authorize revoked execution destinations."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    GlobalCardRelationshipType,
    Project,
    ProjectColumn,
    ProjectExecutionBinding,
    WebhookSetting,
)
from langboard_shared.tasks.webhooks import ExecutionBindingPolicy, WebhookTask
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.mark.parametrize("revoked", ["binding", "webhook", "column", "relationship", "project", "webhook_target"])
def test_execution_checks_current_primary_instead_of_stale_replica(board, monkeypatch, revoked):
    primary = DbEngine.get_main_engine()
    project = board[2]
    event = "io.langboard.work.ready.v1"
    for model in (GlobalCardRelationshipType, WebhookSetting, ProjectExecutionBinding):
        model.__table__.create(primary)
    with DbSession.atomic() as db:
        relationship = GlobalCardRelationshipType(parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks")
        webhook = WebhookSetting(name="Executor", url="https://old.example.invalid/events", secret_id="test-only", events=[event])
        db.insert(relationship)
        db.insert(webhook)
        binding = ProjectExecutionBinding(
            project_id=project.id, is_enabled=True, events=[event],
            column_semantics={board[5][0].get_uid(): "ready", board[5][1].get_uid(): "terminal"},
            column_semantic_ids={str(board[5][0].id): "ready", str(board[5][1].id): "terminal"},
            prerequisite_relationship_type_id=relationship.id,
            prerequisite_relationship_type_uid=relationship.get_uid(),
            webhook_id=webhook.id, webhook_uid=webhook.get_uid(),
        )
        db.insert(binding)
    # Keep a real unchanged database snapshot behind the configured replica.
    replica = create_engine("sqlite://", poolclass=StaticPool)
    try:
        with Session(primary) as source, Session(replica) as target:
            for model in (Project, ProjectColumn, GlobalCardRelationshipType, WebhookSetting, ProjectExecutionBinding):
                model.__table__.create(replica)
                target.add_all(model(**row.model_dump()) for row in source.scalars(select(model)).all())
                target.commit()
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: replica)
        monkeypatch.setattr(ExecutionBindingPolicy.KeyVault, "get_key", lambda _: "test-only")
        with DbSession.atomic() as db:
            model, uid, field, value = {
                "binding": (ProjectExecutionBinding, binding.id, "is_enabled", False),
                "webhook": (WebhookSetting, webhook.id, "events", []),
                "column": (ProjectColumn, board[5][0].id, "deleted_at", SafeDateTime.now()),
                "relationship": (GlobalCardRelationshipType, relationship.id, "is_active", False),
                "project": (Project, project.id, "deleted_at", SafeDateTime.now()),
                "webhook_target": (WebhookSetting, webhook.id, "url", "https://current.example.invalid/events"),
            }[revoked]
            row = db.exec(SqlBuilder.select.table(model).where(model.id == uid)).first()
            setattr(row, field, value)
            db.update(row)
        if revoked == "webhook_target":
            assert WebhookTask._get_webhook_setting(webhook.get_uid()).url == "https://current.example.invalid/events"
        else:
            current = ExecutionBindingPolicy.binding_for_project(project.get_uid())
            reasons = ExecutionBindingPolicy.binding_invalid_reasons(current, event)
            expected = {
                "binding": "binding_disabled", "webhook": "event_not_allowed", "column": "column_invalid",
                "relationship": "relationship_type_not_blocking", "project": "binding_disabled",
            }[revoked]
            assert expected in reasons
    finally:
        replica.dispose()
