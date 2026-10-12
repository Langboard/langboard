"""Native app requests and explicit human answers; neither is a runtime permit."""

import re
from datetime import datetime, timedelta
from langboard_sdk.interactions import validate_card_interaction, validate_card_interaction_values
from sqlalchemy import select
from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime, SnowflakeID
from ..models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppConnection,
    AppConnectionCredential,
    AppExecutionAcknowledgment,
    AppExecutionOutbox,
    CardComment,
    CardCommentReaction,
    CardExecutionIntent,
    ProjectColumn,
    User,
    WorkflowStageDefinition,
)
from ..models.ProjectRole import ProjectRoleAction
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionRequestRead import current_execution_configuration, read_app_execution_request
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
from .CardAudience import resolve_card_audience
from .CardExecutionIntent import _generation
from .CardVisibilityPolicy import CardVisibility


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


def _execution(db, token, project_id, card_id, request, *, for_update=True):
    if "execution" in request:
        context = request["execution"]
        receipt = read_app_execution_request(
            token, project_id, card_id, SnowflakeID.from_short_code(context["request_uid"]), for_update=for_update
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
        if (
            presentation["type"] == "comment_reaction"
            and db.exec(
                SqlBuilder.select.table(AppCardInteraction).where(
                    AppCardInteraction.card_id == card.id,
                    AppCardInteraction.state == "pending",
                    AppCardInteraction.expires_at > now,
                    ~select(AppCardInteractionResponse.id)
                    .where(AppCardInteractionResponse.interaction_id == AppCardInteraction.id)
                    .exists(),
                    AppCardInteraction.request["presentation"]["type"].as_string() == "comment_reaction",
                    AppCardInteraction.request["presentation"]["comment_uid"].as_string()
                    == presentation["comment_uid"],
                )
            ).first()
        ):
            raise AppGovernanceConflict()
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


def read_app_card_interaction_by_request_key(token, project_id, card_id, app_request_key):
    if not isinstance(app_request_key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", app_request_key):
        raise ValueError("Invalid interaction request key")
    with DbSession.atomic() as db:
        principal, *_ = _scope(db, token, project_id, card_id, for_update=False)
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction).where(
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.app_key == principal.app_key,
                AppCardInteraction.app_request_key == app_request_key,
            )
        ).first()
        if row is None:
            return {
                "state": "missing",
                "request": None,
                "response": None,
                "changed": False,
                "execution_resumed": False,
                "workflow_transitioned": False,
            }
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
    actor,
    project_id,
    card_id,
    interaction_id,
    values,
    expected_revision,
    idempotency_key,
    execution_generation=None,
    *,
    _reaction_event_id=None,
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
        project = _authorized_board(db, actor, project_id, action=ProjectRoleAction.CardUpdate)
        card = _card(db, project, card_id)
        if not resolve_card_audience(db, project.id, actor.id, channel=CollaborationChannel.HumanUI).can_read_card(
            CardVisibility(card.visibility), owner_user_id=card.owner_user_id
        ):
            raise AppGovernanceDenied()
        row = _row(db, project_id, card_id, interaction_id, for_update=True)
        if snapshot != row.authority_snapshot:
            raise AppGovernanceConflict()
        # Only the native reaction event adapter may accept a reaction request.
        if row.request["presentation"]["type"] == "comment_reaction":
            reaction = _current(db, CardCommentReaction, _reaction_event_id) if _reaction_event_id else None
            presentation = row.request["presentation"]
            comment = _current(db, CardComment, SnowflakeID.from_short_code(presentation["comment_uid"]))
            choice = presentation["reactions"].get(reaction.reaction_type) if reaction else None
            if (
                reaction is None
                or reaction.user_id != actor.id
                or reaction.bot_id is not None
                or comment is None
                or comment.card_id != card_id
                or comment.deleted_at
                or reaction.comment_id != comment.id
                or reaction.created_at <= row.created_at
                or choice is None
                or idempotency_key != f"reaction-{reaction.get_uid()}"
                or values != {row.request["questions"][0]["id"]: {"choice_id": choice}}
            ):
                raise AppGovernanceDenied()
        elif _reaction_event_id is not None:
            raise AppGovernanceDenied()
        context = row.request.get("execution")
        if execution_generation != (context["generation"] if context else None):
            raise AppGovernanceConflict()
        if context:
            intent = _current(db, CardExecutionIntent, SnowflakeID.from_short_code(context["request_uid"]))
            if intent is None:
                raise AppGovernanceConflict()
            current_execution_configuration(db, intent, project_id, for_update=True)
            generation = _generation(db, card_id)
            if (
                intent is None
                or intent.card_id != card_id
                or intent.app_key != row.app_key
                or intent.connection_id != row.connection_id
                or intent.execution_generation != execution_generation
                or generation is None
                or generation.execution_generation != execution_generation
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
        from .AppEventDelivery import enqueue_event

        db.after_commit(lambda: enqueue_event(event.id))
        return _receipt(db, row, changed=True)


def read_human_card_interaction(actor, project_id, card_id, interaction_id):
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=False)
        project = _authorized_board(db, actor, project_id, for_update=False, action=ProjectRoleAction.Read)
        card = _card(db, project, card_id, for_update=False)
        if not resolve_card_audience(db, project.id, actor.id, channel=CollaborationChannel.HumanUI).can_read_card(
            CardVisibility(card.visibility), owner_user_id=card.owner_user_id
        ):
            raise AppGovernanceDenied()
        return _receipt(db, _row(db, project_id, card_id, interaction_id, for_update=False))


