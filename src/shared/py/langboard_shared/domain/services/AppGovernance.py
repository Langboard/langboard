"""Current app policy and connection ownership checks at native service boundaries."""

import hashlib
import json
import re
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.types import SafeDateTime, SnowflakeID
from ...publishers import AppSettingPublisher
from ..models import AppConnection, AppGovernancePolicy, Organization, Project, ProjectAssignedUser, ProjectRole, User
from ..models.ProjectRole import ProjectRoleAction


MODES = ("disabled", "approved_only", "personal_allowed")


def connection_discovery_scope(actor_id):
    """Scope joined connection discovery before paging; admin grants do not share personal accounts."""
    active_organization = (
        select(Organization.id)
        .where(
            Organization.id == AppConnection.organization_id,
            Organization.is_active.is_(True),
            Organization.suspended_at.is_(None),
        )
        .correlate(AppConnection)
        .exists()
    )
    return or_(
        and_(
            AppConnection.ownership == "personal",
            AppConnection.organization_id.is_(None),
            AppConnection.owner_id == actor_id,
        ),
        and_(
            AppConnection.ownership == "organization",
            AppConnection.organization_id == Project.organization_id,
            active_organization,
        ),
    )


class AppGovernanceDenied(Exception):
    pass


class AppGovernanceConflict(Exception):
    pass


def _current(db, model, identifier, *, for_update=True):
    statement = SqlBuilder.select.table(model).where(model.id == identifier)
    if for_update:
        statement = statement.with_for_update()
    # An enclosing atomic unit may already hold this identity before revocation.
    # Row locks serialize writes, but do not refresh SQLAlchemy's identity map.
    return db.exec(statement, execution_options={"populate_existing": True}).first()


def _actor(db, actor, *, for_update=True):
    current = _current(db, User, actor.id, for_update=for_update)
    if current is None or current.deleted_at or not current.activated_at:
        raise AppGovernanceDenied()
    return current


def _authorized_board(db, actor, project_id, *, for_update=True, action=ProjectRoleAction.Update):
    return _authorized_boards(db, actor, [project_id], for_update=for_update, action=action)[project_id]


def _authorized_boards(db, actor, project_ids, *, for_update=True, action=ProjectRoleAction.Update):
    """Apply the same current authority check to a bounded set without per-board queries."""
    project_ids = sorted(set(project_ids))
    if not project_ids:
        return {}

    def rows(model, statement):
        statement = statement.order_by(model.id)
        if for_update:
            statement = statement.with_for_update()
        return db.exec(statement, execution_options={"populate_existing": True}).all()

    projects = {p.id: p for p in rows(Project, SqlBuilder.select.table(Project).where(Project.id.in_(project_ids)))}
    if len(projects) != len(project_ids) or any(p.deleted_at for p in projects.values()):
        raise AppGovernanceDenied()
    required = [p.id for p in projects.values() if not actor.is_admin and actor.id != p.owner_id]
    if required:
        member_statement = SqlBuilder.select.table(ProjectAssignedUser).where(
            ProjectAssignedUser.project_id.in_(required), ProjectAssignedUser.user_id == actor.id
        )
        role_statement = SqlBuilder.select.table(ProjectRole).where(
            ProjectRole.project_id.in_(required), ProjectRole.user_id == actor.id
        )
        members = {m.project_id for m in rows(ProjectAssignedUser, member_statement)}
        role_rows = rows(ProjectRole, role_statement)
        roles = {r.project_id for r in role_rows}
        # No uniqueness constraint exists on roles: a later duplicate must not
        # overwrite a restrictive row and accidentally broaden authority.
        if any(not r.is_granted(action) for r in role_rows) or any(
            pid not in members or pid not in roles for pid in required
        ):
            raise AppGovernanceDenied()
    return projects


def _organization(db, organization_id, *, for_update=True):
    organization = _current(db, Organization, organization_id, for_update=for_update)
    if organization is None or not organization.is_active or organization.suspended_at:
        raise AppGovernanceDenied()
    return organization


def _authorize(db, actor, organization_id):
    actor = _actor(db, actor)
    if organization_id is None:
        if not actor.is_admin:
            raise AppGovernanceDenied()
    else:
        organization = _organization(db, organization_id)
        if not actor.is_admin and organization.owner_user_id != actor.id:
            raise AppGovernanceDenied()


def _key(organization_id):
    return "global" if organization_id is None else f"organization:{int(organization_id)}"


def _row(db, organization_id, *, for_update=True):
    statement = SqlBuilder.select.table(AppGovernancePolicy).where(
        AppGovernancePolicy.scope_key == _key(organization_id)
    )
    if for_update:
        statement = statement.with_for_update()
    # A composed operation may already retain this policy in its identity map.
    # Requerying the primary alone does not refresh a committed revocation.
    return db.exec(statement, execution_options={"populate_existing": True}).first()


