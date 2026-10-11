"""App-owned structured requests, independent of inbox signals and runtime control."""

from datetime import datetime, timedelta
from langboard_sdk.interactions import validate_card_interaction, validate_card_interaction_values
from sqlalchemy import select
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppCardInteractionTransition,
    AppConnection,
    AppDefinition,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardAppResourceSelection,
    CardComment,
    Project,
    ProjectRole,
    User,
)
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppEventDelivery import _expired
from .AppExecutionAcknowledgments import _request
from .AppExecutionGrant import AppExecutionCredentialDenied
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, require_connection_access
from .DomainService import DomainService


class AppCardInteractionConflict(Exception):
    pass


def _scope(db, token, project_id, card_id):
    try:
        principal = authenticate_connection_credential(token)
    except AppGovernanceDenied as exc:
        raise AppExecutionCredentialDenied() from exc
    return _scope_principal(db, principal, project_id, card_id)


def _scope_principal(db, principal, project_id, card_id):
    project = _current(db, Project, project_id)
    card = _current(db, Card, card_id)
    connection = _current(db, AppConnection, principal.connection_id)
    actor = _current(db, User, principal.owner_id)
    if (
        any(row is None for row in (project, card, connection, actor))
        or card.project_id != project.id
        or card.deleted_at
        or card.archived_at
        or card.source_type
    ):
        raise AppGovernanceDenied()
    require_connection_access(db, actor, project, connection, unattended=True)
    role = db.exec(
        SqlBuilder.select.table(ProjectRole).where(
            ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id
        )
    ).first()
    if project.owner_id != actor.id and (role is None or not role.is_granted("card_update")):
        raise AppGovernanceDenied()
    if card.visibility not in ("SHARED", "INTERNAL") or (
        card.visibility == "INTERNAL" and not DomainService().card._resolve_internal_access(actor)
    ):
        raise AppGovernanceDenied()
    owner = db.exec(
        SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id).with_for_update()
    ).first()
    selection = db.exec(
        SqlBuilder.select.table(CardAppResourceSelection)
        .where(CardAppResourceSelection.card_id == card.id, CardAppResourceSelection.app_key == principal.app_key)
        .with_for_update()
    ).first()
    definition = db.exec(
        SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key).with_for_update()
    ).first()
    binding = db.exec(
        SqlBuilder.select.table(BoardAppBinding)
        .where(BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == principal.app_key)
        .with_for_update()
    ).first()
    if (
        owner is None
        or owner.app_key != principal.app_key
        or selection is None
        or selection.connection_id != connection.id
        or not selection.resource_uids
        or definition is None
        or not definition.is_enabled
        or binding is None
        or binding.state != "enabled"
        or "interactions.request" not in definition.declaration.get("capabilities", [])
        or "interactions.request" not in binding.granted_capabilities
    ):
        raise AppGovernanceDenied()
    from ...core.types import SnowflakeID

    resources = db.exec(
        SqlBuilder.select.table(AppResourceBinding)
        .where(
            AppResourceBinding.id.in_([SnowflakeID.from_short_code(uid) for uid in selection.resource_uids]),
            AppResourceBinding.connection_id == connection.id,
            AppResourceBinding.board_binding_id == binding.id,
            AppResourceBinding.is_selected == True,  # noqa: E712
            AppResourceBinding.access_state == "granted",
            AppResourceBinding.resource_type.in_(definition.declaration.get("resource_types", [])),
        )
        .with_for_update()
    ).all()
    if {r.get_uid() for r in resources} != set(selection.resource_uids):
        raise AppGovernanceDenied()
    return principal, card


def _receipt(row):
    with DbSession.use(readonly=False) as db:
        answer = db.exec(
            SqlBuilder.select.table(AppCardInteractionResponse).where(
                AppCardInteractionResponse.interaction_id == row.id
            )
        ).first()
        delivery = _response_delivery(db, answer) if answer else {}
        transition = (
            db.exec(
                SqlBuilder.select.table(AppCardInteractionTransition).where(
                    AppCardInteractionTransition.response_id == answer.id
                )
            ).first()
            if answer
            else None
        )
    return _project_receipt(row, answer, delivery, transition)


