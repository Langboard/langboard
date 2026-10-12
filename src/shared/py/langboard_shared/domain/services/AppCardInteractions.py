"""Native app requests and explicit human answers; neither is a runtime permit."""

import re
from datetime import datetime, timedelta
from langboard_sdk.interactions import validate_card_interaction, validate_card_interaction_values
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppConnection,
    AppConnectionCredential,
    AppExecutionOutbox,
    CardComment,
    CardExecutionIntent,
    ProjectColumn,
    User,
    WorkflowStageDefinition,
)
from ..models.ProjectRole import ProjectRoleAction
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionRequestRead import read_app_execution_request
from .AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
    _actor,
    _authorized_board,
    _current,
    lock_policy_mutation,
)
from .CardAppGovernance import _card
from .CardAppResources import verified_card_app_configuration
from .CardExecutionIntent import _configuration, _generation


def _scope(db, token, project_id, card_id, *, for_update):
    principal = authenticate_connection_credential(token)
    actor = _current(db, User, principal.owner_id, for_update=for_update)
    if actor is None:
        raise AppGovernanceDenied()
    definition, binding, card, connection, snapshot = verified_card_app_configuration(
        db,
        actor,
        project_id,
        card_id,
        principal.app_key,
        principal.connection_id,
        capability="interactions.request",
        for_update=for_update,
    )
    if for_update:
        _current(db, AppConnectionCredential, principal.credential_id)
        authenticate_connection_credential(token)
    return principal, definition, binding, card, connection, snapshot


def _answer(db, row):
    return db.exec(
        SqlBuilder.select.table(AppCardInteractionResponse).where(AppCardInteractionResponse.interaction_id == row.id)
    ).first()


def _receipt(db, row, *, changed=False):
    answer = _answer(db, row)
    question = row.request["questions"][0]
    choice = answer.values.get(question["id"], {}).get("choice_id") if answer else None
    decision = (
        ("approved" if choice == "approve" else "rejected") if answer and row.request["kind"] == "approval" else None
    )
    return {
        "schema_version": 1,
        "interaction_uid": row.get_uid(),
        "revision": row.revision,
        "source": {"app_key": row.app_key, "connection_uid": SnowflakeID(row.connection_id).to_short_code()},
        "state": "answered"
        if answer
        else "expired"
        if row.state == "pending" and row.expires_at <= SafeDateTime.now()
        else row.state,
        "request": row.request,
        "response": {
            "response_uid": answer.get_uid(),
            "actor_uid": SnowflakeID(answer.actor_id).to_short_code(),
            "request_revision": answer.expected_revision,
            "values": answer.values,
            "execution_generation": answer.execution_generation,
            "decision": decision,
            "accepted_at": answer.created_at.isoformat(),
        }
        if answer
        else None,
        "changed": changed,
        "execution_resumed": False,
        "workflow_transitioned": False,
    }


def _workflow(db, definition, binding, project_id, request):
    snapshot = {"enabled": binding.stage_transitions_enabled, "choices": {}}
    if not snapshot["enabled"]:
        return snapshot
    requirements = definition.declaration.get("workflow_requirements") or {}
    declared = set(requirements.get("required", []) + requirements.get("optional", []))
    for choice, outcome in request.get("outcomes", {}).items():
        key = outcome["workflow_stage"]
        uid = binding.workflow_mapping.get(key)
        if key not in declared or not uid:
            raise AppGovernanceConflict()
        column = _current(db, ProjectColumn, SnowflakeID.from_short_code(uid))
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == key).with_for_update()
        ).first()
        if (
            column is None
            or column.project_id != project_id
            or column.deleted_at
            or column.is_archive
            or column.workflow_stage != key
            or stage is None
            or not stage.is_builtin
            or not stage.is_active
        ):
            raise AppGovernanceConflict()
        snapshot["choices"][choice] = {
            "workflow_stage": key,
            "column_uid": uid,
            "column_revision": column.updated_at.isoformat(),
            "stage_revision": stage.edit_revision(),
        }
    return snapshot