def _revision(key, mode, generation, ceiling=None):
    value = json.dumps([key, mode, generation, ceiling], separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def current_policy(db, organization_id=None, *, for_update=False):
    """Read at most two indexed rows. Organization absence inherits instance policy."""
    global_row = _row(db, None, for_update=for_update)
    global_mode = global_row.mode if global_row else "approved_only"
    global_revision = _revision("global", global_mode, global_row.generation if global_row else 0)
    if organization_id is None:
        return {"mode": global_mode, "effective_mode": global_mode, "revision": global_revision}
    _organization(db, organization_id, for_update=for_update)
    row = _row(db, organization_id, for_update=for_update)
    mode = row.mode if row else None
    effective = min((global_mode, mode or global_mode), key=MODES.index)
    return {
        "mode": mode,
        "effective_mode": effective,
        "revision": _revision(_key(organization_id), mode, row.generation if row else 0, global_revision),
    }


def get_policy(actor, organization_id=None):
    with DbSession.atomic() as db:
        _authorize(db, actor, organization_id)
        return current_policy(db, organization_id, for_update=True)


def list_managed_organizations(actor, *, after_uid=None, limit=25):
    """Discover a bounded page of active policy scopes using current primary authority."""

    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Invalid organization page limit")
    after_id = None
    if after_uid is not None:
        if not isinstance(after_uid, str) or not re.fullmatch(r"[a-zA-Z0-9]{11}", after_uid):
            raise ValueError("Invalid organization cursor")
        after_id = SnowflakeID.from_short_code(after_uid)
        if not after_id or after_id.to_short_code() != after_uid:
            raise ValueError("Invalid organization cursor")
    with DbSession.atomic() as db:
        actor = _actor(db, actor)
        statement = SqlBuilder.select.table(Organization).where(
            Organization.is_active.is_(True),
            Organization.suspended_at.is_(None),
        )
        if not actor.is_admin:
            statement = statement.where(Organization.owner_user_id == actor.id)
        if after_id is not None:
            statement = statement.where(Organization.id > after_id)
        rows = db.exec(statement.order_by(Organization.id).limit(limit + 1)).all()
        page = rows[:limit]
        return {
            "items": [{"uid": row.get_uid(), "name": row.name} for row in page],
            "next_cursor": page[-1].get_uid() if len(rows) > limit else None,
        }


def lock_policy_mutation(db, *, exclusive=False):
    """Serialize policy-sensitive writes, including scopes with no policy row yet.

    PostgreSQL transaction lock is taken before actor/organization/definition
    locks. Runtime reads do not take it; it is released by commit or rollback.
    """
    if DbEngine.get_main_engine().dialect.name != "postgresql":
        return
    lock = func.pg_advisory_xact_lock if exclusive else func.pg_advisory_xact_lock_shared
    db.exec(select(lock(7104389251162)))


def save_policy(actor, mode, expected_revision, organization_id=None):
    if mode not in MODES and not (mode is None and organization_id is not None):
        raise ValueError("Invalid app governance mode")
    try:
        with DbSession.atomic() as db:
            lock_policy_mutation(db, exclusive=True)
            _authorize(db, actor, organization_id)
            policy = current_policy(db, organization_id, for_update=True)
            if expected_revision != policy["revision"]:
                raise AppGovernanceConflict()
            row = _row(db, organization_id)
            if row is None:
                db.insert(
                    AppGovernancePolicy(scope_key=_key(organization_id), organization_id=organization_id, mode=mode)
                )
            else:
                count = db.exec(
                    update(AppGovernancePolicy)
                    .where(AppGovernancePolicy.id == row.id, AppGovernancePolicy.generation == row.generation)
                    .values(mode=mode, generation=row.generation + 1, updated_at=SafeDateTime.now())
                )
                if count != 1:
                    raise AppGovernanceConflict()
            result = current_policy(db, organization_id, for_update=True)
            db.after_commit(AppSettingPublisher.apps_changed)
            return result
    except IntegrityError as exc:
        raise AppGovernanceConflict() from exc


def require_app_allowed(db, project, *, approved=True, personal=False):
    """Policy gate for callers that already enforce their own project authority."""
    current = _current(db, Project, project.id, for_update=False)
    if current is None or current.deleted_at:
        raise AppGovernanceDenied()
    return require_current_project_policy(db, current, approved=approved, personal=personal)


def require_current_project_policy(db, current, *, approved=True, personal=False):
    """Policy gate for a project freshly read from the primary in this transaction."""
    if current.deleted_at:
        raise AppGovernanceDenied()
    policy = current_policy(db, current.organization_id)
    mode = policy["effective_mode"]
    if mode == "disabled" or ((personal or not approved) and mode != "personal_allowed"):
        raise AppGovernanceDenied()
    return policy


def require_connection_access(
    db, actor, project, connection, *, unattended=False, approved=True, personal_app=False, for_update=True
):
    """Return only an authorized current connection; never expose another person's account."""
    actor = _actor(db, actor, for_update=for_update)
    project = _current(db, Project, project.id, for_update=for_update)
    connection = _current(db, AppConnection, connection.id, for_update=for_update)
    if project is None or project.deleted_at or connection is None or connection.state != "connected":
        raise AppGovernanceDenied()
    if project.owner_id != actor.id:
        member = db.exec(
            SqlBuilder.select.table(ProjectAssignedUser)
            .where(ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == actor.id)
            .limit(1)
        ).first()
        if member is None:
            raise AppGovernanceDenied()
    personal = connection.ownership == "personal"
    if personal:
        if connection.organization_id is not None or connection.owner_id != actor.id:
            raise AppGovernanceDenied()
        if unattended:
            shared = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser)
                .where(ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id != actor.id)
                .limit(1)
            ).first()
            if project.organization_id is not None or project.owner_id != actor.id or shared is not None:
                raise AppGovernanceDenied()
    elif connection.ownership == "organization":
        if project.organization_id is None or connection.organization_id != project.organization_id:
            raise AppGovernanceDenied()
    else:
        raise AppGovernanceDenied()
    require_current_project_policy(db, project, approved=approved, personal=personal_app)
    return connection