def pending_comment_interactions(db, project_id, card_id, comment_id):
    """Bound pending lookup by the native card/comment, excluding retained answers."""
    return db.exec(
        SqlBuilder.select.table(AppCardInteraction)
        .where(
            AppCardInteraction.project_id == project_id,
            AppCardInteraction.card_id == card_id,
            AppCardInteraction.state == "pending",
            AppCardInteraction.expires_at > SafeDateTime.now(),
            ~select(AppCardInteractionResponse.id)
            .where(AppCardInteractionResponse.interaction_id == AppCardInteraction.id)
            .exists(),
            AppCardInteraction.request["presentation"]["type"].as_string() == "comment_reaction",
            AppCardInteraction.request["presentation"]["comment_uid"].as_string()
            == SnowflakeID(comment_id).to_short_code(),
        )
        .limit(2)
    ).all()


def accept_current_comment_reaction(actor, project_id, card_id, comment_id, reaction_type):
    """Accept a newly stored human reaction without changing ordinary reaction UX."""
    if not isinstance(actor, User):
        return None
    with DbSession.atomic() as db:
        candidates = pending_comment_interactions(db, project_id, card_id, comment_id)
        if len(candidates) != 1:
            return None
        reaction = db.exec(
            SqlBuilder.select.table(CardCommentReaction).where(
                CardCommentReaction.comment_id == comment_id,
                CardCommentReaction.user_id == actor.id,
                CardCommentReaction.bot_id.is_(None),
                CardCommentReaction.reaction_type == reaction_type,
            )
        ).first()
        row = candidates[0]
        choice = row.request["presentation"]["reactions"].get(reaction_type)
        if reaction is None or choice is None:
            return None
        try:
            return respond_to_app_card_interaction(
                actor,
                project_id,
                card_id,
                row.id,
                {row.request["questions"][0]["id"]: {"choice_id": choice}},
                row.revision,
                f"reaction-{reaction.get_uid()}",
                row.request.get("execution", {}).get("generation"),
                _reaction_event_id=reaction.id,
            )
        except (AppGovernanceDenied, AppGovernanceConflict):
            return None


