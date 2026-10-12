"""Bounded current owning-app content; never execution authorization."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppResourceBinding, User
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppGovernance import AppGovernanceDenied, _actor, _current
from .CardAppResources import verified_card_app_configuration
from .CardExecutionIntent import _generation


def read_app_card(token, project_id, card_id, *, offset=0, limit=16000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
        raise ValueError("Invalid card content window")
    with DbSession.atomic() as db:
        principal = authenticate_connection_credential(token)
        actor = _current(db, User, principal.owner_id, for_update=False)
        if actor is None:
            raise AppGovernanceDenied()
        actor = _actor(db, actor, for_update=False)
        _, _, card, connection, snapshot = verified_card_app_configuration(
            db,
            actor,
            project_id,
            card_id,
            principal.app_key,
            principal.connection_id,
            capability="cards.read",
            for_update=False,
        )
        generation = _generation(db, card.id, for_update=False)
        content = card.description.content
        resources = db.exec(
            SqlBuilder.select.table(AppResourceBinding).where(
                AppResourceBinding.id.in_([SnowflakeID.from_short_code(uid) for uid in snapshot["resource_revisions"]])
            )
        ).all()
        if {row.get_uid(): row.access_revision for row in resources} != snapshot["resource_revisions"]:
            raise AppGovernanceDenied()
        resources.sort(key=lambda row: row.get_uid())
        end = min(offset + limit, len(content))
        return {
            "schema_version": 1,
            "execution_generation": generation.execution_generation if generation else 0,
            "board_uid": SnowflakeID(project_id).to_short_code(),
            "card_uid": card.get_uid(),
            "source": {"app_key": principal.app_key, "connection_uid": connection.get_uid()},
            "title": card.title,
            "card_revision": int(SafeDateTime.fromisoformat(snapshot["card_revision"]).timestamp() * 1000000),
            "ownership_revision": snapshot["ownership_revision"],
            "selection_revision": snapshot["selection_revision"],
            "binding_revision": snapshot["binding_revision"],
            "resources": [
                {
                    "resource_uid": row.get_uid(),
                    "resource_type": row.resource_type,
                    "external_resource_id": row.external_resource_id,
                    "access_revision": row.access_revision,
                }
                for row in resources
            ],
            "description": {
                "format": "markdown",
                "content": content[offset:end],
                "total_chars": len(content),
                "next_offset": end if end < len(content) else None,
            },
        }
