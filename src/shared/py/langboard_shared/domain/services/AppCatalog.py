"""User discovery is a bounded projection, never an installation or connection grant."""

import re
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import AppDefinition
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import _actor, _authorized_board, current_policy


def list_catalog(actor, *, project_uid=None, after_key=None, limit=25):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Catalog page limit must be 1 to 50")
    if after_key is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", after_key):
        raise ValueError("Invalid catalog cursor")
    project_id = None
    if project_uid is not None:
        if not isinstance(project_uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", project_uid):
            raise ValueError("Invalid project identifier")
        project_id = SnowflakeID.from_short_code(project_uid)
        if not project_id or project_id.to_short_code() != project_uid:
            raise ValueError("Invalid project identifier")
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=False)
        organization_id = None
        if project_id is not None:
            project = _authorized_board(db, actor, project_id, for_update=False, action=ProjectRoleAction.Read)
            organization_id = project.organization_id
        policy = current_policy(db, organization_id)
        result = {"items": [], "next_cursor": None, "effective_mode": policy["effective_mode"],
                  "can_manage_installation": actor.is_admin}
        if policy["effective_mode"] == "disabled":
            return result
        statement = SqlBuilder.select.table(AppDefinition).where(AppDefinition.is_enabled.is_(True))
        if after_key is not None:
            statement = statement.where(AppDefinition.key > after_key)
        rows = db.exec(statement.order_by(AppDefinition.key).limit(limit + 1)).all()
        page = rows[:limit]
        # Do not serialize declarations, storage locators, trust targets or personal connections.
        result["items"] = [
            {"key": row.key, "name": row.declaration["name"], "version": row.declaration["version"],
             "description": row.declaration["description"],
             "capabilities": row.declaration["capabilities"],
             "resource_types": row.declaration["resource_types"], "is_installed": True}
            for row in page
        ]
        result["next_cursor"] = page[-1].key if len(rows) > limit else None
        return result
