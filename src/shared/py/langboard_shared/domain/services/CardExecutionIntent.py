"""Explicit human intent using the canonical generation, request and outbox."""

import re
from sqlalchemy import insert, select, text
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.types import SnowflakeID
from ..models import AppConnection, AppExecutionRequest, CardExecutionIntent, Project
from .AppExecutionControl import _human_control_scope
from .AppExecutionGrant import evaluate_connection_execution_grant
from .AppExecutionRequests import accept_authorized_execution_request
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .DomainService import DomainService


def _generation(db, card_id):
    return int(db.exec(select(text(
        "COALESCE((SELECT execution_generation FROM card_execution_generation WHERE card_id=:card_id),0)"
    )), params={"card_id": int(card_id)}).first()[0])


def _configuration(db, actor, project_id, card_id, app_key, connection_id):
    _human_control_scope(db, DomainService(), actor, project_id, card_id, None)
    generation = _generation(db, card_id)
    authority = evaluate_connection_execution_grant(connection_id, project_id, card_id, generation)
    if authority["app_key"] != app_key:
        raise AppGovernanceDenied()
    return authority, generation


def read_execution_intent_context(actor, project_id, card_id, app_key, connection_id):
    with DbSession.atomic() as db:
        authority, generation = _configuration(db, actor, project_id, card_id, app_key, connection_id)
        return {**authority, "generation": generation, "started": False}


def request_card_execution_intent(
    actor, project_id, card_id, app_key, connection_id, idempotency_key, expected_generation, expected_snapshot
):
    if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", idempotency_key):
        raise ValueError("A stable request key of 16 to 100 characters is required")
    if type(expected_generation) is not int or expected_generation < 0 or not isinstance(expected_snapshot, dict):
        raise ValueError("Observed generation and configuration snapshot required")
    with DbSession.atomic() as db:
        # Same card generation lock as the native readiness writer, before card locks.
        if DbEngine.get_main_engine().dialect.name == "postgresql":
            db.exec(select(text(
                "pg_advisory_xact_lock(hashtextextended('langboard_execution:' || CAST(:card_id AS text), 0))"
            )), params={"card_id": int(card_id)})
        lock_policy_mutation(db)
        authority, generation = _configuration(db, actor, project_id, card_id, app_key, connection_id)
        previous = db.exec(SqlBuilder.select.table(CardExecutionIntent).where(
            CardExecutionIntent.card_id == card_id, CardExecutionIntent.idempotency_key == idempotency_key
        )).first()
        if previous:
            if (previous.actor_id != actor.id or previous.app_key != app_key
                or previous.connection_id != connection_id or previous.snapshot != expected_snapshot
                or previous.execution_generation != expected_generation + 1
                or generation != previous.execution_generation):
                raise AppGovernanceConflict()
            request = _current(db, AppExecutionRequest, previous.id)
            if (request is None or request.authority != authority
                or request.connection_id != connection_id or request.card_id != card_id):
                raise AppGovernanceConflict()
            return _receipt(previous, changed=False)
        observed = {**authority, "generation": generation, "started": False}
        if generation != expected_generation or expected_snapshot != observed:
            raise AppGovernanceConflict()
        # Do not authorize another generation while a request for this one exists.
        existing = db.exec(SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.card_id == card_id, AppExecutionRequest.generation == generation
        )).first()
        if existing is not None:
            raise AppGovernanceConflict()
        bumped = db.exec(text("""
            INSERT INTO card_execution_generation(card_id, execution_generation)
            VALUES (:card_id, 1)
            ON CONFLICT(card_id) DO UPDATE SET execution_generation = card_execution_generation.execution_generation + 1
            WHERE card_execution_generation.execution_generation = :expected
        """), params={"card_id": int(card_id), "expected": expected_generation})
        if bumped != 1 or _generation(db, card_id) != expected_generation + 1:
            raise AppGovernanceConflict()
        authority = evaluate_connection_execution_grant(connection_id, project_id, card_id, expected_generation + 1)
        receipt = accept_authorized_execution_request(db, authority, project_id, card_id, expected_generation + 1)
        # Intent is audit evidence for the canonical request, never a second request identity.
        intent = CardExecutionIntent(
            id=SnowflakeID.from_short_code(receipt["request_uid"]), card_id=card_id,
            actor_id=actor.id, idempotency_key=idempotency_key,
            execution_generation=receipt["generation"], app_key=app_key,
            connection_id=connection_id, snapshot=expected_snapshot,
        )
        db.exec(insert(CardExecutionIntent.__table__).values(
            **{name: getattr(intent, name) for name in CardExecutionIntent.__table__.columns.keys()}
        ))
        return _receipt(intent, changed=True)


def _receipt(intent, *, changed):
    return {"request_uid": intent.get_uid(), "generation": intent.execution_generation,
            "state": "requested", "started": False, "execution_resumed": False, "changed": changed}


def read_execution_intent_receipt(actor, project_id, card_id, app_key, connection_id, request_id):
    """Retain audit reads under current human authority after app revocation."""
    with DbSession.atomic() as db:
        current = _human_control_scope(db, DomainService(), actor, project_id, card_id, None)
        connection = _current(db, AppConnection, connection_id)
        project = _current(db, Project, project_id)
        if (connection is None or connection.app_key != app_key
            or connection.ownership not in ("personal", "organization")
            or (connection.ownership == "personal" and connection.owner_id != current.id)
            or (connection.ownership == "organization" and (project.organization_id is None
                or connection.organization_id != project.organization_id))):
            raise AppGovernanceDenied()
        intent = _current(db, CardExecutionIntent, request_id, for_update=False)
        request = _current(db, AppExecutionRequest, request_id, for_update=False)
        if (intent is None or request is None or intent.card_id != card_id
            or request.project_id != project_id or request.card_id != card_id
            or intent.app_key != app_key or intent.connection_id != connection_id
            or request.app_key != app_key or request.connection_id != connection_id):
            raise AppGovernanceDenied()
        return {**_receipt(intent, changed=False), "snapshot": intent.snapshot}
