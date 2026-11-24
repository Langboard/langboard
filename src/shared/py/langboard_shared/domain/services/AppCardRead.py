"""Bounded current card content; read permission never grants execution authority."""

from ...core.db import DbSession
from ...core.types import SnowflakeID
from .AppCardScope import require_app_card_scope
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionGrant import AppExecutionCredentialDenied
from .AppGovernance import AppGovernanceDenied, lock_policy_mutation
from .ExecutionGeneration import execution_generations


def read_app_card(token, project_id, card_id, *, offset=0, limit=16000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
        raise ValueError("Invalid card content window")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        try:
            principal = authenticate_connection_credential(token)
        except AppGovernanceDenied as exc:
            raise AppExecutionCredentialDenied() from exc
        card, owner, selection, binding, resources = require_app_card_scope(
            db, principal, project_id, card_id, capability="cards.read", role_action="read"
        )
        content = card.description.content
        end = offset + limit
        return {
            "schema_version": 1,
            "board_uid": card.project_id.to_short_code(),
            "card_uid": card.get_uid(),
            "title": card.title,
            "card_revision": card.last_change_seq,
            "execution_generation": execution_generations([card_id]).get(int(card_id), 0),
            "description": {"content": content[offset:end], "format": "markdown",
                            "total_chars": len(content), "next_offset": end if end < len(content) else None},
            "source": {"app_key": principal.app_key, "connection_uid": SnowflakeID(principal.connection_id).to_short_code()},
            "ownership_revision": owner.revision,
            "selection_revision": selection.revision,
            "binding_revision": binding.edit_revision(),
            "resources": [{"resource_uid": row.get_uid(), "resource_type": row.resource_type,
                           "external_resource_id": row.external_resource_id,
                           "access_revision": row.access_revision} for row in sorted(resources, key=lambda row: row.get_uid())],
        }
