"""Current primary-DB audience facts, independent of app consent or credentials."""

from ...core.db import SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...Env import Env
from ..models import (
    EmployeeMembershipPolicy,
    IdentityProvider,
    Project,
    ProjectAssignedUser,
    ScimGroup,
    ScimGroupMember,
    User,
    UserIdentityLink,
)
from .CardVisibilityPolicy import CardVisibilityContext


def _internal_member(db, user):
    mode = Env.CARD_INTERNAL_ACCESS_MODE
    if mode == "project_members":
        return True
    if mode not in ("scim", "oidc_issuers"):
        return False
    policy = db.exec(
        SqlBuilder.select.table(EmployeeMembershipPolicy).where(EmployeeMembershipPolicy.key == "global")
    ).first()
    if mode == "oidc_issuers" and policy is None:
        link = db.exec(
            SqlBuilder.select.table(UserIdentityLink).where(
                UserIdentityLink.user_id == user.id, UserIdentityLink.provider == IdentityProvider.Oidc
            )
        ).first()
        return bool(link and link.external_id and (link.issuer or "").rstrip("/") in Env.CARD_INTERNAL_OIDC_ISSUERS)
    issuer = (Env.SCIM_ISSUER or "").strip().rstrip("/")
    if not issuer or (policy is None and not Env.MCP_EMPLOYEE_GROUP_IDS):
        return None
    if policy is not None and policy.issuer != issuer:
        return False
    link = db.exec(
        SqlBuilder.select.table(UserIdentityLink).where(
            UserIdentityLink.user_id == user.id, UserIdentityLink.provider == IdentityProvider.Scim
        )
    ).first()
    if link is None or (link.issuer or "").strip().rstrip("/") != issuer:
        return False
    groups = db.exec(
        SqlBuilder.select.table(ScimGroup)
        .join(ScimGroupMember, ScimGroupMember.group_id == ScimGroup.id)
        .where(ScimGroupMember.user_id == user.id, *ScimGroup.authority_filters(issuer))
    ).all()
    if policy is not None:
        return any(group.get_uid() in policy.group_uids for group in groups)
    return any(group.external_id in Env.MCP_EMPLOYEE_GROUP_IDS for group in groups)


def resolve_card_audience(db, project_id, actor_id, *, channel=CollaborationChannel.Api):
    """Caller supplies its current primary transaction; client input cannot select channel."""
    project = db.exec(SqlBuilder.select.table(Project).where(Project.id == project_id)).first()
    user = db.exec(SqlBuilder.select.table(User).where(User.id == actor_id)).first()
    active = bool(user and not user.deleted_at and user.activated_at)
    member = bool(
        active
        and project
        and not project.deleted_at
        and (
            project.owner_id == user.id
            or db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project_id, ProjectAssignedUser.user_id == user.id
                )
            ).first()
        )
    )
    return CardVisibilityContext(
        channel=channel,
        active=active,
        project_member=member,
        internal_member=_internal_member(db, user) if member else False,
        actor_user_id=int(user.id) if active else None,
    )