def _project_receipt(row, answer, delivery, transition=None):
    return {
        "schema_version": 1,
        "interaction_uid": row.get_uid(),
        "revision": row.revision,
        "state": "answered"
        if answer
        else "expired"
        if row.state == "pending" and _expired(row.expires_at, SafeDateTime.now())
        else row.state,
        "request": row.request,
        "response": {
            "response_uid": answer.get_uid(),
            "actor_uid": SnowflakeID(answer.actor_id).to_short_code(),
            "values": answer.values,
            "execution_generation": answer.execution_generation,
            "accepted_at": answer.created_at.isoformat(),
        }
        if answer
        else None,
        "execution_resumed": False,
        "workflow_transitioned": bool(transition and transition.receipt["workflow_transitioned"]),
        "workflow_receipt": {"transition_uid": transition.get_uid(), **transition.receipt} if transition else None,
        "workflow_plan": _selected_workflow_plan(row, answer.values) if answer else None,
        **delivery,
    }


def _selected_workflow_plan(row, values):
    snapshot = row.workflow_snapshot
    if not snapshot:
        return {"state": "legacy_unbound"}
    question = row.request["questions"][0]
    choice = values.get(question["id"], {}).get("choice_id")
    target = snapshot.get("choices", {}).get(choice)
    if target:
        return {"state": "planned", "binding_revision": snapshot["binding_revision"], **target}
    return {"state": "disabled" if not snapshot.get("enabled") else "no_outcome"}


def _freeze_workflow(db, principal, project_id, request):
    from ..models import ProjectColumn, WorkflowStageDefinition

    binding = db.exec(
        SqlBuilder.select.table(BoardAppBinding)
        .where(BoardAppBinding.project_id == project_id, BoardAppBinding.app_key == principal.app_key)
        .with_for_update()
    ).first()
    snapshot = {
        "enabled": binding.stage_transitions_enabled,
        "binding_revision": binding.edit_revision(),
        "choices": {},
    }
    outcomes = request.get("outcomes", {})
    if not snapshot["enabled"] or not outcomes:
        return snapshot
    definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key)).first()
    requirements = definition.declaration.get("workflow_requirements") or {}
    declared = set(requirements.get("required", []) + requirements.get("optional", []))
    for choice, outcome in outcomes.items():
        stage_key = outcome["workflow_stage"]
        uid = binding.workflow_mapping.get(stage_key)
        if stage_key not in declared or not uid:
            raise AppCardInteractionConflict()
        column = _current(db, ProjectColumn, SnowflakeID.from_short_code(uid))
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition)
            .where(WorkflowStageDefinition.key == stage_key)
            .with_for_update()
        ).first()
        if (
            column is None
            or column.project_id != project_id
            or column.deleted_at
            or column.is_archive
            or column.workflow_stage != stage_key
            or stage is None
            or not stage.is_builtin
            or not stage.is_active
        ):
            raise AppCardInteractionConflict()
        snapshot["choices"][choice] = {
            "workflow_stage": stage_key,
            "column_uid": uid,
            "column_revision": column.updated_at.isoformat(),
            "stage_revision": stage.updated_at.isoformat(),
        }
    return snapshot


def _validate_workflow_snapshot(db, row, values):
    """A saved choice cannot acquire a different target after mapping changes."""
    from ..models import ProjectColumn, WorkflowStageDefinition

    plan = _selected_workflow_plan(row, values)
    if plan["state"] != "planned":
        return plan
    binding = db.exec(
        SqlBuilder.select.table(BoardAppBinding)
        .where(BoardAppBinding.project_id == row.project_id, BoardAppBinding.app_key == row.app_key)
        .with_for_update()
    ).first()
    column = _current(db, ProjectColumn, SnowflakeID.from_short_code(plan["column_uid"]))
    stage = db.exec(
        SqlBuilder.select.table(WorkflowStageDefinition)
        .where(WorkflowStageDefinition.key == plan["workflow_stage"])
        .with_for_update()
    ).first()
    if (
        binding is None
        or binding.edit_revision() != plan["binding_revision"]
        or column is None
        or column.project_id != row.project_id
        or column.deleted_at
        or column.is_archive
        or column.workflow_stage != plan["workflow_stage"]
        or column.updated_at.isoformat() != plan["column_revision"]
        or stage is None
        or not stage.is_builtin
        or not stage.is_active
        or stage.updated_at.isoformat() != plan["stage_revision"]
    ):
        raise AppCardInteractionConflict()
    return plan


