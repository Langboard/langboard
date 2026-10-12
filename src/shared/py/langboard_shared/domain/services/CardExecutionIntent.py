"""Persist an explicit human run request; runtime admission remains a separate fence."""

import re
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import (
    AppExecutionOutbox,
    CardExecutionGeneration,
    CardExecutionIntent,
    ProjectColumn,
    WorkflowStageDefinition,
)
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .CardAppResources import _context, verified_card_app_configuration
from .CardDependencyState import card_dependency_state


def _generation(db, card_id, *, for_update=True):
    statement = SqlBuilder.select.table(CardExecutionGeneration).where(CardExecutionGeneration.card_id == card_id)
    return db.exec(statement.with_for_update() if for_update else statement).first()


def _configuration(db, actor, project_id, card_id, app_key, connection_id, *, for_update=True, workflow_stage="ready"):
    _, binding, card, connection, snapshot = verified_card_app_configuration(
        db,
        actor,
        project_id,
        card_id,
        app_key,
        connection_id,
        capability="execution.run",
        for_update=for_update,
    )
    column = _current(db, ProjectColumn, card.project_column_id, for_update=for_update)
    stage_statement = SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == workflow_stage)
    stage = db.exec(stage_statement.with_for_update() if for_update else stage_statement).first()
    if (
        column is None
        or column.deleted_at
        or column.is_archive
        or column.project_id != project_id
        or column.workflow_stage != workflow_stage
        or stage is None
        or not stage.is_builtin
        or not stage.is_active
        or not binding.stage_transitions_enabled
        or binding.workflow_mapping.get(workflow_stage) != column.get_uid()
    ):
        raise AppGovernanceDenied()
    dependencies = card_dependency_state(db, project_id, card_id)
    if dependencies["state"] != "clear":
        raise AppGovernanceDenied()
    return (
        card,
        connection,
        {**snapshot, "stage_revision": stage.edit_revision(), "dependency_revision": dependencies["revision"]},
    )


def read_execution_intent_context(actor, project_id, card_id, app_key, connection_id):
    with DbSession.atomic() as db:
        card, _, snapshot = _configuration(db, actor, project_id, card_id, app_key, connection_id, for_update=False)
        row = _generation(db, card.id, for_update=False)
        return {**snapshot, "generation": row.execution_generation if row else 0, "started": False}


def request_card_execution_intent(
    actor, project_id, card_id, app_key, connection_id, idempotency_key, expected_generation, expected_snapshot
):
    if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", idempotency_key):
        raise ValueError("A stable request key of 16 to 100 characters is required")
    if type(expected_generation) is not int or expected_generation < 0 or not isinstance(expected_snapshot, dict):
        raise ValueError("Observed generation and configuration snapshot required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        card, connection, snapshot = _configuration(db, actor, project_id, card_id, app_key, connection_id)
        previous = db.exec(
            SqlBuilder.select.table(CardExecutionIntent).where(
                CardExecutionIntent.card_id == card.id, CardExecutionIntent.idempotency_key == idempotency_key
            )
        ).first()
        if previous:
            if (
                previous.actor_id != actor.id
                or previous.app_key != app_key
                or previous.connection_id != connection.id
                or previous.snapshot != expected_snapshot
                or previous.execution_generation != expected_generation + 1
            ):
                raise AppGovernanceConflict()
            return _receipt(previous, changed=False)
        if snapshot != expected_snapshot:
            raise AppGovernanceConflict()
        generation = _generation(db, card.id)
        if (generation.execution_generation if generation else 0) != expected_generation:
            raise AppGovernanceConflict()
        if generation is None:
            generation = CardExecutionGeneration(card_id=card.id)
            db.insert(generation)
        generation.execution_generation += 1
        db.update(generation)
        intent = CardExecutionIntent(
            card_id=card.id,
            actor_id=actor.id,
            idempotency_key=idempotency_key,
            execution_generation=generation.execution_generation,
            app_key=app_key,
            connection_id=connection.id,
            snapshot=snapshot,
        )
        db.insert(intent)
        event = AppExecutionOutbox(
            request_id=intent.id,
            project_id=project_id,
            app_key=app_key,
            connection_id=connection.id,
            event_type="io.langboard.app.execution.requested.v1",
        )
        db.insert(event)
        event.payload = {
            "schema_version": 1,
            "event_uid": event.get_uid(),
            "event_type": event.event_type,
            "app_key": app_key,
            "connection_uid": connection.get_uid(),
            "board_uid": SnowflakeID(project_id).to_short_code(),
            "card_uid": card.get_uid(),
            "request_uid": intent.get_uid(),
            "generation": intent.execution_generation,
            "resource_uids": sorted(snapshot["resource_revisions"]),
            "state": "requested",
            "started": False,
        }
        db.update(event)
        from .AppEventDelivery import enqueue_event

        db.after_commit(lambda: enqueue_event(event.id))
        return _receipt(intent, changed=True)


def _receipt(intent, *, changed):
    return {
        "request_uid": intent.get_uid(),
        "generation": intent.execution_generation,
        "state": "requested",
        "started": False,
        "execution_resumed": False,
        "changed": changed,
    }


def read_execution_intent_receipt(actor, project_id, card_id, app_key, connection_id, request_id):
    """Retained receipt read under current human authority, including disabled app cleanup."""
    with DbSession.atomic() as db:
        _, _, connection, card, _ = _context(
            db, actor, project_id, card_id, app_key, connection_id, for_update=False, withdrawal=True
        )
        intent = _current(db, CardExecutionIntent, request_id, for_update=False)
        if (
            intent is None
            or intent.card_id != card.id
            or intent.app_key != app_key
            or intent.connection_id != connection.id
        ):
            raise AppGovernanceDenied()
        return {**_receipt(intent, changed=False), "snapshot": intent.snapshot}
