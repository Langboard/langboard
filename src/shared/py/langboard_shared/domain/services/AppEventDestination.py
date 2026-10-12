"""Bind existing administrator-managed webhook settings to an approved app origin."""

from hashlib import sha256
from json import dumps
from urllib.parse import urlsplit
from langboard_sdk.governance import declaration_trust_fingerprint
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ...tasks.webhooks.utils import validate_webhook_url
from ..models import AppConnection, AppDefinition, AppEventDestination, User, WebhookSetting
from .AppConnectionAuthentication import _connection
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _current, lock_policy_mutation


APP_EXECUTION_EVENT = "io.langboard.app.execution.requested.v1"
APP_INTERACTION_RESPONSE_EVENT = "io.langboard.app.interaction.answered.v1"
APP_CARD_ARCHIVE_EVENT = "io.langboard.app.card.archived.v1"
APP_EVENTS = {APP_EXECUTION_EVENT, APP_INTERACTION_RESPONSE_EVENT, APP_CARD_ARCHIVE_EVENT}


def _target_revision(setting):
    return sha256(dumps([setting.url, setting.secret_id, setting.events], sort_keys=True).encode()).hexdigest()


def current_destination(db, connection, event_type=None):
    connection = _connection(db, connection.id)
    row = db.exec(
        SqlBuilder.select.table(AppEventDestination)
        .where(
            AppEventDestination.connection_id == connection.id,
        )
        .with_for_update()
    ).first()
    setting = _current(db, WebhookSetting, row.webhook_id) if row else None
    definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == connection.app_key)).first()
    if (
        row is None
        or not row.is_enabled
        or setting is None
        or not setting.secret_id
        or definition is None
        or not definition.is_enabled
    ):
        raise AppGovernanceDenied()
    declaration = definition.declaration
    if row.target_revision != _target_revision(setting) or row.trust_revision != declaration_trust_fingerprint(
        declaration
    ):
        raise AppGovernanceConflict()
    if "events.receive" not in declaration.get("capabilities", []) or not (
        ({event_type} if event_type else APP_EVENTS) & set(setting.events or [])
    ):
        raise AppGovernanceDenied()
    try:
        url = urlsplit(validate_webhook_url(setting.url))
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Invalid app event destination")
        origin = f"https://{url.hostname}" + (f":{url.port}" if url.port and url.port != 443 else "")
    except ValueError as exc:
        raise AppGovernanceDenied() from exc
    if origin not in declaration.get("trust", {}).get("data_origins", []):
        raise AppGovernanceDenied()
    return row, setting


def bind_app_event_destination(actor, connection_id, webhook_id, expected_revision):
    """Internal host configuration. Existing webhook secrets are instance-admin managed."""
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        raise ValueError("Invalid destination revision")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        current = _actor(db, actor)
        if not current.is_admin:
            raise AppGovernanceDenied()
        connection = _current(db, AppConnection, connection_id)
        if connection is None or connection.state != "connected":
            raise AppGovernanceDenied()
        setting = _current(db, WebhookSetting, webhook_id)
        definition = db.exec(
            SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == connection.app_key)
        ).first()
        if setting is None or definition is None:
            raise AppGovernanceDenied()
        target_revision = _target_revision(setting)
        trust_revision = declaration_trust_fingerprint(definition.declaration)
        row = db.exec(
            SqlBuilder.select.table(AppEventDestination)
            .where(
                AppEventDestination.connection_id == connection.id,
            )
            .with_for_update()
        ).first()
        if (row is None and expected_revision is not None) or (row is not None and row.revision != expected_revision):
            raise AppGovernanceConflict()
        if row is None:
            row = AppEventDestination(
                connection_id=connection.id,
                webhook_id=webhook_id,
                target_revision=target_revision,
                trust_revision=trust_revision,
            )
            db.insert(row)
        elif not row.is_enabled or (row.webhook_id, row.target_revision, row.trust_revision) != (
            webhook_id,
            target_revision,
            trust_revision,
        ):
            row.is_enabled = True
            row.webhook_id = webhook_id
            row.target_revision = target_revision
            row.trust_revision = trust_revision
            row.revision += 1
            db.update(row)
        current_destination(db, connection)
        return {"revision": row.revision, "destination_uid": row.get_uid(), "enabled": row.is_enabled}