def list_card_interactions(service, actor, project_id, card_id, *, limit=25, before=None, channel=None):
    """Read retained requests using native card ACLs; never require an app credential.

    Disconnecting an app prevents new execution, but does not erase card history.
    Related response/delivery records are fetched once per page, not once per row.
    """
    from ...core.security.CollaborationChannel import CollaborationChannel
    from ..models import AppExecutionAcknowledgment, AppExecutionOutbox
    from .AppEventDestination import APP_INTERACTION_RESPONSE_EVENT

    if type(limit) is not int or not 1 <= limit <= 25:
        raise ValueError("Interaction page size must be between 1 and 25")
    readable = (
        service.card.resolve_readable_card(project_id, card_id, actor, channel or CollaborationChannel.Api)
        if isinstance(actor, User)
        else None
    )
    if readable is None:
        raise AppGovernanceDenied()
    with DbSession.use(readonly=False) as db:
        if readable[0].owner_id != actor.id:
            role = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.project_id == project_id, ProjectRole.user_id == actor.id
                )
            ).first()
            if role is None or not role.is_granted("read"):
                raise AppGovernanceDenied()
        query = SqlBuilder.select.table(AppCardInteraction).where(
            AppCardInteraction.project_id == project_id, AppCardInteraction.card_id == card_id
        )
        if before is not None:
            query = query.where(AppCardInteraction.id < before)
        rows = db.exec(query.order_by(AppCardInteraction.id.desc()).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        if not rows:
            return {"items": [], "next_cursor": None, "card_revision": readable[1].last_change_seq}
        answers = db.exec(
            SqlBuilder.select.table(AppCardInteractionResponse).where(
                AppCardInteractionResponse.interaction_id.in_([row.id for row in rows])
            )
        ).all()
        by_interaction = {answer.interaction_id: answer for answer in answers}
        events, acknowledgments, transitions = {}, {}, {}
        if answers:
            answer_ids = [answer.id for answer in answers]
            transitions = {
                transition.response_id: transition
                for transition in db.exec(
                    SqlBuilder.select.table(AppCardInteractionTransition).where(
                        AppCardInteractionTransition.response_id.in_(answer_ids)
                    )
                ).all()
            }
            events = {
                event.request_id: event
                for event in db.exec(
                    SqlBuilder.select.table(AppExecutionOutbox).where(
                        AppExecutionOutbox.request_id.in_(answer_ids),
                        AppExecutionOutbox.event_type == APP_INTERACTION_RESPONSE_EVENT,
                    )
                ).all()
            }
            if events:
                acknowledgments = {
                    ack.request_id: ack
                    for ack in db.exec(
                        SqlBuilder.select.table(AppExecutionAcknowledgment).where(
                            AppExecutionAcknowledgment.request_id.in_(list(events))
                        )
                    ).all()
                }
        items = []
        for row in rows:
            answer = by_interaction.get(row.id)
            event = events.get(answer.id) if answer else None
            ack = acknowledgments.get(answer.id) if answer else None
            delivery = (
                {
                    "event_uid": event.get_uid() if event else None,
                    "delivery_state": event.state if event else "not_queued",
                    "acknowledgment_uid": ack.get_uid() if ack else None,
                    "reception_state": "received" if ack else "not_received",
                }
                if answer
                else {}
            )
            items.append(_project_receipt(row, answer, delivery, transitions.get(answer.id) if answer else None))
        return {
            "items": items,
            "next_cursor": rows[-1].get_uid() if more else None,
            "card_revision": readable[1].last_change_seq,
        }


def create_app_card_interaction(token, project_id, card_id, body):
    request = validate_card_interaction(body)
    expiry = datetime.fromisoformat(request["expires_at"])
    with DbSession.atomic() as db:
        principal, card = _scope(db, token, project_id, card_id)
        if "execution" in request:
            from ...core.types import SnowflakeID

            context = request["execution"]
            execution = _request(db, token, project_id, card_id, SnowflakeID.from_short_code(context["request_uid"]))
            if execution.generation != context["generation"]:
                raise AppCardInteractionConflict()
        presentation = request["presentation"]
        if presentation["type"] == "comment_reaction":
            from ...core.types import SnowflakeID

            comment = _current(db, CardComment, SnowflakeID.from_short_code(presentation["comment_uid"]))
            if comment is None or comment.card_id != card.id:
                raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.app_request_key == request["app_request_key"],
            )
            .with_for_update()
        ).first()
        if row is not None:
            if row.request != request:
                raise AppCardInteractionConflict()
            return {**_receipt(row), "changed": False}
        now = SafeDateTime.now()
        if presentation["type"] == "comment_reaction":
            pending = db.exec(
                SqlBuilder.select.table(AppCardInteraction)
                .where(
                    AppCardInteraction.card_id == card_id,
                    AppCardInteraction.state == "pending",
                    AppCardInteraction.expires_at > now,
                    AppCardInteraction.request["presentation"]["type"].as_string() == "comment_reaction",
                    AppCardInteraction.request["presentation"]["comment_uid"].as_string()
                    == presentation["comment_uid"],
                    ~select(AppCardInteractionResponse.id)
                    .where(AppCardInteractionResponse.interaction_id == AppCardInteraction.id)
                    .exists(),
                )
                .limit(1)
            ).first()
            if pending is not None:
                raise AppCardInteractionConflict()
        if expiry <= now or expiry > now + timedelta(days=7):
            raise ValueError("Interaction expiry must be within seven days")
        row = AppCardInteraction(
            project_id=project_id,
            card_id=card_id,
            connection_id=principal.connection_id,
            app_key=principal.app_key,
            app_request_key=request["app_request_key"],
            request=request,
            workflow_snapshot=_freeze_workflow(db, principal, project_id, request),
            expires_at=expiry,
        )
        db.insert(row)
        return {**_receipt(row), "changed": True}