def _interaction_ack(db, row, answer, *, changed=False):
    ack = db.exec(
        SqlBuilder.select.table(AppExecutionAcknowledgment).where(
            AppExecutionAcknowledgment.request_id == answer.id,
            AppExecutionAcknowledgment.app_key == row.app_key,
            AppExecutionAcknowledgment.connection_id == row.connection_id,
        )
    ).first()
    result = {
        "schema_version": 1,
        "interaction_uid": row.get_uid(),
        "response_uid": answer.get_uid(),
        "state": "received" if ack else "not_received",
        "changed": changed,
        "execution_resumed": False,
        "workflow_transitioned": False,
    }
    if ack:
        result.update(
            acknowledgment_uid=ack.get_uid(),
            event_uid=SnowflakeID(ack.event_id).to_short_code(),
            runtime_reference=ack.runtime_reference,
        )
    return result


def read_interaction_acknowledgment(token, project_id, card_id, interaction_id):
    with DbSession.atomic() as db:
        principal, _, _, _, _, snapshot = _scope(db, token, project_id, card_id, for_update=False)
        row = _row(db, project_id, card_id, interaction_id, for_update=False)
        if row.app_key != principal.app_key or row.connection_id != principal.connection_id:
            raise AppGovernanceDenied()
        if row.authority_snapshot != snapshot:
            raise AppGovernanceConflict()
        _execution(db, token, project_id, card_id, row.request, for_update=False)
        answer = _answer(db, row)
        if answer is None:
            raise AppGovernanceConflict()
        return _interaction_ack(db, row, answer)


def acknowledge_interaction_response(token, project_id, card_id, interaction_id, event_id, runtime_reference):
    if not isinstance(runtime_reference, str) or not runtime_reference.strip() or len(runtime_reference) > 200:
        raise ValueError("A runtime reference of 1 to 200 characters is required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal, _, _, _, _, snapshot = _scope(db, token, project_id, card_id, for_update=True)
        row = _row(db, project_id, card_id, interaction_id, for_update=True)
        if row.app_key != principal.app_key or row.connection_id != principal.connection_id:
            raise AppGovernanceDenied()
        if row.authority_snapshot != snapshot:
            raise AppGovernanceConflict()
        _execution(db, token, project_id, card_id, row.request)
        answer = _answer(db, row)
        event = _current(db, AppExecutionOutbox, event_id)
        if (
            answer is None
            or event is None
            or event.request_id != answer.id
            or event.project_id != project_id
            or event.app_key != row.app_key
            or event.connection_id != row.connection_id
            or event.event_type != "io.langboard.app.interaction.answered.v1"
            or event.state not in ("delivering", "delivered")
            or event.payload.get("state") != "answered"
            or event.payload.get("event_uid") != event.get_uid()
            or event.payload.get("interaction_uid") != row.get_uid()
            or event.payload.get("response_uid") != answer.get_uid()
            or event.payload.get("card_uid") != SnowflakeID(card_id).to_short_code()
            or event.payload.get("board_uid") != SnowflakeID(project_id).to_short_code()
            or event.payload.get("app_key") != row.app_key
            or event.payload.get("connection_uid") != SnowflakeID(row.connection_id).to_short_code()
            or event.payload.get("actor_uid") != SnowflakeID(answer.actor_id).to_short_code()
            or event.payload.get("values") != answer.values
            or event.payload.get("execution") != row.request.get("execution")
        ):
            raise AppGovernanceDenied()
        ack = db.exec(
            SqlBuilder.select.table(AppExecutionAcknowledgment).where(
                AppExecutionAcknowledgment.request_id == answer.id
            )
        ).first()
        changed = ack is None
        if ack is None:
            db.insert(
                AppExecutionAcknowledgment(
                    request_id=answer.id,
                    event_id=event.id,
                    app_key=row.app_key,
                    connection_id=row.connection_id,
                    runtime_reference=runtime_reference,
                )
            )
        elif ack.event_id != event.id or ack.runtime_reference != runtime_reference:
            raise AppGovernanceConflict()
        return _interaction_ack(db, row, answer, changed=changed)
