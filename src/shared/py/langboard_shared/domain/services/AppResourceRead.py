"""Verified resource receipts for one authenticated app; never execution grants."""

from ...core.db import DbSession, SqlBuilder
from ..models import AppDefinition, AppResourceBinding, BoardAppBinding, Project, ProjectAssignedUser, ProjectRole
from ..models.ProjectRole import ProjectRoleAction
from .AppConnectionAuthentication import _connection, _identity_hash, authenticate_connection_credential
from .AppGovernance import AppGovernanceDenied, _current, current_policy


def list_app_resources(token, project_id, *, after_id=None, limit=25):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Resource page limit must be 1 to 50")
    with DbSession.atomic() as db:
        principal = authenticate_connection_credential(token)
        connection = _connection(db, principal.connection_id)
        project = _current(db, Project, project_id, for_update=False)
        if project is None or project.deleted_at:
            raise AppGovernanceDenied()
        if connection.ownership == "organization":
            if project.organization_id is None or project.organization_id != connection.organization_id:
                raise AppGovernanceDenied()
        elif project.owner_id != connection.owner_id:
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == connection.owner_id
                )
            ).first()
            role = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.project_id == project.id, ProjectRole.user_id == connection.owner_id
                )
            ).first()
            if member is None or role is None or not role.is_granted(ProjectRoleAction.Read):
                raise AppGovernanceDenied()
        definition = db.exec(
            SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key)
        ).first()
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == principal.app_key
            )
        ).first()
        if (
            definition is None
            or "resources.read" not in definition.declaration["capabilities"]
            or binding is None
            or binding.state != "enabled"
            or "resources.read" not in binding.granted_capabilities
        ):
            raise AppGovernanceDenied()
        if current_policy(db, project.organization_id)["effective_mode"] == "disabled":
            raise AppGovernanceDenied()
        identity = _identity_hash(db, connection, definition=definition)
        query = SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.board_binding_id == binding.id,
            AppResourceBinding.connection_id == connection.id,
            AppResourceBinding.is_selected.is_(True),
            AppResourceBinding.access_state == "granted",
            AppResourceBinding.identity_hash == identity,
            AppResourceBinding.resource_type.in_(definition.declaration["resource_types"]),
        )
        if after_id is not None:
            query = query.where(AppResourceBinding.id > after_id)
        rows = db.exec(query.order_by(AppResourceBinding.id).limit(limit + 1)).all()
        page = rows[:limit]
        return {
            "schema_version": 1,
            "connection_uid": connection.get_uid(),
            "binding_revision": binding.edit_revision(),
            "items": [
                {
                    "resource_uid": row.get_uid(),
                    "resource_type": row.resource_type,
                    "external_resource_id": row.external_resource_id,
                    "resource_path": row.resource_path,
                    "access_revision": row.access_revision,
                    "health": row.health,
                }
                for row in page
            ],
            "next_cursor": page[-1].get_uid() if len(rows) > limit else None,
        }
