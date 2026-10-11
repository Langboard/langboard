"""Current card-bound MCP declarations; discovery is not remote execution."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import AppDefinition
from .AppCardScope import require_app_card_scope
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionGrant import AppExecutionCredentialDenied
from .AppGovernance import AppGovernanceDenied


def read_app_mcp_catalog(token, project_id, card_id):
    with DbSession.atomic() as db:
        try:
            principal = authenticate_connection_credential(token)
        except AppGovernanceDenied as exc:
            raise AppExecutionCredentialDenied() from exc
        card, _, selection, binding, resources = require_app_card_scope(
            db, principal, project_id, card_id, capability="cards.read", role_action="read"
        )
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key)).first()
        declared = definition.declaration
        if "resources.read" not in declared.get("capabilities", []) or "resources.read" not in binding.granted_capabilities:
            raise AppGovernanceDenied()
        extension = declared.get("mcp") or {}
        allowed = set(declared.get("capabilities", [])) & set(binding.granted_capabilities)
        connection_uid = SnowflakeID(principal.connection_id).to_short_code()
        return {"schema_version": 1, "board_uid": card.project_id.to_short_code(), "card_uid": card.get_uid(),
                "app_key": principal.app_key, "connection_uid": connection_uid,
                "identity": extension.get("identity"), "binding_revision": binding.edit_revision(),
                "selection_revision": selection.revision,
                "resources": [{"resource_uid": row.get_uid(), "access_revision": row.access_revision}
                              for row in sorted(resources, key=lambda row: row.get_uid())],
                "tools": [{**tool, "qualified_name": f"{principal.app_key}:{connection_uid}:{tool['name']}",
                           "callable": False} for tool in extension.get("tools", [])
                          if f"mcp.{tool['authority_scope']}" in allowed],
                "execution_supported": False}