def _execution(db, token, project_id, card_id, request):
    if "execution" in request:
        context = request["execution"]
        receipt = read_app_execution_request(
            token, project_id, card_id, SnowflakeID.from_short_code(context["request_uid"]), for_update=True
        )
        if receipt["generation"] != context["generation"]:
            raise AppGovernanceConflict()


def create_app_card_interaction(token, project_id, card_id, body):
    request = validate_card_interaction(body)
    expiry = datetime.fromisoformat(request["expires_at"])
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal, definition, binding, card, _, snapshot = _scope(db, token, project_id, card_id, for_update=True)
        _execution(db, token, project_id, card_id, request)
        presentation = request["presentation"]
        if presentation["type"] == "comment_reaction":
            comment = _current(db, CardComment, SnowflakeID.from_short_code(presentation["comment_uid"]))
            if comment is None or comment.card_id != card.id or comment.deleted_at:
                raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.card_id == card.id,
                AppCardInteraction.app_request_key == request["app_request_key"],
            )
            .with_for_update()
        ).first()
        if row:
            if row.request != request or row.authority_snapshot != snapshot:
                raise AppGovernanceConflict()
            return _receipt(db, row)
        now = SafeDateTime.now()
        if not now < expiry <= now + timedelta(days=7):
            raise ValueError("Interaction expiry must be within seven days")
        row = AppCardInteraction(
            project_id=project_id,
            card_id=card.id,
            connection_id=principal.connection_id,
            app_key=principal.app_key,
            app_request_key=request["app_request_key"],
            request=request,
            authority_snapshot=snapshot,
            workflow_snapshot=_workflow(db, definition, binding, project_id, request),
            expires_at=expiry,
        )
        db.insert(row)
        return _receipt(db, row, changed=True)


def _row(db, project_id, card_id, interaction_id, *, for_update):
    row = _current(db, AppCardInteraction, interaction_id, for_update=for_update)
    if row is None or row.project_id != project_id or row.card_id != card_id:
        raise AppGovernanceDenied()
    return row


def read_app_card_interaction(token, project_id, card_id, interaction_id):
    with DbSession.atomic() as db:
        principal, *_ = _scope(db, token, project_id, card_id, for_update=False)
        row = _row(db, project_id, card_id, interaction_id, for_update=False)
        if row.connection_id != principal.connection_id or row.app_key != principal.app_key:
            raise AppGovernanceDenied()
        return _receipt(db, row)