def read_app_card_interaction(token, project_id, card_id, interaction_id):
    with DbSession.atomic() as db:
        principal, _ = _scope(db, token, project_id, card_id)
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction).where(
                AppCardInteraction.id == interaction_id,
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.app_key == principal.app_key,
            )
        ).first()
        if row is None:
            raise AppGovernanceDenied()
        return _receipt(row)


def cancel_app_card_interaction(token, project_id, card_id, interaction_id, expected_revision):
    """Retain the immutable request and fence cancellation against its revision."""
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("A positive interaction revision is required")
    with DbSession.atomic() as db:
        principal, _ = _scope(db, token, project_id, card_id)
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.id == interaction_id,
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.app_key == principal.app_key,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise AppGovernanceDenied()
        if _receipt(row)["state"] == "answered":
            raise AppCardInteractionConflict()
        if row.state == "cancelled" and row.revision == expected_revision + 1:
            return {**_receipt(row), "changed": False}
        if row.revision != expected_revision or row.state != "pending":
            raise AppCardInteractionConflict()
        if _expired(row.expires_at, SafeDateTime.now()):
            # Expiry is already authoritative; cancellation cannot change it.
            return {**_receipt(row), "changed": False}
        row.state = "cancelled"
        row.revision += 1
        db.update(row)
        return {**_receipt(row), "changed": True}


