"""Current, explicit profile authority. No delegated user privilege or private-card access.

Workers must call require_compactor_card in the transaction that commits an effect,
as well as before sending content to a model. Keep that transaction short: never
hold these locks while awaiting a provider.
"""

from ...core.db import DbSession, SqlBuilder
from ...helpers import InfraHelper
from ..models import Card, InternalBot, Project, User
from ..models.CompactorBoardGrant import CompactorBoardGrant
from ..models.InternalBot import InternalBotType
from .CardVisibilityPolicy import CardVisibility


class CompactorAuthorityError(Exception):
    def __init__(self, code="forbidden", status=403):
        self.code, self.status = code, status
        super().__init__(code)


def _current(db, model, identity, *, lock=False):
    try:
        identity = InfraHelper.convert_id(identity)
    except (ValueError, TypeError):
        raise CompactorAuthorityError("not_found", 404) from None
    query = SqlBuilder.select.table(model).where(model.id == identity).execution_options(populate_existing=True)
    # Project policy stays serialized without blocking a native card edit's FK KEY SHARE.
    return db.exec(query.with_for_update(key_share=model is Project) if lock else query).first()


def _manager_actor(db, actor):
    # Match personal collection writes: current actor before project policy guards.
    if not isinstance(actor, User):
        raise CompactorAuthorityError()
    actor = _current(db, User, actor.id, lock=True)
    if not actor or actor.deleted_at or not actor.activated_at:
        raise CompactorAuthorityError()
    return actor


def _require_manager(actor, project):
    if not actor.is_admin and actor.id != project.owner_id:
        raise CompactorAuthorityError()


def _project_profile(db, project_id, profile_id):
    # Same lock order for grants and effects serializes concurrent revocation.
    project = _current(db, Project, project_id, lock=True)
    profile = _current(db, InternalBot, profile_id, lock=True)
    if not project or project.deleted_at or not profile or profile.deleted_at:
        raise CompactorAuthorityError("not_found", 404)
    if profile.bot_type != InternalBotType.CommentCompaction:
        raise CompactorAuthorityError("invalid_profile", 400)
    return project, profile


def _grant(db, project, profile):
    return db.exec(SqlBuilder.select.table(CompactorBoardGrant).where(
        CompactorBoardGrant.project_id == project.id,
        CompactorBoardGrant.internal_bot_id == profile.id,
    ).execution_options(populate_existing=True).with_for_update()).first()


def _response(project, profile, grant):
    return {"project_uid": project.get_uid(), "profile_uid": profile.get_uid(),
            "can_read": bool(grant and grant.can_read),
            "can_compact": bool(grant and grant.can_compact),
            "can_archive": bool(grant and grant.can_archive)}


def get_compactor_board_grant(actor, project_id, profile_id):
    with DbSession.atomic() as db:
        actor = _manager_actor(db, actor)
        project, profile = _project_profile(db, project_id, profile_id)
        _require_manager(actor, project)
        return _response(project, profile, _grant(db, project, profile))


def set_compactor_board_grant(actor, project_id, profile_id, *, can_read, can_compact, can_archive):
    if any(type(value) is not bool for value in (can_read, can_compact, can_archive)):
        raise CompactorAuthorityError("invalid_permissions", 400)
    if not can_read and (can_compact or can_archive):
        raise CompactorAuthorityError("read_required", 400)
    with DbSession.atomic() as db:
        actor = _manager_actor(db, actor)
        project, profile = _project_profile(db, project_id, profile_id)
        _require_manager(actor, project)
        grant = _grant(db, project, profile)
        if grant is None:
            grant = CompactorBoardGrant(project_id=project.id, internal_bot_id=profile.id,
                                        granted_by_user_id=actor.id)
            insert = True
        else:
            insert = False
        grant.can_read, grant.can_compact, grant.can_archive = can_read, can_compact, can_archive
        grant.granted_by_user_id = actor.id
        db.insert(grant) if insert else db.update(grant)
        return _response(project, profile, grant)


def require_compactor_card(db, project_id, profile_id, card_id, *, action):
    if action not in {"read", "compact", "archive"}:
        raise CompactorAuthorityError("invalid_action", 400)
    project, profile = _project_profile(db, project_id, profile_id)
    grant = _grant(db, project, profile)
    if not grant or not grant.can_read or not getattr(grant, f"can_{action}"):
        raise CompactorAuthorityError()
    card = _current(db, Card, card_id, lock=True)
    if (not card or card.deleted_at or card.project_id != project.id or card.is_linked_resource
            or card.visibility not in {CardVisibility.Internal.value, CardVisibility.Shared.value}):
        raise CompactorAuthorityError("not_found", 404)
    return project, profile, card
