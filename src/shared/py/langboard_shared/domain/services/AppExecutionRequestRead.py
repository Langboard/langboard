"""Connection-scoped SDK request receipt reads; never runtime authorization."""

import json
from hashlib import sha256
from ...core.db import DbSession
from ...core.types import SafeDateTime
from ..models import CardExecutionIntent, User
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppGovernance import AppGovernanceDenied, _actor, _current
from .CardExecutionIntent import _configuration, _generation


def read_app_execution_request(token, project_id, card_id, request_id, *, for_update=False):
    with DbSession.atomic() as db:
        principal = authenticate_connection_credential(token)
        intent = _current(db, CardExecutionIntent, request_id, for_update=False)
        if (
            intent is None
            or intent.card_id != card_id
            or intent.app_key != principal.app_key
            or intent.connection_id != principal.connection_id
        ):
            raise AppGovernanceDenied()
        actor = _current(db, User, intent.actor_id, for_update=for_update)
        if actor is None:
            raise AppGovernanceDenied()
        actor = _actor(db, actor, for_update=for_update)
        card, connection, snapshot = _configuration(
            db,
            actor,
            project_id,
            card_id,
            principal.app_key,
            principal.connection_id,
            for_update=for_update,
        )
        if for_update:
            intent = _current(db, CardExecutionIntent, request_id)
        generation = _generation(db, card_id, for_update=for_update)
        if (
            generation is None
            or generation.execution_generation != intent.execution_generation
            or snapshot != intent.snapshot
        ):
            raise AppGovernanceDenied()
        authority = {
            "schema_version": 1,
            "card_uid": card.get_uid(),
            "app_key": intent.app_key,
            "connection_uid": connection.get_uid(),
            "generation": intent.execution_generation,
            "resource_uids": sorted(snapshot["resource_revisions"]),
            "resource_revisions": snapshot["resource_revisions"],
            "card_revision": int(SafeDateTime.fromisoformat(snapshot["card_revision"]).timestamp() * 1000000),
            "ownership_revision": snapshot["ownership_revision"],
            "selection_revision": snapshot["selection_revision"],
            "binding_revision": snapshot["binding_revision"],
            "authority_version": sha256(
                json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        return {
            "schema_version": 1,
            "request_uid": intent.get_uid(),
            "card_uid": card.get_uid(),
            "generation": intent.execution_generation,
            "state": "requested",
            "started": False,
            "authority": authority,
        }
