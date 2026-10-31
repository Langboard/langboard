"""Primary database authority for incoming, user-owned native MCP grants."""

from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ..models import NativeMcpBoardConsent, NativeMcpConnection, Project, ProjectAssignedUser, ProjectRole, User
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, lock_policy_mutation


SCOPES = ("read", "write")


def _current_user(db, actor):
    user = db.exec(
        SqlBuilder.select.table(User).where(User.id == actor.id).execution_options(populate_existing=True)
    ).first()
    if user is None or user.deleted_at or not user.activated_at:
        raise AppGovernanceDenied()
    return user


def positively_internal(actor):
    from .DomainService import DomainService

    with DbSession.use(readonly=False) as db:
        user = _current_user(db, actor)
    service = DomainService()
    try:
        return service.scim_provisioning.classify_members([user.id]).get(user.get_uid()) == "internal"
    finally:
        service.close()


def connect_native_client(actor, client_id):
    """Only called after full token verification and active linked-user resolution."""
    if not isinstance(client_id, str) or not client_id.strip():
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        user = _current_user(db, actor)
        db.exec(SqlBuilder.select.table(User).where(User.id == user.id).with_for_update()).first()
        connection = db.exec(
            SqlBuilder.select.table(NativeMcpConnection).where(
                NativeMcpConnection.user_id == user.id, NativeMcpConnection.client_id == client_id
            )
        ).first()
        if connection is None:
            connection = NativeMcpConnection(user_id=user.id, client_id=client_id)
            db.insert(connection)
        return connection


def list_native_connections(actor):
    with DbSession.use(readonly=False) as db:
        user = _current_user(db, actor)
        return [
            {"uid": row.get_uid(), "client_id": row.client_id}
            for row in db.exec(
                SqlBuilder.select.table(NativeMcpConnection)
                .where(NativeMcpConnection.user_id == user.id)
                .order_by(NativeMcpConnection.id)
            ).all()
        ]


def available_board_scopes(db, actor, project_id):
    user = _current_user(db, actor)
    project = db.exec(
        SqlBuilder.select.table(Project)
        .where(Project.id == project_id, Project.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    ).first()
    if project is None:
        return []
    member = db.exec(
        SqlBuilder.select.table(ProjectAssignedUser)
        .where(ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == user.id)
        .execution_options(populate_existing=True)
    ).first()
    # An instance administrator must still be a current board member.
    if not member and project.owner_id != user.id:
        return []
    if project.owner_id == user.id or user.is_admin:
        return list(SCOPES)
    role = db.exec(
        SqlBuilder.select.table(ProjectRole)
        .where(ProjectRole.project_id == project.id, ProjectRole.user_id == user.id)
        .execution_options(populate_existing=True)
    ).first()
    if role is None or not role.is_granted(ProjectRoleAction.Read):
        return []
    return (
        ["read", "write"]
        if any(
            role.is_granted(action)
            for action in (
                ProjectRoleAction.Update,
                ProjectRoleAction.CardWrite,
                ProjectRoleAction.CardUpdate,
                ProjectRoleAction.CardDelete,
            )
        )
        else ["read"]
    )


def _context(db, actor, connection_id, project_id):
    _current_user(db, actor)
    connection = db.exec(
        SqlBuilder.select.table(NativeMcpConnection)
        .where(NativeMcpConnection.id == connection_id, NativeMcpConnection.user_id == actor.id)
        .with_for_update()
    ).first()
    if connection is None:
        raise AppGovernanceDenied()
    row = db.exec(
        SqlBuilder.select.table(NativeMcpBoardConsent)
        .where(NativeMcpBoardConsent.connection_id == connection.id, NativeMcpBoardConsent.project_id == project_id)
        .with_for_update()
    ).first()
    available = available_board_scopes(db, actor, project_id)
    # Owners may inspect and revoke their existing grant even after board access
    # is removed. New grants still require current board authority below.
    if not available and row is None:
        raise AppGovernanceDenied()
    return connection, row, available


def _receipt(connection, project_id, row, available):
    """Report the owner's saved grant separately from current board authority."""
    from ...core.types import SnowflakeID

    return {
        "connection_uid": connection.get_uid(),
        "project_uid": SnowflakeID(project_id).to_short_code(),
        "revision": row.revision if row else None,
        "scopes": list(row.scopes) if row else [],
        "available_scopes": available,
    }


def read_native_board_consent(actor, connection_id, project_id):
    with DbSession.atomic() as db:
        connection, row, available = _context(db, actor, connection_id, project_id)
        return _receipt(connection, project_id, row, available)


def save_native_board_consent(actor, connection_id, project_id, scopes, expected_revision):
    if (
        not isinstance(scopes, list)
        or any(scope not in SCOPES for scope in scopes)
        or len(set(scopes)) != len(scopes)
        or (expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1))
    ):
        raise ValueError("Unique read/write scopes and current revision required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        connection, row, available = _context(db, actor, connection_id, project_id)
        if (row.revision if row else None) != expected_revision:
            raise AppGovernanceConflict()
        if not set(scopes).issubset(available):
            raise AppGovernanceDenied()
        if row is None:
            row = NativeMcpBoardConsent(connection_id=connection.id, project_id=project_id, scopes=scopes)
            db.insert(row)
        else:
            count = db.exec(
                update(NativeMcpBoardConsent)
                .where(NativeMcpBoardConsent.id == row.id, NativeMcpBoardConsent.revision == expected_revision)
                .values(scopes=list(scopes), revision=expected_revision + 1)
            )
            if count != 1:
                raise AppGovernanceConflict()
            row.scopes = list(scopes)
            row.revision = expected_revision + 1
        return _receipt(connection, project_id, row, available)


def effective_board_ids(actor, connection_id, scope):
    """Membership and roles are reread from the primary on every invocation."""
    with DbSession.use(readonly=False) as db:
        user = _current_user(db, actor)
        connection = db.exec(
            SqlBuilder.select.table(NativeMcpConnection).where(
                NativeMcpConnection.id == connection_id, NativeMcpConnection.user_id == user.id
            )
        ).first()
        if connection is None:
            raise AppGovernanceDenied()
        rows = db.exec(
            SqlBuilder.select.table(NativeMcpBoardConsent).where(NativeMcpBoardConsent.connection_id == connection.id)
        ).all()
        return frozenset(
            int(row.project_id)
            for row in rows
            if scope in row.scopes and scope in available_board_scopes(db, user, row.project_id)
        )