def cancel_app_card_interaction(token, project_id, card_id, interaction_id, expected_revision):
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("A positive interaction revision is required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal, *_ = _scope(db, token, project_id, card_id, for_update=True)
        row = _row(db, project_id, card_id, interaction_id, for_update=True)
        if row.connection_id != principal.connection_id or row.app_key != principal.app_key:
            raise AppGovernanceDenied()
        if row.state == "cancelled" and row.revision == expected_revision + 1:
            return _receipt(db, row)
        if row.revision != expected_revision or _answer(db, row) or row.state != "pending":
            raise AppGovernanceConflict()
        row.state, row.revision = "cancelled", row.revision + 1
        db.update(row)
        return _receipt(db, row, changed=True)


def respond_to_app_card_interaction(
    actor, project_id, card_id, interaction_id, values, expected_revision, idempotency_key, execution_generation=None
):
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    if (
        type(expected_revision) is not int
        or expected_revision < 1
        or not isinstance(idempotency_key, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", idempotency_key)
    ):
        raise ValueError("Observed revision and stable response key required")
    if execution_generation is not None and (type(execution_generation) is not int or execution_generation < 1):
        raise ValueError("A positive execution generation is required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        # Read identity before acquiring the shared configuration lock order.
        row = _row(db, project_id, card_id, interaction_id, for_update=False)
        connection = _current(db, AppConnection, row.connection_id, for_update=False)
        owner = _current(db, User, connection.owner_id, for_update=False) if connection else None
        if owner is None:
            raise AppGovernanceDenied()
        definition, binding, _, _, snapshot = verified_card_app_configuration(
            db, owner, project_id, card_id, row.app_key, row.connection_id, capability="interactions.request"
        )
        actor = _actor(db, actor)
        _authorized_board(db, actor, project_id, action=ProjectRoleAction.CardUpdate)
        row = _row(db, project_id, card_id, interaction_id, for_update=True)
        if snapshot != row.authority_snapshot:
            raise AppGovernanceConflict()
        # Only the native reaction event adapter may accept a reaction request.
        if row.request["presentation"]["type"] == "comment_reaction":
            raise AppGovernanceDenied()
        context = row.request.get("execution")
        if execution_generation != (context["generation"] if context else None):
            raise AppGovernanceConflict()
        if context:
            intent = _current(db, CardExecutionIntent, SnowflakeID.from_short_code(context["request_uid"]))
            _, _, execution_snapshot = _configuration(db, owner, project_id, card_id, row.app_key, row.connection_id)
            generation = _generation(db, card_id)
            if (
                intent is None
                or intent.card_id != card_id
                or intent.app_key != row.app_key
                or intent.connection_id != row.connection_id
                or intent.execution_generation != execution_generation
                or generation is None
                or generation.execution_generation != execution_generation
                or intent.snapshot != execution_snapshot
            ):
                raise AppGovernanceConflict()
        validated = validate_card_interaction_values(row.request, values)
        answer = _answer(db, row)
        if answer:
            if (
                answer.actor_id != actor.id
                or answer.expected_revision != expected_revision
                or answer.idempotency_key != idempotency_key
                or answer.values != validated
                or answer.execution_generation != execution_generation
            ):
                raise AppGovernanceConflict()
            return _receipt(db, row)
        if row.revision != expected_revision or row.state != "pending" or row.expires_at <= SafeDateTime.now():
            raise AppGovernanceConflict()
        if _workflow(db, definition, binding, project_id, row.request) != row.workflow_snapshot:
            raise AppGovernanceConflict()
        answer = AppCardInteractionResponse(
            interaction_id=row.id,
            actor_id=actor.id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            execution_generation=execution_generation,
            values=validated,
        )
        db.insert(answer)
        row.revision += 1
        db.update(row)
        if "events.receive" not in binding.granted_capabilities or "events.receive" not in definition.declaration.get(
            "capabilities", []
        ):
            return _receipt(db, row, changed=True)
        event = AppExecutionOutbox(
            request_id=answer.id,
            project_id=project_id,
            app_key=row.app_key,
            connection_id=row.connection_id,
            event_type="io.langboard.app.interaction.answered.v1",
        )
        db.insert(event)
        event.payload = {
            "schema_version": 1,
            "event_uid": event.get_uid(),
            "event_type": event.event_type,
            "app_key": row.app_key,
            "connection_uid": SnowflakeID(row.connection_id).to_short_code(),
            "board_uid": SnowflakeID(project_id).to_short_code(),
            "card_uid": SnowflakeID(card_id).to_short_code(),
            "interaction_uid": row.get_uid(),
            "response_uid": answer.get_uid(),
            "actor_uid": SnowflakeID(answer.actor_id).to_short_code(),
            "values": answer.values,
            "execution": row.request.get("execution"),
            "state": "answered",
            "execution_resumed": False,
            "workflow_transitioned": False,
        }
        db.update(event)
        return _receipt(db, row, changed=True)


def read_human_card_interaction(actor, project_id, card_id, interaction_id):
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=False)
        project = _authorized_board(db, actor, project_id, for_update=False, action=ProjectRoleAction.Read)
        _card(db, project, card_id, for_update=False)
        return _receipt(db, _row(db, project_id, card_id, interaction_id, for_update=False))
