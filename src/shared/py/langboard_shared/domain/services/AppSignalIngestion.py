"""Signed external observations. No card creation, triage or workflow mutation."""

import hashlib
import json
import re
from datetime import datetime, timezone
from langboard_sdk.webhooks import verify_webhook
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ...publishers import CardPublisher
from ..models import (
    AppConnection,
    AppConnectionCredential,
    AppDefinition,
    AppResourceBinding,
    AppSignal,
    BoardAppBinding,
    User,
)
from ..models.ProjectRole import ProjectRoleAction
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppGovernance import AppGovernanceDenied, require_connection_access


class AppSignalConflict(Exception):
    pass


def receive_external_signal(service, token, project_uid, body, headers):
    if not isinstance(body, bytes) or len(body) > 65536:
        raise ValueError("Signal envelope exceeds the byte limit")
    with DbSession.atomic() as db:
        principal = authenticate_connection_credential(token)
        verify_webhook(body, headers, token)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate signal field")
                result[key] = value
            return result
        envelope = json.loads(body, object_pairs_hook=unique)
        fields = {"version", "board_uid", "resource_uid", "app_revision", "binding_revision",
                  "event_id", "event_type", "occurred_at", "external_id", "outcome", "commit_sha"}
        if (not isinstance(envelope, dict) or set(envelope) != fields or type(envelope["version"]) is not int
            or envelope["version"] != 1 or envelope["board_uid"] != project_uid):
            raise ValueError("Invalid signal envelope scope/schema")
        for field, maximum in (("event_id", 128), ("external_id", 200), ("event_type", 64), ("outcome", 64), ("occurred_at", 40)):
            value = envelope[field]
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError("Invalid bounded signal field")
        stamp = datetime.fromisoformat(envelope["occurred_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Signal occurrence needs a timezone")
        owner = db.exec(SqlBuilder.select.table(User).where(User.id == principal.owner_id)).first()
        project = service.workflow_stage._authorized_app_board(owner, project_uid, ProjectRoleAction.Update, lock=True)
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == principal.connection_id).with_for_update()).first()
        if project is None or connection is None:
            raise AppGovernanceDenied()
        # Serialize credential revocation through the same native credential row.
        db.exec(SqlBuilder.select.table(AppConnectionCredential).where(
            AppConnectionCredential.id == principal.credential_id,
        ).with_for_update()).first()
        authenticate_connection_credential(token)
        require_connection_access(db, owner, project, connection, unattended=True)
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key).with_for_update()).first()
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == principal.app_key,
        ).with_for_update()).first()
        if (definition is None or not definition.is_enabled or binding is None or binding.state != "enabled"
            or "signals.publish" not in definition.declaration.get("capabilities", [])
            or "signals.publish" not in binding.granted_capabilities):
            raise AppGovernanceDenied()
        if definition.edit_revision() != envelope["app_revision"] or binding.edit_revision() != envelope["binding_revision"]:
            raise AppSignalConflict()
        policy = definition.declaration.get("signal_policy")
        if (policy is None or envelope["event_type"] not in policy["event_types"]
            or envelope["outcome"] not in policy["outcome_states"]):
            raise ValueError("Signal event/outcome is not declared")
        commit = envelope["commit_sha"]
        if (not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})?", commit)
            or policy["commit_mode"] == "empty" and commit or policy["commit_mode"] == "required" and not commit):
            raise ValueError("Signal commit violates declared semantics")
        resource = db.exec(SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.id == SnowflakeID.from_short_code(envelope["resource_uid"]),
            AppResourceBinding.board_binding_id == binding.id, AppResourceBinding.connection_id == connection.id,
            AppResourceBinding.resource_type.in_(policy["resource_types"]),
            AppResourceBinding.is_selected == True, AppResourceBinding.access_state == "granted",  # noqa: E712
        ).with_for_update()).first()
        if resource is None or not connection.credential_reference:
            raise AppGovernanceDenied()
        try:
            secret = service.secret_reference._find(owner, connection.credential_reference, lock=True)
        except Exception:
            raise AppGovernanceDenied() from None
        if secret.state != "active":
            raise AppGovernanceDenied()
        values = {field: envelope[field] for field in ("event_id", "event_type", "external_id", "outcome", "commit_sha")}
        values.update(provider=principal.app_key, occurred_at=stamp.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                      payload_digest=hashlib.sha256(body).hexdigest())
        existing = db.exec(SqlBuilder.select.table(AppSignal).where(
            AppSignal.resource_id == resource.id, AppSignal.event_id == values["event_id"],
        )).first()
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise AppSignalConflict()
            return {"signal_uid": existing.get_uid(), "duplicate": True}
        signal = AppSignal(resource_id=resource.id, **values)
        db.insert(signal)
        db.after_commit(lambda: CardPublisher.app_signal_changed(project_uid))
        return {"signal_uid": signal.get_uid(), "duplicate": False}