def respond_to_app_card_interaction(
    service,
    actor,
    project_id,
    card_id,
    interaction_id,
    expected_revision,
    idempotency_key,
    values,
    execution_generation=None,
    channel=None,
    *,
    _reaction_event_id=None,
    _retained_response=False,
):
    """Accept one current human answer; delivery and workflow outcomes are separate."""
    import re
    from ...core.security.CollaborationChannel import CollaborationChannel
    from ...core.types import SnowflakeID
    from ..models import AppExecutionRequest, ProjectAssignedUser
    from .AppConnectionAuthentication import AuthenticatedAppConnection, _connection
    from .AppExecutionGrant import evaluate_connection_execution_grant

    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("A positive interaction revision is required")
    if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key):
        raise ValueError("Invalid response idempotency key")
    if execution_generation is not None and (type(execution_generation) is not int or execution_generation < 1):
        raise ValueError("Invalid execution generation")
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        # Match app mutations: project/card locks precede user and request locks.
        project = _current(db, Project, project_id)
        card = _current(db, Card, card_id)
        current = _current(db, User, actor.id)
        if (
            current is None
            or current.deleted_at
            or not current.activated_at
            or project is None
            or project.deleted_at
            or card is None
            or card.project_id != project_id
            or card.deleted_at
            or card.archived_at
        ):
            raise AppGovernanceDenied()
        if (
            service.card.resolve_readable_card(project_id, card_id, current, channel or CollaborationChannel.Api)
            is None
        ):
            raise AppGovernanceDenied()
        if project.owner_id != current.id:
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == current.id
                )
            ).first()
            role = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.project_id == project.id, ProjectRole.user_id == current.id
                )
            ).first()
            if member is None or role is None or not role.is_granted("card_update"):
                raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.id == interaction_id,
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.card_id == card_id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise AppGovernanceDenied()
        connection = _connection(db, row.connection_id)
        principal = AuthenticatedAppConnection(
            connection.app_key, connection.id, 0, connection.owner_id, connection.organization_id
        )
        _scope_principal(db, principal, project_id, card_id)
        if row.app_key != principal.app_key:
            raise AppGovernanceDenied()
        if row.request["presentation"]["type"] == "comment_reaction" and not _retained_response:
            from ..models import CardCommentReaction

            reaction = (
                _current(db, CardCommentReaction, _reaction_event_id, for_update=False) if _reaction_event_id else None
            )
            comment = _current(db, CardComment, reaction.comment_id, for_update=False) if reaction else None
            presentation = row.request["presentation"]
            mapped = presentation["reactions"].get(reaction.reaction_type) if reaction else None
            question = row.request["questions"][0]
            if (
                reaction is None
                or comment is None
                or comment.deleted_at
                or comment.card_id != card_id
                or comment.get_uid() != presentation["comment_uid"]
                or reaction.user_id != current.id
                or reaction.bot_id is not None
                or reaction.created_at <= row.created_at
                or mapped is None
                or values != {question["id"]: {"choice_id": mapped}}
                or idempotency_key != f"reaction-{reaction.get_uid()}"
            ):
                raise AppGovernanceDenied()
        context = row.request.get("execution")
        if context:
            if execution_generation != context["generation"]:
                raise AppCardInteractionConflict()
            execution = _current(db, AppExecutionRequest, SnowflakeID.from_short_code(context["request_uid"]))
            if (
                execution is None
                or execution.card_id != card_id
                or execution.project_id != project_id
                or execution.app_key != row.app_key
                or execution.connection_id != row.connection_id
                or execution.generation != execution_generation
            ):
                raise AppGovernanceDenied()
            authority = evaluate_connection_execution_grant(
                row.connection_id, project_id, card_id, execution_generation
            )
            if authority["authority_version"] != execution.authority.get("authority_version"):
                raise AppCardInteractionConflict()
        elif execution_generation is not None:
            raise AppCardInteractionConflict()
        validated = validate_card_interaction_values(row.request, values)
        answer = db.exec(
            SqlBuilder.select.table(AppCardInteractionResponse).where(
                AppCardInteractionResponse.interaction_id == row.id
            )
        ).first()
        if answer:
            if (
                answer.actor_id != current.id
                or answer.expected_revision != expected_revision
                or answer.idempotency_key != idempotency_key
                or answer.values != validated
                or answer.execution_generation != execution_generation
            ):
                raise AppCardInteractionConflict()
            changed = False
        else:
            if (
                row.revision != expected_revision
                or row.state != "pending"
                or _expired(row.expires_at, SafeDateTime.now())
            ):
                raise AppCardInteractionConflict()
            _validate_workflow_snapshot(db, row, validated)
            answer = AppCardInteractionResponse(
                interaction_id=row.id,
                actor_id=current.id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                values=validated,
                execution_generation=execution_generation,
            )
            db.insert(answer)
            row.revision += 1
            db.update(row)
            _queue_response_event(db, row, answer)
            changed = True
        return {
            "schema_version": 1,
            "interaction_uid": row.get_uid(),
            "response_uid": answer.get_uid(),
            "actor_uid": current.get_uid(),
            "revision": row.revision,
            "state": "answered",
            "values": answer.values,
            "changed": changed,
            "execution_resumed": False,
            "workflow_transitioned": False,
            "workflow_plan": _selected_workflow_plan(row, answer.values),
            **_response_delivery(db, answer),
        }


def _response_delivery(db, answer):
    from ..models import AppExecutionAcknowledgment, AppExecutionOutbox
    from .AppEventDestination import APP_INTERACTION_RESPONSE_EVENT

    event = db.exec(
        SqlBuilder.select.table(AppExecutionOutbox).where(
            AppExecutionOutbox.request_id == answer.id, AppExecutionOutbox.event_type == APP_INTERACTION_RESPONSE_EVENT
        )
    ).first()
    ack = (
        db.exec(
            SqlBuilder.select.table(AppExecutionAcknowledgment).where(
                AppExecutionAcknowledgment.request_id == answer.id
            )
        ).first()
        if event
        else None
    )
    return {
        "event_uid": event.get_uid() if event else None,
        "delivery_state": event.state if event else "not_queued",
        "acknowledgment_uid": ack.get_uid() if ack else None,
        "reception_state": "received" if ack else "not_received",
    }