def prepare_app_event_signature(event_id, expected_destination_revision, *, timestamp=None, claim_token=None):
    """Prepare exact signed bytes without sending or claiming execution authority."""
    from ...core.security import KeyVault
    from ...tasks.webhooks.WebhookTask import sign_webhook_bytes
    from ..models import AppExecutionOutbox

    if type(expected_destination_revision) is not int or expected_destination_revision < 1:
        raise ValueError("Invalid destination revision")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        event = _current(db, AppExecutionOutbox, event_id, for_update=False)
        if event is None or event.event_type not in APP_EVENTS or not _claim_matches(event, claim_token):
            raise AppGovernanceDenied()
        connection = _current(db, AppConnection, event.connection_id)
        if connection is None or connection.app_key != event.app_key:
            raise AppGovernanceDenied()
        observed_payload = event.payload
        validate_event_authority(db, event, connection)
        event = _current(db, AppExecutionOutbox, event_id)
        if event is None or not _claim_matches(event, claim_token) or event.payload != observed_payload:
            raise AppGovernanceConflict()
        destination, setting = current_destination(db, connection, event.event_type)
        if destination.revision != expected_destination_revision:
            raise AppGovernanceConflict()
        snapshot = {"destination_uid": destination.get_uid(), "revision": destination.revision}
        if event.payload.get("destination") is not None and event.payload["destination"] != snapshot:
            raise AppGovernanceConflict()
        secret = KeyVault.get_key(setting.secret_id)
        if not secret:
            raise AppGovernanceDenied()
        if event.payload.get("destination") is None:
            event.payload = {**event.payload, "destination": snapshot}
            db.update(event)
        body = dumps(event.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        body, headers = sign_webhook_bytes(body, event.get_uid(), secret, timestamp=timestamp)
        return {"body": body, "headers": headers, "url": setting.url, "destination_revision": destination.revision}


def validate_event_authority(db, event, connection):
    """Recheck native immutable request authority before exposing signed bytes."""
    from ..models import AppCardInteraction, AppCardInteractionResponse, CardExecutionIntent
    from .CardAppResources import verified_card_app_configuration
    from .CardExecutionIntent import _configuration, _generation

    owner = _current(db, User, connection.owner_id, for_update=False)
    if owner is None:
        raise AppGovernanceDenied()
    try:
        card_id = SnowflakeID.from_short_code(event.payload["card_uid"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AppGovernanceDenied() from exc
    if event.event_type == APP_INTERACTION_RESPONSE_EVENT:
        answer = _current(db, AppCardInteractionResponse, event.request_id, for_update=False)
        row = _current(db, AppCardInteraction, answer.interaction_id, for_update=False) if answer else None
        if (
            row is None
            or row.app_key != event.app_key
            or row.connection_id != connection.id
            or row.card_id != card_id
            or row.project_id != event.project_id
        ):
            raise AppGovernanceDenied()
        _, binding, _, _, snapshot = verified_card_app_configuration(
            db, owner, event.project_id, card_id, event.app_key, connection.id, capability="interactions.request"
        )
        row = _current(db, AppCardInteraction, row.id)
        answer = _current(db, AppCardInteractionResponse, answer.id)
        if snapshot != row.authority_snapshot:
            raise AppGovernanceConflict()
        if "events.receive" not in binding.granted_capabilities:
            raise AppGovernanceDenied()
        expected = dict(
            schema_version=1,
            event_uid=event.get_uid(),
            event_type=event.event_type,
            app_key=row.app_key,
            connection_uid=connection.get_uid(),
            board_uid=SnowflakeID(event.project_id).to_short_code(),
            card_uid=SnowflakeID(card_id).to_short_code(),
            interaction_uid=row.get_uid(),
            response_uid=answer.get_uid(),
            actor_uid=SnowflakeID(answer.actor_id).to_short_code(),
            values=answer.values,
            execution=row.request.get("execution"),
            state="answered",
            execution_resumed=False,
            workflow_transitioned=False,
        )
        if {key: value for key, value in event.payload.items() if key != "destination"} != expected:
            raise AppGovernanceConflict()
        context = row.request.get("execution")
        if not context:
            return
        request_id = SnowflakeID.from_short_code(context["request_uid"])
    elif event.event_type == APP_EXECUTION_EVENT:
        request_id = event.request_id
    else:
        # Archive events require their native lifecycle receipt before support.
        raise AppGovernanceDenied()
    intent = _current(db, CardExecutionIntent, request_id, for_update=False)
    if (
        intent is None
        or intent.app_key != event.app_key
        or intent.connection_id != connection.id
        or intent.card_id != card_id
    ):
        raise AppGovernanceDenied()
    actor = _current(db, User, intent.actor_id)
    _, _, snapshot = _configuration(db, actor, event.project_id, card_id, event.app_key, connection.id)
    intent = _current(db, CardExecutionIntent, request_id)
    generation = _generation(db, card_id)
    if (
        generation is None
        or generation.execution_generation != intent.execution_generation
        or snapshot != intent.snapshot
    ):
        raise AppGovernanceConflict()
    if event.event_type == APP_EXECUTION_EVENT:
        expected = dict(
            schema_version=1,
            event_uid=event.get_uid(),
            event_type=event.event_type,
            app_key=intent.app_key,
            connection_uid=connection.get_uid(),
            board_uid=SnowflakeID(event.project_id).to_short_code(),
            card_uid=SnowflakeID(card_id).to_short_code(),
            request_uid=intent.get_uid(),
            generation=intent.execution_generation,
            resource_uids=sorted(snapshot["resource_revisions"]),
            state="requested",
            started=False,
        )
        if {key: value for key, value in event.payload.items() if key != "destination"} != expected:
            raise AppGovernanceConflict()


def manage_app_event_destination(actor, connection_id, *, expected_revision=None, disable=False):
    """Administrator-only status and explicit disable; never return a secret."""
    with DbSession.atomic() as db:
        if disable:
            lock_policy_mutation(db)
        current = _actor(db, actor, for_update=disable)
        if not current.is_admin:
            raise AppGovernanceDenied()
        query = SqlBuilder.select.table(AppEventDestination).where(AppEventDestination.connection_id == connection_id)
        row = db.exec(query.with_for_update() if disable else query).first()
        if row is None:
            raise AppGovernanceDenied()
        if disable:
            if type(expected_revision) is not int or expected_revision != row.revision:
                raise AppGovernanceConflict()
            if row.is_enabled:
                row.is_enabled = False
                row.revision += 1
                db.update(row)
        return {
            "destination_uid": row.get_uid(),
            "revision": row.revision,
            "enabled": row.is_enabled,
            "webhook_uid": SnowflakeID(row.webhook_id).to_short_code(),
        }


def _claim_matches(event, token):
    from ...core.types import SafeDateTime

    return (
        event.state == "pending"
        if token is None
        else event.state == "delivering"
        and event.claim_token == token
        and event.claim_expires_at is not None
        and event.claim_expires_at > SafeDateTime.now()
    )