def _queue_response_event(db, row, answer):
    from ..models import AppExecutionOutbox
    from .AppEventDestination import APP_INTERACTION_RESPONSE_EVENT, current_destination

    binding = db.exec(
        SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.project_id == row.project_id, BoardAppBinding.app_key == row.app_key
        )
    ).first()
    if "events.receive" not in binding.granted_capabilities:
        return
    connection = _current(db, AppConnection, row.connection_id)
    try:
        destination, _ = current_destination(db, connection, APP_INTERACTION_RESPONSE_EVENT)
    except (AppGovernanceDenied, AppGovernanceConflict):
        return
    event = AppExecutionOutbox(
        request_id=answer.id,
        project_id=row.project_id,
        app_key=row.app_key,
        connection_id=row.connection_id,
        event_type=APP_INTERACTION_RESPONSE_EVENT,
    )
    db.insert(event)
    event.payload = {
        "schema_version": 1,
        "event_uid": event.get_uid(),
        "event_type": event.event_type,
        "app_key": row.app_key,
        "connection_uid": SnowflakeID(row.connection_id).to_short_code(),
        "board_uid": SnowflakeID(row.project_id).to_short_code(),
        "card_uid": SnowflakeID(row.card_id).to_short_code(),
        "interaction_uid": row.get_uid(),
        "response_uid": answer.get_uid(),
        "actor_uid": SnowflakeID(answer.actor_id).to_short_code(),
        "values": answer.values,
        "execution": row.request.get("execution"),
        "state": "answered",
        "execution_resumed": False,
        "workflow_plan": _selected_workflow_plan(row, answer.values),
        "destination": {"destination_uid": destination.get_uid(), "revision": destination.revision},
    }
    db.update(event)

    def enqueue():
        from ...tasks.webhooks.AppEventDeliveryTask import app_event_delivery_task

        app_event_delivery_task(event.get_uid(), destination.revision)

    db.after_commit(enqueue)


def revalidate_response_event(db, event):
    """Current native responder and owning app authority, independent of transport."""
    answer = _current(db, AppCardInteractionResponse, event.request_id, for_update=False)
    row = _current(db, AppCardInteraction, answer.interaction_id, for_update=False) if answer else None
    if (
        row is None
        or event.connection_id != row.connection_id
        or event.app_key != row.app_key
        or event.project_id != row.project_id
        or event.payload.get("response_uid") != answer.get_uid()
        or event.payload.get("interaction_uid") != row.get_uid()
        or event.payload.get("card_uid") != SnowflakeID(row.card_id).to_short_code()
        or event.payload.get("event_uid") != event.get_uid()
        or event.payload.get("event_type") != event.event_type
        or event.payload.get("board_uid") != SnowflakeID(row.project_id).to_short_code()
        or event.payload.get("connection_uid") != SnowflakeID(row.connection_id).to_short_code()
        or event.payload.get("app_key") != row.app_key
        or event.payload.get("state") != "answered"
        or event.payload.get("execution_resumed") is not False
        or event.payload.get("values") != answer.values
        or event.payload.get("actor_uid") != SnowflakeID(answer.actor_id).to_short_code()
        or event.payload.get("execution") != row.request.get("execution")
        or (row.workflow_snapshot and event.payload.get("workflow_plan") != _selected_workflow_plan(row, answer.values))
    ):
        raise AppGovernanceDenied()
    actor = _current(db, User, answer.actor_id, for_update=False)
    respond_to_app_card_interaction(
        DomainService(),
        actor,
        row.project_id,
        row.card_id,
        row.id,
        answer.expected_revision,
        answer.idempotency_key,
        answer.values,
        answer.execution_generation,
        _retained_response=True,
    )
    binding = db.exec(
        SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.project_id == row.project_id, BoardAppBinding.app_key == row.app_key
        )
    ).first()
    if "events.receive" not in binding.granted_capabilities:
        raise AppGovernanceDenied()
    from .AppEventDestination import APP_INTERACTION_RESPONSE_EVENT, current_destination

    connection = _current(db, AppConnection, row.connection_id)
    destination, _ = current_destination(db, connection, APP_INTERACTION_RESPONSE_EVENT)
    if event.payload.get("destination") != {"destination_uid": destination.get_uid(), "revision": destination.revision}:
        from .AppGovernance import AppGovernanceConflict

        raise AppGovernanceConflict()
    return row, answer


def acknowledge_app_interaction_response(token, project_id, card_id, interaction_id, event_id, runtime_reference):
    """Acknowledge event reception only; never imply provider execution or resume."""
    from ..models import AppExecutionAcknowledgment, AppExecutionOutbox
    from .AppEventDestination import APP_INTERACTION_RESPONSE_EVENT

    if not isinstance(runtime_reference, str) or not runtime_reference.strip() or len(runtime_reference) > 200:
        raise ValueError("Runtime reference must contain 1 to 200 characters")
    with DbSession.atomic() as db:
        event = _current(db, AppExecutionOutbox, event_id)
        principal, _ = _scope(db, token, project_id, card_id)
        row = _current(db, AppCardInteraction, interaction_id, for_update=False)
        if (
            event is None
            or row is None
            or event.event_type != APP_INTERACTION_RESPONSE_EVENT
            or row.project_id != project_id
            or row.card_id != card_id
            or row.connection_id != principal.connection_id
            or row.app_key != principal.app_key
            or event.payload.get("interaction_uid") != row.get_uid()
            or event.connection_id != principal.connection_id
            or event.app_key != principal.app_key
            or event.state not in ("delivering", "delivered")
            or (event.state == "delivering" and _expired(event.lease_until, SafeDateTime.now()))
        ):
            raise AppGovernanceDenied()
        _, answer = revalidate_response_event(db, event)
        ack = db.exec(
            SqlBuilder.select.table(AppExecutionAcknowledgment)
            .where(AppExecutionAcknowledgment.request_id == answer.id)
            .with_for_update()
        ).first()
        changed = ack is None
        if ack is None:
            ack = AppExecutionAcknowledgment(
                request_id=answer.id,
                event_id=event.id,
                connection_id=row.connection_id,
                app_key=row.app_key,
                runtime_reference=runtime_reference,
            )
            db.insert(ack)
        elif ack.event_id != event.id or ack.runtime_reference != runtime_reference:
            raise AppCardInteractionConflict()
        return {
            "schema_version": 1,
            "interaction_uid": row.get_uid(),
            "response_uid": answer.get_uid(),
            "event_uid": event.get_uid(),
            "acknowledgment_uid": ack.get_uid(),
            "runtime_reference": ack.runtime_reference,
            "state": "received",
            "changed": changed,
            "execution_resumed": False,
        }


def accept_current_comment_reaction(service, actor, project_id, card_id, comment_id, reaction_type):
    """Resolve only a newly stored human reaction; normal reactions retain their UX."""
    from ..models import CardCommentReaction

    if not isinstance(actor, User):
        return None
    with DbSession.atomic() as db:
        candidates = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.state == "pending",
                AppCardInteraction.expires_at > SafeDateTime.now(),
                ~select(AppCardInteractionResponse.id)
                .where(AppCardInteractionResponse.interaction_id == AppCardInteraction.id)
                .exists(),
                AppCardInteraction.request["presentation"]["type"].as_string() == "comment_reaction",
                AppCardInteraction.request["presentation"]["comment_uid"].as_string()
                == SnowflakeID(comment_id).to_short_code(),
            )
            .order_by(AppCardInteraction.id.desc())
            .limit(2)
        ).all()
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
        if reaction is None:
            return None
        row = candidates[0]
        choice = row.request["presentation"]["reactions"].get(reaction_type)
        if choice is None or _expired(row.expires_at, SafeDateTime.now()):
            return None
        try:
            return respond_to_app_card_interaction(
                service,
                actor,
                project_id,
                card_id,
                row.id,
                row.revision,
                f"reaction-{reaction.get_uid()}",
                {row.request["questions"][0]["id"]: {"choice_id": choice}},
                row.request.get("execution", {}).get("generation"),
                _reaction_event_id=reaction.id,
            )
        except (AppGovernanceDenied, AppCardInteractionConflict):
            return None
